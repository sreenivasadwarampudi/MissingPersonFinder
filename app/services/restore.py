"""Image cleanup. Restoration is preferred over regeneration, and the original is ALWAYS kept."""
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from .images import basic_enhance

log = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class Restored:
    data: bytes | None
    method: str


_gfpgan = None


def _gfpgan_restore(jpeg: bytes) -> bytes:
    """Local GFPGAN face restoration. weight=0.5 keeps more of the original identity."""
    global _gfpgan
    import cv2
    import numpy as np
    from gfpgan import GFPGANer

    if _gfpgan is None:
        if not settings.gfpgan_model_path:
            raise RuntimeError("GFPGAN_MODEL_PATH is not set")
        _gfpgan = GFPGANer(
            model_path=settings.gfpgan_model_path, upscale=1, arch="clean", channel_multiplier=2, bg_upsampler=None
        )
    arr = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    _, _, out = _gfpgan.enhance(arr, has_aligned=False, only_center_face=True, paste_back=True, weight=0.5)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise RuntimeError("could not encode restored image")
    return buf.tobytes()


def _hf_space_restore(jpeg: bytes) -> bytes:
    """Call a public Hugging Face Space with gradio_client. The photo leaves your server.
    Argument order differs per Space: open the Space page, click 'Use via API' and adjust below."""
    from gradio_client import Client, handle_file

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.jpg"
        src.write_bytes(jpeg)
        client = Client(settings.hf_space_id)  # picks up HF_TOKEN from the environment if set
        # CodeFormer Space: (image, background_enhance, face_upsample, upscale, fidelity). Fidelity 0.8 favours the real face.
        result = client.predict(handle_file(str(src)), False, True, 2, 0.8, api_name=settings.hf_space_api_name)
        path = result if isinstance(result, str) else result[0]
        return Path(path).read_bytes()


def restore(jpeg: bytes) -> Restored:
    method = settings.restore_backend
    if method == "none":
        return Restored(None, "none")
    try:
        if method == "gfpgan":
            return Restored(_gfpgan_restore(jpeg), "gfpgan")
        if method == "hf_space":
            return Restored(_hf_space_restore(jpeg), f"hf_space:{settings.hf_space_id}")
    except Exception:
        log.exception("Restoration backend '%s' failed; falling back to basic enhancement", method)
        return Restored(basic_enhance(jpeg), f"basic (fallback from {method})")
    return Restored(basic_enhance(jpeg), "basic")
