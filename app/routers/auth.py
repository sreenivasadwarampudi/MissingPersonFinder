import hmac
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import OtpCode, User, as_utc, utcnow
from ..security import (
    create_access_token,
    generate_otp,
    get_current_user,
    hash_otp,
    rate_limit,
)
from ..services.notify import send_email
from ..services.records import audit, purge_user

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


class OtpRequest(BaseModel):
    email: EmailStr


class OtpVerify(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    accept_privacy: bool = False


def user_json(u: User) -> dict:
    return {"id": u.id, "email": u.email, "role": u.role}


@router.post("/request-otp", dependencies=[Depends(rate_limit("otp-request", 10, 3600))])
def request_otp(body: OtpRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    email = body.email.lower()
    now = utcnow()
    recent = (
        db.query(OtpCode)
        .filter(OtpCode.email == email, OtpCode.created_at > now - timedelta(hours=1))
        .order_by(OtpCode.created_at.desc())
        .all()
    )
    if len(recent) >= 5:
        raise HTTPException(429, "Too many codes requested for this email. Try again in an hour.")
    if recent and (now - as_utc(recent[0].created_at)).total_seconds() < 60:
        raise HTTPException(429, "Please wait a minute before requesting another code.")

    code = generate_otp()
    db.add(OtpCode(email=email, code_hash=hash_otp(email, code), expires_at=now + timedelta(minutes=settings.otp_ttl_minutes)))
    db.commit()
    background.add_task(
        send_email,
        email,
        f"Your {settings.app_name} sign-in code",
        f"Your sign-in code is {code}.\nIt expires in {settings.otp_ttl_minutes} minutes.\n"
        "If you did not ask for it, you can ignore this email.",
    )
    response = {"sent": True}
    if not settings.is_prod and not settings.smtp_host:
        response["dev_otp"] = code  # dev convenience only; never present in prod
    return response


@router.post("/verify-otp", dependencies=[Depends(rate_limit("otp-verify", 30, 3600))])
def verify_otp(body: OtpVerify, db: Session = Depends(get_db)):
    email = body.email.lower()
    now = utcnow()
    otp = (
        db.query(OtpCode)
        .filter(OtpCode.email == email, OtpCode.used.is_(False), OtpCode.expires_at > now)
        .order_by(OtpCode.created_at.desc())
        .first()
    )
    if not otp or otp.attempts >= settings.otp_max_attempts:
        raise HTTPException(400, "Invalid or expired code")
    if not hmac.compare_digest(otp.code_hash, hash_otp(email, body.code)):
        otp.attempts += 1
        db.commit()
        raise HTTPException(400, "Invalid or expired code")

    user = db.query(User).filter(User.email == email).first()
    if (user is None or user.consented_at is None) and not body.accept_privacy:
        raise HTTPException(409, "privacy_consent_required")  # code stays valid so the user can tick the box

    otp.used = True
    if user is None:
        user = User(email=email, consented_at=now)
        db.add(user)
    elif user.consented_at is None:
        user.consented_at = now
    user.role = "moderator" if email in settings.moderators else "user"  # env var is the source of truth
    user.last_login_at = now
    db.commit()
    return {"token": create_access_token(user), "user": user_json(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_json(user)


@router.delete("/me", status_code=204)
def delete_account(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Right to erasure: removes the account and everything it uploaded (records, photos, matches)."""
    audit(db, None, "account.delete", target=user.id)
    purge_user(db, user)
    db.commit()
