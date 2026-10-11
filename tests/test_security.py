import io
import zipfile

import pytest

from app.services.storage import LocalStorage
from tests.conftest import make_payload, submit

ROW = {"name": "Asha Verma", "course": "Backend"}


def test_user_cannot_read_another_users_job(client, auth, other_auth):
    job_id = submit(client, auth, make_payload()).json()["job_id"]
    for path in (
        f"/api/v1/certificate-jobs/{job_id}",
        f"/api/v1/certificate-jobs/{job_id}/certificates",
        f"/api/v1/certificate-jobs/{job_id}/download",
    ):
        r = client.get(path, headers=other_auth)
        assert r.status_code == 404, path  # 404 (not 403) so existence is not revealed
    assert client.post(f"/api/v1/certificate-jobs/{job_id}/retry-failed", headers=other_auth).status_code == 404


def test_user_cannot_download_another_users_certificate(client, auth, other_auth):
    job_id = submit(client, auth, make_payload()).json()["job_id"]
    item = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates", headers=auth).json()["items"][0]
    assert client.get(item["download_url"], headers=other_auth).status_code == 404
    assert client.get(item["download_url"], headers=auth).status_code == 200


def test_markup_and_injection_strings_are_stored_and_rendered_literally(client, auth):
    nasty = ["<script>alert(1)</script>", "'; DROP TABLE jobs;--", "{{7*7}}", "%s%s%s%n", "<b>x</b>"]
    rows = [{"name": n, "course": "C"} for n in nasty]
    job_id = submit(client, auth, make_payload(rows, event_name="Evil <i>Event</i>")).json()["job_id"]
    body = client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).json()
    assert body["status"] == "COMPLETED" and body["succeeded"] == len(nasty)
    items = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates", headers=auth).json()["items"]
    assert [i["recipient_name"] for i in items] == nasty  # stored verbatim, parameterised SQL
    assert client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).status_code == 200  # tables intact


def test_path_traversal_in_names_never_reaches_filesystem(client, auth):
    job_id = submit(client, auth, make_payload([{"name": "../../etc/passwd", "course": "C"}])).json()["job_id"]
    r = client.get(f"/api/v1/certificate-jobs/{job_id}/download", headers=auth)
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert all(".." not in n and "/" not in n for n in names)
    item = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates", headers=auth).json()["items"][0]
    cd = client.get(item["download_url"], headers=auth).headers["content-disposition"]
    assert ".." not in cd and "/" not in cd


@pytest.mark.parametrize("key", ["../outside.pdf", "/etc/passwd", "a/../../b.pdf", ""])
def test_local_storage_blocks_traversal(tmp_path, key):
    store = LocalStorage(tmp_path / "base")
    with pytest.raises(ValueError):
        store.save(key, b"x")


def test_local_storage_roundtrip_and_atomic_write(tmp_path):
    store = LocalStorage(tmp_path)
    stored = store.save("job/cert.pdf", b"%PDF-data")
    assert stored.size == 9 and len(stored.sha256) == 64 and store.exists("job/cert.pdf")
    with store.open("job/cert.pdf") as fh:
        assert fh.read() == b"%PDF-data"
    assert not list(tmp_path.rglob("*.tmp"))
    store.delete("job/cert.pdf")
    assert not store.exists("job/cert.pdf")


def test_security_headers_present(client):
    r = client.get("/health/live")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-request-id"]


def test_downloads_are_not_cacheable(client, auth):
    job_id = submit(client, auth, make_payload()).json()["job_id"]
    r = client.get(f"/api/v1/certificate-jobs/{job_id}/download", headers=auth)
    assert "no-store" in r.headers["cache-control"]


def test_errors_do_not_leak_internals(client, auth, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret-connection-string")

    monkeypatch.setattr("app.api.routes_jobs.job_service.get_job", boom)
    r = client.get("/api/v1/certificate-jobs/00000000-0000-0000-0000-000000000000", headers=auth)
    assert r.status_code == 500 and "secret" not in r.text
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"


def test_error_shape_is_consistent(client, auth):
    for r in (
        client.get("/api/v1/certificate-jobs/x", headers=auth),
        client.get("/api/v1/certificate-jobs/00000000-0000-0000-0000-000000000000", headers=auth),
        client.get("/api/v1/certificate-jobs/00000000-0000-0000-0000-000000000000"),
        client.get("/nope"),
    ):
        assert set(r.json()["error"]) == {"code", "message", "details"}


def test_health_endpoints(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    r = client.get("/health")  # no Redis in the test environment -> degraded, but DB is ok
    assert r.json()["checks"]["database"] == "ok"


def test_rate_limit_blocks_bursts(client, monkeypatch):
    """Uses an in-memory stand-in for Redis."""
    from app.config import get_settings
    from app.core import ratelimit

    class FakeRedis:
        def __init__(self):
            self.counts = {}

        def incr(self, k):
            self.counts[k] = self.counts.get(k, 0) + 1
            return self.counts[k]

        def expire(self, k, s):
            pass

    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    monkeypatch.setattr(get_settings(), "rate_limit_login_per_minute", 3)
    fake = FakeRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)
    codes = [
        client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "whatever123"}).status_code
        for _ in range(5)
    ]
    assert codes == [401, 401, 401, 429, 429]


def test_registration_can_be_disabled(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "allow_registration", False)
    r = client.post("/api/v1/auth/register", json={"email": "x@example.com", "password": "S3cure-pass!"})
    assert r.status_code == 403
