"""Upload validation, EXIF handling and the safe 'basic' enhancement."""
import io
import logging

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageFilter, ImageOps

from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

try:  # optional: iPhone HEIC support
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pass

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO", "HEIF"}


def read_upload(file: UploadFile) -> bytes:
    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if not data:
        raise HTTPException(400, "The uploaded file is empty")
    if len(data) > limit:
        raise HTTPException(413, f"Photo is larger than {settings.max_upload_mb} MB")
    return data


def sanitize_image(data: bytes) -> bytes:
    """Validate, fix orientation, downscale and re-encode as JPEG. Re-encoding drops all EXIF/GPS metadata."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ALLOWED_FORMATS:
            raise ValueError(f"format {img.format}")
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise HTTPException(400, "Unsupported or corrupt image. Please upload a JPEG, PNG or WebP photo.")
    img.thumbnail((settings.max_image_side, settings.max_image_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


def basic_enhance(jpeg: bytes) -> bytes:
    """Conservative cleanup: upscale tiny photos, stretch contrast, light sharpen.
    It cannot invent facial detail, so it cannot change who the person looks like."""
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    w, h = img.size
    short = min(w, h)
    if short < 480:
        scale = 480 / short
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=60, threshold=3))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _dms(values, ref) -> float | None:
    try:
        d, m, s = (float(v) for v in values)
    except Exception:
        return None
    deg = d + m / 60 + s / 3600
    if isinstance(ref, bytes):
        ref = ref.decode(errors="ignore")
    return -deg if str(ref).upper() in ("S", "W") else deg


def read_exif_gps(data: bytes) -> tuple[float, float] | None:
    """GPS fallback: read coordinates from the ORIGINAL upload before metadata is stripped."""
    try:
        img = Image.open(io.BytesIO(data))
        gps = img.getexif().get_ifd(0x8825)
        if not gps:
            return None
        lat = _dms(gps.get(2), gps.get(1))
        lng = _dms(gps.get(4), gps.get(3))
        if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None
        if lat == 0 and lng == 0:  # many cameras write 0,0 when there was no fix
            return None
        return lat, lng
    except Exception:
        return None
