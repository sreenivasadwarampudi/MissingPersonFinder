"""Response shapes. Each is explicit so sensitive fields (embeddings, exact locations) cannot leak by accident."""
from datetime import date, datetime

from pydantic import BaseModel

from .models import MissingPerson, Sighting


class MissingOut(BaseModel):
    id: str
    full_name: str
    age: int | None
    gender: str | None
    description: str | None
    last_seen_place: str | None
    last_seen_date: date | None
    contact_phone: str | None
    status: str
    has_restored: bool
    restore_method: str | None
    review_note: str | None
    created_at: datetime


def missing_out(m: MissingPerson) -> MissingOut:
    return MissingOut(
        id=m.id,
        full_name=m.full_name,
        age=m.age,
        gender=m.gender,
        description=m.description,
        last_seen_place=m.last_seen_place,
        last_seen_date=m.last_seen_date,
        contact_phone=m.contact_phone,
        status=m.status,
        has_restored=bool(m.restored_key),
        restore_method=m.restore_method,
        review_note=m.review_note,
        created_at=m.created_at,
    )


class SightingOut(BaseModel):
    """What the uploader sees about their own report. Deliberately says nothing about any match."""

    id: str
    status: str
    created_at: datetime
    location_source: str
    area_label: str | None
    note: str | None
    has_restored: bool
    restore_method: str | None


def sighting_out(s: Sighting) -> SightingOut:
    return SightingOut(
        id=s.id,
        status=s.status,
        created_at=s.created_at,
        location_source=s.location_source,
        area_label=s.area_label,
        note=s.note,
        has_restored=bool(s.restored_key),
        restore_method=s.restore_method,
    )
