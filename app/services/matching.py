"""Candidate generation. The AI only *suggests* pairs; a human moderator decides every match."""
import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Match, MissingPerson, Sighting

settings = get_settings()


def _literal(vec) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def _rank(db: Session, model, query_vec, status: str) -> list[tuple[str, float]]:
    """Top-k most similar rows of `model` (by cosine similarity) at or above the threshold."""
    if db.get_bind().dialect.name == "postgresql":
        sql = text(
            f"SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS sim FROM {model.__tablename__} "
            "WHERE embedding IS NOT NULL AND status = :status "
            "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"
        )
        rows = db.execute(sql, {"q": _literal(query_vec), "status": status, "k": settings.match_top_k}).all()
        hits = [(r.id, float(r.sim)) for r in rows]
    else:  # SQLite dev mode: brute force in numpy
        q = np.asarray(query_vec, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        rows = db.query(model.id, model.embedding).filter(model.embedding.isnot(None), model.status == status).all()
        scored = []
        for row_id, emb in rows:
            v = np.asarray(emb, dtype=np.float32)
            scored.append((row_id, float(q @ v / (np.linalg.norm(v) or 1.0))))
        hits = sorted(scored, key=lambda t: -t[1])[: settings.match_top_k]
    return [(i, s) for i, s in hits if s >= settings.effective_threshold]


def _exists(db: Session, missing_id: str, sighting_id: str) -> bool:
    return (
        db.query(Match.id).filter(Match.missing_id == missing_id, Match.sighting_id == sighting_id).first() is not None
    )


def match_sighting(db: Session, sighting: Sighting) -> list[Match]:
    """A new sighting arrived: compare it with every ACTIVE missing-person record."""
    if sighting.embedding is None:
        return []
    created = []
    for missing_id, score in _rank(db, MissingPerson, sighting.embedding, "active"):
        if not _exists(db, missing_id, sighting.id):
            m = Match(missing_id=missing_id, sighting_id=sighting.id, score=score)
            db.add(m)
            created.append(m)
    db.commit()
    return created


def match_missing(db: Session, missing: MissingPerson) -> list[Match]:
    """A record was just approved: compare it with every stored sighting."""
    if missing.embedding is None:
        return []
    created = []
    for sighting_id, score in _rank(db, Sighting, missing.embedding, "active"):
        if not _exists(db, missing.id, sighting_id):
            m = Match(missing_id=missing.id, sighting_id=sighting_id, score=score)
            db.add(m)
            created.append(m)
    db.commit()
    return created
