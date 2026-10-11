import csv
import io
import uuid
import zipfile

from tests.conftest import make_payload, submit

ROWS = [
    {"name": "Asha Verma", "email": "asha@example.com", "course": "Backend"},
    {"name": "", "course": "bad row"},
    {"name": "Ravi Kumar", "course": "Backend"},
]


def _items(client, auth, job_id):
    return client.get(f"/api/v1/certificate-jobs/{job_id}/certificates", headers=auth).json()["items"]


def test_download_single_pdf(client, auth):
    job_id = submit(client, auth, make_payload()).json()["job_id"]
    item = _items(client, auth, job_id)[0]
    r = client.get(item["download_url"], headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert "attachment" in r.headers["content-disposition"] and ".pdf" in r.headers["content-disposition"]


def test_zip_contains_succeeded_pdfs_and_summary(client, auth):
    job_id = submit(client, auth, make_payload(ROWS)).json()["job_id"]
    r = client.get(f"/api/v1/certificate-jobs/{job_id}/download", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["x-job-status"] == "COMPLETED_WITH_ERRORS"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert sorted(n for n in names if n.endswith(".pdf")) == ["0000_Asha_Verma.pdf", "0002_Ravi_Kumar.pdf"]
    assert all(zf.read(n).startswith(b"%PDF") for n in names if n.endswith(".pdf"))
    summary = list(csv.DictReader(io.StringIO(zf.read("summary.csv").decode())))
    assert [row["status"] for row in summary] == ["SUCCEEDED", "FAILED", "SUCCEEDED"]
    assert summary[1]["error_code"] == "VALIDATION_ERROR"


def test_zip_summary_neutralises_csv_formula_injection(client, auth):
    rows = [{"name": '=HYPERLINK("http://evil")', "course": "C"}]
    job_id = submit(client, auth, make_payload(rows)).json()["job_id"]
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/certificate-jobs/{job_id}/download", headers=auth).content))
    summary = list(csv.DictReader(io.StringIO(zf.read("summary.csv").decode())))
    assert summary[0]["name"].startswith("'=")
    assert all(".." not in n and "/" not in n and "=" not in n for n in zf.namelist())


def test_zip_conflicts_when_nothing_succeeded(client, auth):
    job_id = submit(client, auth, make_payload([{"name": "", "course": "x"}])).json()["job_id"]
    r = client.get(f"/api/v1/certificate-jobs/{job_id}/download", headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_READY"


def test_failed_certificate_cannot_be_downloaded(client, auth):
    job_id = submit(client, auth, make_payload(ROWS)).json()["job_id"]
    failed = [i for i in _items(client, auth, job_id) if i["status"] == "FAILED"][0]
    assert failed["download_url"] is None
    r = client.get(f"/api/v1/certificates/{failed['id']}/download", headers=auth)
    assert r.status_code == 409


def test_missing_file_on_disk_is_404(client, auth, storage):
    job_id = submit(client, auth, make_payload()).json()["job_id"]
    item = _items(client, auth, job_id)[0]
    storage.delete(f"{job_id}/{item['id']}.pdf")
    assert client.get(item["download_url"], headers=auth).status_code == 404


def test_unknown_certificate_is_404(client, auth):
    assert client.get(f"/api/v1/certificates/{uuid.uuid4()}/download", headers=auth).status_code == 404


def test_public_verification_exposes_minimum_data(client, auth):
    job_id = submit(client, auth, make_payload()).json()["job_id"]
    item = _items(client, auth, job_id)[0]
    r = client.get(f"/verify/{item['verification_code']}")  # no auth header on purpose
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["recipient_name"] == "Asha Verma"
    assert body["course"] == "Backend Engineering" and body["event_name"] == "Python Bootcamp 2026"
    assert "email" not in str(body).lower() and "asha@example.com" not in str(body)


def test_verification_unknown_code_is_404(client):
    assert client.get("/verify/does-not-exist").status_code == 404
    assert client.get("/verify/" + "x" * 100).status_code == 404


def test_failed_certificates_are_not_verifiable(client, auth):
    job_id = submit(client, auth, make_payload(ROWS)).json()["job_id"]
    code = [i for i in _items(client, auth, job_id) if i["status"] == "FAILED"][0]
    assert code["verification_code"] is None
