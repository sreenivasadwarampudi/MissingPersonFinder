"""The family's view of matches. Nothing sensitive is returned until a moderator has CONFIRMED the match."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Match, MissingPerson, Sighting, User
from ..security import get_current_user, user_rate_limit
from ..services.records import audit

router = APIRouter(prefix="/api/matches", tags=["matches"])


@router.get("/mine")
def my_matches(user: User = Depends(user_rate_limit("matches-read", 60, 60)), db: Session = Depends(get_db)):
    rows = (
        db.query(Match, MissingPerson)
        .join(MissingPerson, Match.missing_id == MissingPerson.id)
        .filter(MissingPerson.owner_id == user.id, Match.status.in_(["pending", "confirmed"]))
        .order_by(Match.created_at.desc())
        .all()
    )
    out = []
    for match, person in rows:
        item = {
            "id": match.id,
            "missing_id": person.id,
            "missing_name": person.full_name,
            "status": match.status,
            "created_at": match.created_at,
        }
        if match.status == "confirmed":
            s = db.get(Sighting, match.sighting_id)
            item.update(
                score=round(match.score, 3),
                moderator_note=match.moderator_note,
                routed_to=match.routed_to,
                family_feedback=match.family_feedback,
                sighting={
                    "id": s.id,
                    "lat": s.lat,
                    "lng": s.lng,
                    "address": s.address,
                    "area_label": s.area_label,
                    "location_source": s.location_source,
                    "seen_at": s.created_at,
                    "note": s.note,
                    "has_restored": bool(s.restored_key),
                }
                if s
                else None,
            )
        out.append(item)  # pending matches expose only "a possible match is under review"
    return out


class Feedback(BaseModel):
    outcome: str  # reunited | not_them


@router.post("/{match_id}/feedback")
def feedback(match_id: str, body: Feedback, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.outcome not in ("reunited", "not_them"):
        raise HTTPException(400, "outcome must be 'reunited' or 'not_them'")
    row = (
        db.query(Match, MissingPerson)
        .join(MissingPerson, Match.missing_id == MissingPerson.id)
        .filter(Match.id == match_id, MissingPerson.owner_id == user.id, Match.status == "confirmed")
        .first()
    )
    if not row:
        raise HTTPException(404, "Match not found")
    match, person = row
    match.family_feedback = body.outcome
    if body.outcome == "reunited":
        person.status = "found"
    audit(db, user.id, f"match.feedback.{body.outcome}", target=match.id)
    db.commit()
    return {"ok": True}
