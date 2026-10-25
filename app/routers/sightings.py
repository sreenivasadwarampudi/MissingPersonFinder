from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Sighting, User, utcnow
from ..schemas import SightingOut, sighting_out
from ..security import get_current_user, user_rate_limit
from ..services import jobs
from ..services.images import read_exif_gps, read_upload, sanitize_image
from ..services.records import audit, purge_sighting
from ..services.storage import get_storage

router = APIRouter(prefix="/api/sightings", tags=["sightings"])


def _own(db: Session, user: User, sighting_id: str) -> Sighting:
    s = db.get(Sighting, sighting_id)
    if not s or (s.uploader_id != user.id and user.role != "moderator"):
        raise HTTPException(404, "Report not found")
    return s


@router.post("", response_model=SightingOut, status_code=201)
def create_sighting(
    background: BackgroundTasks,
    photo: UploadFile = File(...),
    lat: float | None = Form(None, ge=-90, le=90),
    lng: float | None = Form(None, ge=-180, le=180),
    accuracy_m: float | None = Form(None, ge=0),
    location_source: str | None = Form(None),
    note: str | None = Form(None, max_length=500),
    consent: bool = Form(False),
    user: User = Depends(user_rate_limit("sighting-create", 10, 3600)),
    db: Session = Depends(get_db),
):
    if not consent:
        raise HTTPException(400, "Please confirm you took or are allowed to share this photo and accept the privacy policy.")
    if (lat is None) != (lng is None):
        raise HTTPException(400, "Send both lat and lng, or neither.")

    raw = read_upload(photo)
    exif_gps = read_exif_gps(raw)  # read BEFORE sanitising strips the metadata
    jpeg = sanitize_image(raw)

    source = "none"
    if lat is not None:
        source = "browser" if location_source == "browser" else "map"
    elif exif_gps:
        lat, lng = exif_gps  # fallback: location embedded in the photo itself
        source = "exif"

    s = Sighting(
        uploader_id=user.id,
        status="processing",
        lat=lat,
        lng=lng,
        accuracy_m=accuracy_m if source in ("browser", "map") else None,
        location_source=source,
        note=note,
        consent_at=utcnow(),
    )
    db.add(s)
    db.flush()
    s.original_key = f"sighting/{s.id}/original.jpg"
    get_storage().put(s.original_key, jpeg)
    audit(db, user.id, "sighting.create", target=s.id)
    db.commit()
    background.add_task(jobs.process_sighting, s.id)
    return sighting_out(s)


@router.get("/mine", response_model=list[SightingOut])
def my_sightings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Sighting).filter(Sighting.uploader_id == user.id).order_by(Sighting.created_at.desc()).all()
    return [sighting_out(s) for s in rows]


@router.get("/{sighting_id}", response_model=SightingOut)
def get_sighting(sighting_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return sighting_out(_own(db, user, sighting_id))


@router.delete("/{sighting_id}", status_code=204)
def delete_sighting(sighting_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = _own(db, user, sighting_id)
    audit(db, user.id, "sighting.delete", target=s.id)
    purge_sighting(db, s)
    db.commit()
