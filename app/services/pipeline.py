"""Image pipeline: restore -> embed, with a guard so cleanup can never change who the person is."""
import logging
from dataclasses import dataclass

from ..config import get_settings
from .faces import cosine, get_backend
from .restore import restore

log = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class Processed:
    restored: bytes | None
    restore_method: str
    embedding: list[float] | None
    embedding_source: str | None  # original | restored
    det_score: float | None
    face_count: int


def process(jpeg: bytes) -> Processed:
    faces = get_backend()
    original = faces.embed(jpeg)

    r = restore(jpeg)
    restored_bytes, method = r.data, r.method
    restored_face = faces.embed(restored_bytes) if restored_bytes else None

    if original is not None:
        # Identity guard: generative cleanup can alter a face. If the cleaned face no longer
        # looks like the original, throw the cleaned copy away.
        if restored_face is not None:
            sim = cosine(original.embedding, restored_face.embedding)
            if sim < settings.restore_min_identity:
                log.warning("Discarding cleaned image: identity drift (similarity %.2f)", sim)
                restored_bytes, method = None, f"{method}:discarded_identity_drift"
        return Processed(restored_bytes, method, original.embedding, "original", original.det_score, original.face_count)

    # Original had no detectable face (tiny / blurry). Try the cleaned copy and say so.
    if restored_face is not None:
        return Processed(
            restored_bytes, method, restored_face.embedding, "restored", restored_face.det_score, restored_face.face_count
        )
    return Processed(restored_bytes, method, None, None, None, 0)
