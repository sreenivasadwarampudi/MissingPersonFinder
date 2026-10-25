import uuid
from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .db import Base

EMBEDDING_DIM = 512


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class EmbeddingType(TypeDecorator):
    """pgvector `vector(512)` on Postgres, plain JSON list everywhere else (SQLite dev mode)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(EMBEDDING_DIM))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        return None if value is None else [float(x) for x in value]

    def process_result_value(self, value, dialect):
        return None if value is None else [float(x) for x in value]


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | moderator
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MissingPerson(Base):
    """A record a family submits for a relative they are searching for."""

    __tablename__ = "missing_persons"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_place: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_seen_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # processing | no_face | failed | pending_review | active | rejected | found | closed
    status: Mapped[str] = mapped_column(String(20), default="processing", index=True)
    original_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    restored_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    restore_method: Mapped[str | None] = mapped_column(String(60), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType, nullable=True)
    embedding_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    det_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    face_count: Mapped[int] = mapped_column(Integer, default=0)

    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Sighting(Base):
    """A photo + location submitted by someone who saw a person who may be missing."""

    __tablename__ = "sightings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    uploader_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="processing", index=True)  # processing | no_face | failed | active
    original_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    restored_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    restore_method: Mapped[str | None] = mapped_column(String(60), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType, nullable=True)
    embedding_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    det_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    face_count: Mapped[int] = mapped_column(Integer, default=0)

    # exact location: PRIVATE. Only moderators and the family of a *confirmed* match can ever see it.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_source: Mapped[str] = mapped_column(String(10), default="none")  # browser | map | exif | none
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    area_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("missing_id", "sighting_id", name="uq_match_pair"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    missing_id: Mapped[str] = mapped_column(ForeignKey("missing_persons.id"), index=True)
    sighting_id: Mapped[str] = mapped_column(ForeignKey("sightings.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | confirmed | rejected
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    moderator_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    routed_to: Mapped[str | None] = mapped_column(String(200), nullable=True)  # police station / NGO handling it
    family_feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)  # reunited | not_them
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    target_type: Mapped[str] = mapped_column(String(20))  # missing | sighting | other
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open | actioned | dismissed
    resolved_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
