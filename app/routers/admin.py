"""Moderator tools: approve records, review AI-suggested matches, handle reports. Every action is audit-logged."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import AuditLog, Match, MissingPerson, Report, Sighting, User, utcnow
from ..schemas import missing_out
from ..security import require_moderator
from ..services import matching, notify
from ..services.records import audit, purge_missing, purge_sighting

router = APIRouter(prefix="/api/admin", tags=["admin"])
settings = get_settings()


@router.get("/stats")
def stats(_: User = Depends(require_moderator), db: Session = Depends(get_db)):
    def count(model, *conds):
        return db.query(func.count(model.id)).filter(*conds).scalar()

    return {
        "records_pending": count(MissingPerson, MissingPerson.status == "pending_review"),
        "records_active": count(MissingPerson, MissingPerson.status == "active"),
        "sightings_active": count(Sighting, Sighting.status == "active"),
        "matches_pending": count(Match, Match.status == "pending"),
        "matches_confirmed": count(Match, Match.status == "confirmed"),
        "reports_open": count(Report, Report.status == "open"),
    }


# ------------------------------------------------------------ missing-person records
@router.get("/missing")
def list_missing(status: str = "pending_review", _: User = Depends(require_moderator), db: Session = Depends(get_db)):
    rows = db.query(MissingPerson).filter(MissingPerson.status == status).order_by(MissingPerson.created_at).limit(200).all()
    out = []
    for m in rows:
        owner = db.get(User, m.owner_id)
        out.append({**missing_out(m).model_dump(mode="json"), "owner_email": owner.email if owner else None, "face_count": m.face_count, "embedding_source": m.embedding_source})
    return out


class RecordDecision(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    note: str | None = Field(default=None, max_length=500)


@router.post("/missing/{missing_id}/decision")
def decide_missing(
    missing_id: str,
    body: RecordDecision,
    background: BackgroundTasks,
    mod: User = Depends(require_moderator),
    db: Session = Depends(get_db),
):
    m = db.get(MissingPerson, missing_id)
    if not m or m.status != "pending_review":
        raise HTTPException(404, "Record not awaiting review")
    m.reviewed_by = mod.id
    m.review_note = body.note
    created = 0
    if body.decision == "approve":
        m.status = "active"
        db.commit()
        created = len(matching.match_missing(db, m))  # compare against sightings already on file
        if created:
            background.add_task(notify.notify_moderators, f"{created} possible match(es) awaiting review (record {m.id[:8]}).")
    else:
        m.status = "rejected"
    audit(db, mod.id, f"missing.{body.decision}", target=m.id, detail=body.note)
    db.commit()
    return {"status": m.status, "new_matches": created}


# ------------------------------------------------------------ matches
def _match_view(db: Session, match: Match) -> dict:
    m = db.get(MissingPerson, match.missing_id)
    s = db.get(Sighting, match.sighting_id)
    return {
        "id": match.id,
        "score": round(match.score, 3),
        "status": match.status,
        "created_at": match.created_at,
        "moderator_note": match.moderator_note,
        "routed_to": match.routed_to,
        "family_feedback": match.family_feedback,
        "missing": {
            "id": m.id,
            "full_name": m.full_name,
            "age": m.age,
            "gender": m.gender,
            "description": m.description,
            "last_seen_place": m.last_seen_place,
            "last_seen_date": m.last_seen_date,
            "has_restored": bool(m.restored_key),
            "embedding_source": m.embedding_source,
        },
        "sighting": {
            "id": s.id,
            "lat": s.lat,
            "lng": s.lng,
            "accuracy_m": s.accuracy_m,
            "address": s.address,
            "area_label": s.area_label,
            "location_source": s.location_source,
            "note": s.note,
            "seen_at": s.created_at,
            "has_restored": bool(s.restored_key),
            "restore_method": s.restore_method,
            "face_count": s.face_count,
            "embedding_source": s.embedding_source,
        },
    }


@router.get("/matches")
def list_matches(status: str = "pending", _: User = Depends(require_moderator), db: Session = Depends(get_db)):
    rows = db.query(Match).filter(Match.status == status).order_by(Match.score.desc()).limit(100).all()
    return [_match_view(db, m) for m in rows]


class MatchDecision(BaseModel):
    decision: str = Field(pattern=r"^(confirm|reject)$")
    note: str | None = Field(default=None, max_length=1000)
    routed_to: str | None = Field(default=None, max_length=200)


@router.post("/matches/{match_id}/decision")
def decide_match(
    match_id: str,
    body: MatchDecision,
    background: BackgroundTasks,
    mod: User = Depends(require_moderator),
    db: Session = Depends(get_db),
):
    match = db.get(Match, match_id)
    if not match or match.status != "pending":
        raise HTTPException(404, "Match not awaiting review")
    match.reviewed_by = mod.id
    match.reviewed_at = utcnow()
    match.moderator_note = body.note
    if body.decision == "confirm":
        match.status = "confirmed"
        match.routed_to = body.routed_to or settings.ngo_name or None
        owner = db.get(User, db.get(MissingPerson, match.missing_id).owner_id)
        background.add_task(_email_family, owner.email, match.routed_to)
    else:
        match.status = "rejected"
    audit(db, mod.id, f"match.{body.decision}", target=match.id, detail=f"score={match.score:.3f}")
    db.commit()
    return {"status": match.status}


def _email_family(email: str, routed_to: str | None) -> None:
    routing = f"\nWe have shared this with: {routed_to}." if routed_to else ""
    contact = f"\nQuestions: {settings.ngo_contact}" if settings.ngo_contact else ""
    notify.send_email(
        email,
        f"{settings.app_name}: a possible match was confirmed by our team",
        "One of your records has a possible match that our review team confirmed.\n"
        "For privacy, details are only shown after you sign in.\n\n"
        "Please do not go to the location alone and do not confront anyone. "
        "Contact your local police station or the NGO/helpline you are working with, and share what you see in the app."
        f"{routing}{contact}\n",
    )


# ------------------------------------------------------------ reports / takedowns
@router.get("/reports")
def list_reports(status: str = "open", _: User = Depends(require_moderator), db: Session = Depends(get_db)):
    rows = db.query(Report).filter(Report.status == status).order_by(Report.created_at).limit(200).all()
    return [
        {
            "id": r.id,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "reason": r.reason,
            "contact_email": r.contact_email,
            "status": r.status,
            "created_at": r.created_at,
            "resolution_note": r.resolution_note,
        }
        for r in rows
    ]


class ReportResolution(BaseModel):
    action: str = Field(pattern=r"^(takedown|dismiss)$")
    note: str | None = Field(default=None, max_length=500)


@router.post("/reports/{report_id}/resolve")
def resolve_report(report_id: str, body: ReportResolution, mod: User = Depends(require_moderator), db: Session = Depends(get_db)):
    r = db.get(Report, report_id)
    if not r or r.status != "open":
        raise HTTPException(404, "Report not open")
    if body.action == "takedown":
        if r.target_type == "missing" and r.target_id and (m := db.get(MissingPerson, r.target_id)):
            purge_missing(db, m)
        elif r.target_type == "sighting" and r.target_id and (s := db.get(Sighting, r.target_id)):
            purge_sighting(db, s)
        r.status = "actioned"
    else:
        r.status = "dismissed"
    r.resolved_by = mod.id
    r.resolution_note = body.note
    audit(db, mod.id, f"report.{body.action}", target=r.target_id or r.id, detail=body.note)
    db.commit()
    return {"status": r.status}


@router.get("/audit")
def audit_log(limit: int = 100, _: User = Depends(require_moderator), db: Session = Depends(get_db)):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 500)).all()
    return [{"id": a.id, "actor_id": a.actor_id, "action": a.action, "target": a.target, "detail": a.detail, "created_at": a.created_at} for a in rows]
