"""Private file storage. Files are never served directly: only through /api/files with permission checks."""
import logging
from pathlib import Path

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


class LocalStorage:
    def __init__(self) -> None:
        self.base = Path(settings.local_storage_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.base / key).resolve()
        if self.base not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class SupabaseStorage:
    """Supabase Storage via REST with the service key. Use a PRIVATE bucket."""

    def __init__(self) -> None:
        if not (settings.supabase_url and settings.supabase_service_key):
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY are required for STORAGE_BACKEND=supabase")
        self.base = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{settings.supabase_bucket}"
        self.headers = {
            "Authorization": f"Bearer {settings.supabase_service_key}",
            "apikey": settings.supabase_service_key,
        }

    def put(self, key: str, data: bytes) -> None:
        r = httpx.post(
            f"{self.base}/{key}",
            content=data,
            headers={**self.headers, "Content-Type": "image/jpeg", "x-upsert": "true"},
            timeout=30,
        )
        r.raise_for_status()

    def get(self, key: str) -> bytes:
        r = httpx.get(f"{self.base}/{key}", headers=self.headers, timeout=30)
        r.raise_for_status()
        return r.content

    def delete(self, key: str) -> None:
        httpx.delete(f"{self.base}/{key}", headers=self.headers, timeout=30)


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        _storage = SupabaseStorage() if settings.storage_backend == "supabase" else LocalStorage()
    return _storage
