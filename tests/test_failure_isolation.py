import uuid

from sqlalchemy import select

from app.db.models import Certificate
from app.services.exceptions import StorageError
from app.services.renderer import ReportLabRenderer
from tests.conftest import make_payload, submit

ROWS = [{"name": "Good One", "course": "C"}, {"name": "Explode", "course": "C"}, {"name": "Good Two", "course": "C"}]


class ExplodingRenderer(ReportLabRenderer):
    def render(self, data):
        if data.recipient_name == "Explode":
            raise RuntimeError("boom")
        return super().render(data)


def test_one_failed_certificate_does_not_block_others(client, auth, monkeypatch):
    monkeypatch.setattr("app.worker.tasks.get_renderer", lambda: ExplodingRenderer())
    job_id = submit(client, auth, make_payload(ROWS)).json()["job_id"]

    job = client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).json()
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert (job["succeeded"], job["failed"], job["pending"]) == (2, 1, 0)

    items = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates", headers=auth).json()["items"]
    failed = [i for i in items if i["status"] == "FAILED"]
    assert len(failed) == 1 and failed[0]["recipient_name"] == "Explode"
    assert failed[0]["error_code"] == "RENDER_ERROR"
    assert "boom" not in failed[0]["error_message"]  # internals are not leaked to the client


def test_running_the_same_task_twice_does_not_double_count(client, auth):
    from app.worker.tasks import generate_certificate

    job_id = submit(client, auth, make_payload()).json()["job_id"]
    items = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates", headers=auth).json()["items"]
    assert generate_certificate.apply(args=[items[0]["id"]]).result == "skipped"
    job = client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).json()
    assert (job["succeeded"], job["failed"]) == (2, 0)


def test_retry_failed_requeues_only_retryable_items(client, auth, monkeypatch):
    rows = ROWS + [{"name": "", "course": "bad row"}]
    monkeypatch.setattr("app.worker.tasks.get_renderer", lambda: ExplodingRenderer())
    job_id = submit(client, auth, make_payload(rows)).json()["job_id"]
    assert client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).json()["failed"] == 2

    monkeypatch.setattr("app.worker.tasks.get_renderer", lambda: ReportLabRenderer())  # "bug fixed"
    r = client.post(f"/api/v1/certificate-jobs/{job_id}/retry-failed", headers=auth)
    assert r.status_code == 202 and r.json()["requeued"] == 1

    job = client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).json()
    assert job["status"] == "COMPLETED_WITH_ERRORS"  # the invalid row stays failed
    assert (job["succeeded"], job["failed"]) == (3, 1)

    again = client.post(f"/api/v1/certificate-jobs/{job_id}/retry-failed", headers=auth)
    assert again.status_code == 409 and again.json()["error"]["code"] == "NOTHING_TO_RETRY"


def test_storage_failure_is_retried_then_marked_failed(client, auth, monkeypatch):
    calls = {"n": 0}

    class BrokenStorage:
        def save(self, key, content):
            calls["n"] += 1
            raise StorageError("disk full")

    monkeypatch.setattr("app.worker.tasks.get_storage", lambda: BrokenStorage())
    job_id = submit(client, auth, make_payload([{"name": "A", "course": "C"}])).json()["job_id"]
    items = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates", headers=auth).json()["items"]
    assert items[0]["status"] == "FAILED" and items[0]["error_code"] == "STORAGE_ERROR"
    assert calls["n"] == 4  # first attempt + 3 retries


def test_sweeper_recovers_a_stuck_certificate(client, auth, db, monkeypatch):
    """A worker died mid-task: the row is stuck in PROCESSING. The sweeper must rescue it."""
    from datetime import timedelta

    from app.db.models import Certificate as C
    from app.db.models import utcnow
    from app.worker.tasks import recover_stuck

    # Make the first run a no-op by pointing the renderer at something that never finishes the job
    monkeypatch.setattr("app.services.job_service.enqueue_certificates", lambda ids: None)
    job_id = submit(client, auth, make_payload([{"name": "A", "course": "C"}])).json()["job_id"]
    cert = db.scalar(select(C).where(C.job_id == uuid.UUID(job_id)))
    cert.status, cert.attempts, cert.updated_at = "PROCESSING", 1, utcnow() - timedelta(hours=1)
    db.commit()

    monkeypatch.undo()  # real enqueue again (eager)
    assert recover_stuck.apply().result == 1
    db.expire_all()
    job = client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).json()
    assert job["status"] == "COMPLETED" and job["succeeded"] == 1


def test_sweeper_gives_up_after_max_attempts(client, auth, db, monkeypatch):
    from datetime import timedelta

    from app.db.models import Certificate as C
    from app.db.models import utcnow
    from app.worker.tasks import recover_stuck

    monkeypatch.setattr("app.services.job_service.enqueue_certificates", lambda ids: None)
    job_id = submit(client, auth, make_payload([{"name": "A", "course": "C"}])).json()["job_id"]
    cert = db.scalar(select(C).where(C.job_id == uuid.UUID(job_id)))
    cert.status, cert.attempts, cert.updated_at = "PROCESSING", 5, utcnow() - timedelta(hours=1)
    db.commit()

    recover_stuck.apply()
    items = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates", headers=auth).json()["items"]
    assert items[0]["status"] == "FAILED" and items[0]["error_code"] == "STUCK_TIMEOUT"
    assert client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).json()["status"] == "FAILED"


def test_cleanup_removes_old_jobs_and_files(client, auth, db, storage, monkeypatch):
    from datetime import timedelta

    from app.config import get_settings
    from app.db.models import Job, utcnow
    from app.worker.tasks import cleanup_old_jobs

    job_id = submit(client, auth, make_payload()).json()["job_id"]
    cert = db.scalar(select(Certificate).where(Certificate.job_id == uuid.UUID(job_id)))
    assert storage.exists(cert.file_path)
    db.get(Job, uuid.UUID(job_id)).created_at = utcnow() - timedelta(days=90)
    db.commit()

    monkeypatch.setattr(get_settings(), "retention_days", 30)
    assert cleanup_old_jobs.apply().result == 1
    assert not storage.exists(cert.file_path)
    assert client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).status_code == 404
