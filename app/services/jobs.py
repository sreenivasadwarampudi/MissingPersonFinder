"""Background jobs: run after the upload response so the browser never waits on AI work."""
import logging

from ..db import SessionLocal
from ..models import MissingPerson, Sighting
from . import matching, notify, pipeline
from .geo import reverse_geocode
from .storage import get_storage

log = logging.getLogger(__name__)


def process_missing(missing_id: str) -> None:
    with SessionLocal() as db:
        m = db.get(MissingPerson, missing_id)
        if not m or m.status != "processing":
            return
        try:
            storage = get_storage()
            res = pipeline.process(storage.get(m.original_key))
            if res.restored:
                m.restored_key = f"missing/{m.id}/restored.jpg"
                storage.put(m.restored_key, res.restored)
            m.restore_method = res.restore_method
            m.embedding = res.embedding
            m.embedding_source = res.embedding_source
            m.det_score = res.det_score
            m.face_count = res.face_count
            m.status = "pending_review" if res.embedding else "no_face"
            db.commit()
            if m.status == "pending_review":
                notify.notify_moderators(f"New missing-person record awaiting review (ref {m.id[:8]}).")
        except Exception:
            log.exception("process_missing failed for %s", missing_id)
            db.rollback()
            m = db.get(MissingPerson, missing_id)
            if m:
                m.status = "failed"
                db.commit()


def process_sighting(sighting_id: str) -> None:
    with SessionLocal() as db:
        s = db.get(Sighting, sighting_id)
        if not s or s.status != "processing":
            return
        try:
            storage = get_storage()
            res = pipeline.process(storage.get(s.original_key))
            if res.restored:
                s.restored_key = f"sighting/{s.id}/restored.jpg"
                storage.put(s.restored_key, res.restored)
            s.restore_method = res.restore_method
            s.embedding = res.embedding
            s.embedding_source = res.embedding_source
            s.det_score = res.det_score
            s.face_count = res.face_count

            if s.lat is not None and s.lng is not None:
                try:
                    geo = reverse_geocode(s.lat, s.lng)
                    if geo:
                        s.address = geo["address"]
                        s.area_label = geo["area_label"]
                except Exception:
                    log.exception("geocoding failed for sighting %s", sighting_id)

            s.status = "active" if res.embedding else "no_face"
            db.commit()

            if s.status == "active":
                new = matching.match_sighting(db, s)
                if new:
                    notify.notify_moderators(f"{len(new)} possible match(es) awaiting review (sighting {s.id[:8]}).")
        except Exception:
            log.exception("process_sighting failed for %s", sighting_id)
            db.rollback()
            s = db.get(Sighting, sighting_id)
            if s:
                s.status = "failed"
                db.commit()
