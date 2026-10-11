from sqlalchemy import func, select

from app.db.models import Certificate, Job
from tests.conftest import make_payload, submit


def test_create_job_returns_202_and_creates_rows(client, auth, db):
    r = submit(client, auth, make_payload())
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "QUEUED"
    assert (body["total"], body["accepted"], body["rejected"]) == (2, 2, 0)
    assert body["status_url"].endswith(body["job_id"])
    assert db.scalar(select(func.count()).select_from(Job)) == 1
    assert db.scalar(select(func.count()).select_from(Certificate)) == 2


def test_job_requires_authentication(client):
    assert client.post("/api/v1/certificate-jobs", json=make_payload()).status_code == 401


def test_idempotency_key_returns_same_job(client, auth, db):
    first = submit(client, auth, make_payload(), **{"Idempotency-Key": "abc-123"})
    again = submit(client, auth, make_payload(), **{"Idempotency-Key": "abc-123"})
    assert first.status_code == 202 and again.status_code == 200
    assert again.headers["idempotent-replay"] == "true"
    assert first.json()["job_id"] == again.json()["job_id"]
    assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_idempotency_key_with_different_payload_conflicts(client, auth):
    submit(client, auth, make_payload(), **{"Idempotency-Key": "abc-123"})
    other = make_payload([{"name": "Someone Else", "course": "Other"}])
    r = submit(client, auth, other, **{"Idempotency-Key": "abc-123"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_idempotency_keys_are_scoped_per_user(client, auth, other_auth):
    a = submit(client, auth, make_payload(), **{"Idempotency-Key": "same-key"})
    b = submit(client, other_auth, make_payload(), **{"Idempotency-Key": "same-key"})
    assert a.status_code == b.status_code == 202
    assert a.json()["job_id"] != b.json()["job_id"]


def test_bad_idempotency_key_rejected(client, auth):
    r = submit(client, auth, make_payload(), **{"Idempotency-Key": "bad key with spaces!"})
    assert r.status_code == 422
