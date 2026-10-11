"""Test setup. Environment variables are set BEFORE the app is imported, because settings
and the DB engine are created at import time. Tests run on SQLite with Celery in eager mode,
so no Postgres or Redis is needed."""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="certgen-tests-"))
os.environ.update(
    ENVIRONMENT="test",
    DATABASE_URL=f"sqlite:///{_TMP / 'test.db'}",
    JWT_SECRET="test-secret-test-secret-test-secret-123456",
    STORAGE_DIR=str(_TMP / "storage"),
    CELERY_ALWAYS_EAGER="true",
    RATE_LIMIT_ENABLED="false",
    MAX_RECIPIENTS="5",
    MAX_BODY_BYTES="20000",
    PUBLIC_BASE_URL="http://testserver",
    STUCK_TIMEOUT_SECONDS="300",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.storage import get_storage  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def storage():
    return get_storage()


def register_and_login(
    client: TestClient, email: str = "owner@example.com", password: str = "S3cure-pass!"
) -> dict[str, str]:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client):
    return register_and_login(client)


@pytest.fixture
def other_auth(client):
    return register_and_login(client, "intruder@example.com")


def make_payload(recipients=None, **overrides) -> dict:
    payload = {
        "event_name": "Python Bootcamp 2026",
        "issuer_name": "Acme Academy",
        "issued_on": "2026-10-09",
        "recipients": recipients
        if recipients is not None
        else [
            {"name": "Asha Verma", "email": "asha@example.com", "course": "Backend Engineering", "grade": "A"},
            {"name": "Ravi Kumar", "email": "ravi@example.com", "course": "Backend Engineering"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def payload():
    return make_payload


def submit(client, headers, body, **extra_headers):
    return client.post("/api/v1/certificate-jobs", json=body, headers={**headers, **extra_headers})
