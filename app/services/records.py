"""Hard deletion (user erasure requests, moderator takedowns) and audit logging."""
import logging

from sqlalchemy.orm import Session

from ..models import AuditLog, Match, MissingPerson, Sighting, User
from .storage import get_storage

log = logging.getLogger(__name__)


def audit(db: Session, actor_id: str | None, action: str, target: str | None = None, detail: str | None = None) -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, target=target, detail=detail))


def _delete_files(*keys: str | None) -> None:
    storage = get_storage()
    for key in keys:
        if key:
            try:
                storage.delete(key)
            except Exception:
                log.exception("Could not delete stored file %s", key)


def purge_missing(db: Session, m: MissingPerson) -> None:
    _delete_files(m.original_key, m.restored_key)
    db.query(Match).filter(Match.missing_id == m.id).delete()
    db.delete(m)


def purge_sighting(db: Session, s: Sighting) -> None:
    _delete_files(s.original_key, s.restored_key)
    db.query(Match).filter(Match.sighting_id == s.id).delete()
    db.delete(s)


def purge_user(db: Session, user: User) -> None:
    for m in db.query(MissingPerson).filter(MissingPerson.owner_id == user.id).all():
        purge_missing(db, m)
    for s in db.query(Sighting).filter(Sighting.uploader_id == user.id).all():
        purge_sighting(db, s)
    db.delete(user)
