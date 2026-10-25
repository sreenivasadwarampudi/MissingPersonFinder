import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User

settings = get_settings()
bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------- OTP + JWT
def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(email: str, code: str) -> str:
    return hmac.new(settings.secret_key.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def create_access_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": user.id, "exp": exp}, settings.secret_key, algorithm="HS256")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not creds:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired, please sign in again")
    user = db.get(User, payload.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(401, "Account not found or disabled")
    return user  # role always comes from the database, never from the token


def require_moderator(user: User = Depends(get_current_user)) -> User:
    if user.role != "moderator":
        raise HTTPException(403, "Moderator access required")
    return user


# ---------------------------------------------------------------- rate limiting
def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            # last entry = the one appended by our (single) trusted proxy; earlier ones are client-controlled
            return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """In-memory sliding window. Fine for one instance (free-tier prototype); use Redis to scale out."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(self, key: str, limit: int, window: int) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 20_000:  # opportunistic cleanup
                for k in [k for k, q in self._hits.items() if not q or now - q[-1] > window]:
                    del self._hits[k]
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                retry = int(window - (now - q[0])) + 1
                raise HTTPException(
                    429,
                    "Too many requests. Please slow down and try again later.",
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)


limiter = RateLimiter()


def rate_limit(name: str, limit: int, window_seconds: int):
    """Per-IP limit, for endpoints that do not require sign-in."""

    def dep(request: Request) -> None:
        limiter.check(f"{name}:ip:{client_ip(request)}", limit, window_seconds)

    return dep


def user_rate_limit(name: str, limit: int, window_seconds: int):
    """Per-user limit; also returns the authenticated user."""

    def dep(user: User = Depends(get_current_user)) -> User:
        limiter.check(f"{name}:user:{user.id}", limit, window_seconds)
        return user

    return dep
