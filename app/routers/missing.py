from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import MissingPerson, User, utcnow
from ..schemas import MissingOut, missing_out
from ..security import get_current_user, user_rate_limit
from ..services import jobs
from ..services.images import read_upload, sanitize_image
from ..services.records import audit, purge_missing
from ..services.storage import get_storage

router = APIRouter(prefix="/api/missing", tags=["missing"])

MAX_ACTIVE_RECORDS_PER_USER = 10


def _owned(db: Session, user: User, missing_id: str) -> MissingPerson:
    m = db.get(MissingPerson, missing_id)
    if not m or (m.owner_id != user.id and user.role != "moderator"):
        raise HTTPException(404, "Record not found")
    return m


@router.post("", response_model=MissingOut, status_code=201)
def create_missing(
    background: BackgroundTasks,
    photo: UploadFile = File(...),
    full_name: str = Form(..., min_length=1, max_length=120),
    age: int | None = Form(None, ge=0, le=120),
    gender: str | None = Form(None, max_length=20),
    description: str | None = Form(None, max_length=1000),
    last_seen_place: str | None = Form(None, max_length=200),
    last_seen_date: date | None = Form(None),
    contact_phone: str | None = Form(None, max_length=20),
    consent: bool = Form(False),
    user: User = Depends(user_rate_limit("missing-create", 5, 86400)),
    db: Session = Depends(get_db),
):
    if not consent:
        raise HTTPException(400, "You must confirm you are authorised to search for this person and accept the privacy policy.")
    count = (
        db.query(MissingPerson)
        .filter(MissingPerson.owner_id == user.id, MissingPerson.status.notin_(["rejected", "closed", "found"]))
        .count()
    )
    if count >= MAX_ACTIVE_RECORDS_PER_USER:
        raise HTTPException(400, "You have reached the limit of open records. Close an old one first.")

    jpeg = sanitize_image(read_upload(photo))
    m = MissingPerson(
        owner_id=user.id,
        full_name=full_name.strip(),
        age=age,
        gender=gender,
        description=description,
        last_seen_place=last_seen_place,
        last_seen_date=last_seen_date,
        contact_phone=contact_phone,
        status="processing",
        consent_at=utcnow(),
    )
    db.add(m)
    db.flush()
    m.original_key = f"missing/{m.id}/original.jpg"
    get_storage().put(m.original_key, jpeg)
    audit(db, user.id, "missing.create", target=m.id)
    db.commit()
    background.add_task(jobs.process_missing, m.id)
    return missing_out(m)


@router.get("/mine", response_model=list[MissingOut])
def my_records(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(MissingPerson).filter(MissingPerson.owner_id == user.id).order_by(MissingPerson.created_at.desc()).all()
    return [missing_out(m) for m in rows]


@router.get("/{missing_id}", response_model=MissingOut)
def get_record(missing_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return missing_out(_owned(db, user, missing_id))


class CloseBody(BaseModel):
    outcome: str = "closed"  # found | closed


@router.post("/{missing_id}/close", response_model=MissingOut)
def close_record(missing_id: str, body: CloseBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _owned(db, user, missing_id)
    if body.outcome not in ("found", "closed"):
        raise HTTPException(400, "outcome must be 'found' or 'closed'")
    m.status = body.outcome
    audit(db, user.id, f"missing.{body.outcome}", target=m.id)
    db.commit()
    return missing_out(m)


@router.delete("/{missing_id}", status_code=204)
def delete_record(missing_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _owned(db, user, missing_id)
    audit(db, user.id, "missing.delete", target=m.id)
    purge_missing(db, m)
    db.commit()
