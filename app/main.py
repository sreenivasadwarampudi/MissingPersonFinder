import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import DEFAULT_SECRET, get_settings
from .db import SessionLocal, init_db
from .models import MissingPerson, Sighting
from .routers import admin, auth, files, matches, missing, reports, sightings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()


def _fail_interrupted_jobs() -> None:
    """Free hosts sleep and restart. A photo that was mid-processing when that happened would stay on
    'Processing…' forever, so mark it failed and let the user retry. (Assumes a single API instance.)"""
    with SessionLocal() as db:
        for model in (MissingPerson, Sighting):
            db.query(model).filter(model.status == "processing").update({"status": "failed"})
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.is_prod:
        if settings.secret_key == DEFAULT_SECRET or len(settings.secret_key) < 32:
            raise RuntimeError("Set SECRET_KEY to a random string of at least 32 characters when ENV=prod")
        if settings.face_backend == "mock":
            raise RuntimeError("FACE_BACKEND=mock is for demos only and cannot be used when ENV=prod")
    init_db()
    _fail_interrupted_jobs()
    yield


app = FastAPI(
    title=f"{settings.app_name} API",
    lifespan=lifespan,
    docs_url=None if settings.is_prod else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_prod else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=False,  # we use Bearer tokens, not cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_BODY = (settings.max_upload_mb + 1) * 1024 * 1024


@app.middleware("http")
async def guard(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
    return response


for r in (auth, missing, sightings, matches, files, reports, admin):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def public_config():
    """Non-sensitive text the frontend shows."""
    return {
        "app_name": settings.app_name,
        "ngo_name": settings.ngo_name,
        "ngo_contact": settings.ngo_contact,
        "grievance_email": settings.grievance_email,
    }
