import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    LOCAL_STORAGE_DIR=f"{_tmp}/uploads",
    FACE_BACKEND="mock",
    RESTORE_BACKEND="basic",
    MODERATOR_EMAILS="mod@example.org",
    NOMINATIM_URL="",
    ENV="dev",
    SECRET_KEY="test-secret-test-secret-test-secret-123",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.security import limiter  # noqa: E402


@pytest.fixture()
def client():
    limiter.clear()
    with TestClient(app) as c:
        yield c
