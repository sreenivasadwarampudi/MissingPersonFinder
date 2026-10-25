"""Public abuse/takedown reports. Open to anyone (a person photographed without consent may have no account)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Report
from ..security import rate_limit
from ..services import notify

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ReportIn(BaseModel):
    target_type: str = Field(pattern=r"^(missing|sighting|other)$")
    target_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=10, max_length=2000)
    contact_email: EmailStr | None = None


@router.post("", status_code=202, dependencies=[Depends(rate_limit("report", 10, 3600))])
def create_report(body: ReportIn, db: Session = Depends(get_db)):
    r = Report(
        target_type=body.target_type,
        target_id=body.target_id,
        reason=body.reason,
        contact_email=str(body.contact_email) if body.contact_email else None,
    )
    db.add(r)
    db.commit()
    notify.notify_moderators(f"New abuse/takedown report {r.id[:8]} awaiting review.")
    # identical answer whether or not the referenced record exists
    return {"received": True}
