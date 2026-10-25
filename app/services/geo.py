"""Reverse geocoding via Nominatim (OpenStreetMap). Free, ~1 request/second, identify your app in the User-Agent."""
import logging
import threading
import time

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

_lock = threading.Lock()
_last_call = 0.0
_cache: dict[tuple[float, float], dict] = {}


def reverse_geocode(lat: float, lng: float) -> dict | None:
    """Returns {'address': full address (private), 'area_label': suburb/city/state} or None."""
    global _last_call
    if not settings.nominatim_url:
        return None
    key = (round(lat, 4), round(lng, 4))
    if key in _cache:
        return _cache[key]
    with _lock:  # one request at a time, at least 1.1 s apart (OSM usage policy)
        wait = 1.1 - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            r = httpx.get(
                f"{settings.nominatim_url}/reverse",
                params={"format": "jsonv2", "lat": lat, "lon": lng, "zoom": 18, "addressdetails": 1},
                headers={"User-Agent": f"{settings.app_name}/1.0 ({settings.nominatim_contact or 'contact-not-set'})"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.warning("Reverse geocoding failed: %s", exc)
            return None
        finally:
            _last_call = time.monotonic()
    a = data.get("address", {}) or {}
    parts = [
        a.get("suburb") or a.get("neighbourhood") or a.get("village") or a.get("hamlet"),
        a.get("city") or a.get("town") or a.get("county") or a.get("state_district"),
        a.get("state"),
    ]
    result = {"address": data.get("display_name"), "area_label": ", ".join(p for p in parts if p) or None}
    _cache[key] = result
    return result
