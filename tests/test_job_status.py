import uuid

from tests.conftest import make_payload, submit

GOOD = {"name": "Asha Verma", "course": "Backend"}


def test_status_after_successful_job(client, auth):
    job_id = submit(client, auth, make_payload()).json()["job_id"]
    body = client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).json()
    assert body["status"] == "COMPLETED"
    assert (body["total"], body["succeeded"], body["failed"], body["pending"]) == (2, 2, 0, 0)
    assert body["progress_percent"] == 100 and body["finished_at"] and body["download_url"]


def test_mixed_job_is_completed_with_errors(client, auth):
    rows = [GOOD, {"name": "", "course": "X"}]
    job_id = submit(client, auth, make_payload(rows)).json()["job_id"]
    body = client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).json()
    assert body["status"] == "COMPLETED_WITH_ERRORS"
    assert (body["succeeded"], body["failed"]) == (1, 1)


def test_all_invalid_job_fails_immediately(client, auth):
    rows = [{"name": "", "course": "X"}, {"course": "Y"}]
    r = submit(client, auth, make_payload(rows))
    assert r.status_code == 202 and r.json()["accepted"] == 0
    body = client.get(f"/api/v1/certificate-jobs/{r.json()['job_id']}", headers=auth).json()
    assert body["status"] == "FAILED" and body["download_url"] is None and body["finished_at"]


def test_progress_for_partially_processed_job(client, auth, db):
    """Simulate a job in flight by inserting counters directly."""
    from app.db.models import Job

    job_id = submit(client, auth, make_payload([GOOD] * 4)).json()["job_id"]
    job = db.get(Job, uuid.UUID(job_id))
    job.status, job.succeeded_count, job.failed_count, job.finished_at = "PROCESSING", 1, 1, None
    db.commit()
    body = client.get(f"/api/v1/certificate-jobs/{job_id}", headers=auth).json()
    assert (body["pending"], body["progress_percent"]) == (2, 50)


def test_unknown_job_is_404(client, auth):
    r = client.get(f"/api/v1/certificate-jobs/{uuid.uuid4()}", headers=auth)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_malformed_job_id_is_422(client, auth):
    assert client.get("/api/v1/certificate-jobs/not-a-uuid", headers=auth).status_code == 422


def test_item_list_filter_and_pagination(client, auth):
    rows = [GOOD, GOOD, {"name": "", "course": "X"}, GOOD]
    job_id = submit(client, auth, make_payload(rows)).json()["job_id"]
    base = f"/api/v1/certificate-jobs/{job_id}/certificates"
    assert client.get(base, headers=auth).json()["total"] == 4
    failed = client.get(base, params={"status": "FAILED"}, headers=auth).json()
    assert failed["total"] == 1 and failed["items"][0]["index"] == 2
    page = client.get(base, params={"page": 2, "page_size": 3}, headers=auth).json()
    assert len(page["items"]) == 1 and page["items"][0]["index"] == 3
    assert client.get(base, params={"status": "BOGUS"}, headers=auth).status_code == 422
    assert client.get(base, params={"page_size": 1000}, headers=auth).status_code == 422
