"""Authenticated image access. Storage is private; this is the only way a photo leaves the server."""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Match, MissingPerson, Sighting, User
from ..security import get_current_user, user_rate_limit
from ..services.records import audit
from ..services.storage import get_storage

router = APIRouter(prefix="/api/files", tags=["files"])


def _can_see_sighting(db: Session, user: User, s: Sighting) -> bool:
    if user.role == "moderator" or s.uploader_id == user.id:
        return True
    # the family of a CONFIRMED match may see the sighting photo
    return (
        db.query(Match.id)
        .join(MissingPerson, Match.missing_id == MissingPerson.id)
        .filter(Match.sighting_id == s.id, Match.status == "confirmed", MissingPerson.owner_id == user.id)
        .first()
        is not None
    )


@router.get("/{kind}/{item_id}/{variant}")
def get_file(
    kind: str,
    item_id: str,
    variant: str,
    user: User = Depends(user_rate_limit("files", 300, 60)),
    db: Session = Depends(get_db),
):
    if variant not in ("original", "restored") or kind not in ("missing", "sighting"):
        raise HTTPException(404, "Not found")

    if kind == "missing":
        obj = db.get(MissingPerson, item_id)
        allowed = obj is not None and (obj.owner_id == user.id or user.role == "moderator")
    else:
        obj = db.get(Sighting, item_id)
        allowed = obj is not None and _can_see_sighting(db, user, obj)
    if not allowed:
        raise HTTPException(404, "Not found")  # same answer for "missing" and "forbidden": no probing

    key = obj.original_key if variant == "original" else obj.restored_key
    if not key:
        raise HTTPException(404, "Not found")

    if kind == "sighting" and obj.uploader_id != user.id:
        audit(db, user.id, "sighting.photo.view", target=obj.id)
        db.commit()
    try:
        data = get_storage().get(key)
    except Exception:
        raise HTTPException(404, "Not found")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
