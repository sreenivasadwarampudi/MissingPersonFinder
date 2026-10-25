import logging
import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


def _normalize(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _normalize(settings.database_url)
IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_PG = DATABASE_URL.startswith("postgresql")

if IS_SQLITE:
    path = DATABASE_URL.split("///", 1)[-1]
    if path and path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # prepare_threshold=None keeps psycopg3 compatible with Supabase's pgbouncer pooler
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"prepare_threshold": None})

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    if IS_PG:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    if IS_PG:
        for table in ("missing_persons", "sightings"):
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            f"CREATE INDEX IF NOT EXISTS {table}_embedding_hnsw "
                            f"ON {table} USING hnsw (embedding vector_cosine_ops)"
                        )
                    )
            except Exception:  # older pgvector without HNSW: exact search still works
                log.warning("Could not create HNSW index on %s", table)
