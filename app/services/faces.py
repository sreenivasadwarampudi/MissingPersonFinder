"""Face detection + embedding backends. All return L2-normalised 512-d vectors."""
import io
import logging
import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image

from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class FaceResult:
    embedding: list[float]
    det_score: float
    face_count: int


def cosine(a, b) -> float:
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1.0))


def _decode_bgr(data: bytes):
    import cv2

    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("could not decode image")
    return arr


class InsightFaceBackend:
    def __init__(self) -> None:
        from insightface.app import FaceAnalysis

        self.app = FaceAnalysis(
            name=settings.insightface_model,
            providers=["CPUExecutionProvider"],
            allowed_modules=["detection", "recognition"],
        )
        self.app.prepare(ctx_id=-1, det_size=(640, 640))

    def embed(self, data: bytes) -> FaceResult | None:
        faces = self.app.get(_decode_bgr(data))
        if not faces:
            return None
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        return FaceResult(face.normed_embedding.astype(float).tolist(), float(face.det_score), len(faces))


class DeepFaceBackend:
    def __init__(self) -> None:
        from deepface import DeepFace  # noqa: F401

    def embed(self, data: bytes) -> FaceResult | None:
        from deepface import DeepFace

        try:
            reps = DeepFace.represent(
                img_path=_decode_bgr(data),
                model_name=settings.deepface_model,
                detector_backend="opencv",
                enforce_detection=True,
            )
        except ValueError:  # DeepFace raises ValueError when no face is found
            return None
        if not reps:
            return None
        best = max(reps, key=lambda r: r["facial_area"]["w"] * r["facial_area"]["h"])
        v = np.asarray(best["embedding"], dtype=np.float32)
        v /= np.linalg.norm(v) or 1.0
        if v.shape[0] != 512:
            raise RuntimeError("Use a 512-d DeepFace model (Facenet512 or ArcFace); the DB column is vector(512)")
        return FaceResult(v.astype(float).tolist(), float(best.get("face_confidence", 1.0)), len(reps))


class MockBackend:
    """Demo/test backend. NOT face recognition: embeds a coarse brightness layout of the picture.
    Same picture (even upscaled or contrast-stretched) -> similar vector, different pictures -> unrelated."""

    def __init__(self) -> None:
        self.proj = np.random.default_rng(42).normal(size=(512, 256)).astype(np.float32)

    def embed(self, data: bytes) -> FaceResult | None:
        img = Image.open(io.BytesIO(data)).convert("L").resize((16, 16), Image.BILINEAR)
        g = np.asarray(img, dtype=np.float32).ravel()
        if g.std() < 3:  # blank picture -> "no face"
            return None
        g -= g.mean()
        g /= np.linalg.norm(g) or 1.0
        v = self.proj @ g
        v /= np.linalg.norm(v) or 1.0
        return FaceResult(v.astype(float).tolist(), 0.99, 1)


_backend = None
_lock = threading.Lock()


def get_backend():
    global _backend
    if _backend is None:
        with _lock:
            if _backend is None:
                name = settings.face_backend
                if name == "insightface":
                    _backend = InsightFaceBackend()
                elif name == "deepface":
                    _backend = DeepFaceBackend()
                elif name == "mock":
                    log.warning("FACE_BACKEND=mock: matching is NOT real face recognition")
                    _backend = MockBackend()
                else:
                    raise RuntimeError(f"Unknown FACE_BACKEND '{name}'")
    return _backend
