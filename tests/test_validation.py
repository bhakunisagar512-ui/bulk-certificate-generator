import pytest

from app.services.validation import validate_recipient
from tests.conftest import make_payload, submit

GOOD = {"name": "Asha Verma", "email": "Asha@Example.com", "course": "Backend", "grade": "A"}


def test_valid_recipient_is_normalised():
    r = validate_recipient(GOOD)
    assert r.ok and r.data["email"] == "asha@example.com"
    assert r.data["extra"] == {"course": "Backend", "grade": "A"}


@pytest.mark.parametrize(
    "bad, fragment",
    [
        ({**GOOD, "name": ""}, "name"),
        ({**GOOD, "name": "   "}, "name"),
        ({**GOOD, "name": "x" * 101}, "name"),
        ({**GOOD, "name": "Evil\x00Name"}, "control"),
        ({**GOOD, "email": "not-an-email"}, "email"),
        ({**GOOD, "course": ""}, "course"),
        ({**GOOD, "grade": "g" * 21}, "grade"),
        ({"course": "Backend"}, "name"),
        ({**GOOD, "unexpected": 1}, "unexpected"),
        ("just a string", "object"),
        (None, "object"),
    ],
)
def test_invalid_recipients_are_rejected_with_reason(bad, fragment):
    r = validate_recipient(bad)
    assert not r.ok and fragment in r.message


def test_blank_optional_fields_become_none():
    r = validate_recipient({**GOOD, "email": "", "grade": ""})
    assert r.ok and r.data["email"] is None and r.data["extra"]["grade"] is None


def test_empty_recipient_list_is_422(client, auth):
    assert submit(client, auth, make_payload([])).status_code == 422


def test_too_many_recipients_is_422(client, auth):
    many = [{"name": f"P{i}", "course": "C"} for i in range(6)]  # MAX_RECIPIENTS=5 in tests
    r = submit(client, auth, make_payload(many))
    assert r.status_code == 422 and "at most 5" in r.json()["error"]["message"]


def test_unknown_top_level_field_is_422(client, auth):
    assert submit(client, auth, make_payload(surprise="x")).status_code == 422


def test_missing_required_field_is_422(client, auth):
    body = make_payload()
    del body["event_name"]
    r = submit(client, auth, body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_date_is_422(client, auth):
    assert submit(client, auth, make_payload(issued_on="not-a-date")).status_code == 422


def test_oversized_body_is_413(client, auth):
    big = [{"name": "N" * 90, "course": "C" * 140} for _ in range(5)] * 50  # exceeds 20 000 bytes
    r = client.post("/api/v1/certificate-jobs", json=make_payload(big), headers=auth)
    assert r.status_code == 413


def test_bad_rows_are_recorded_and_good_rows_still_processed(client, auth):
    rows = [GOOD, {"name": "", "email": "bad", "course": "X"}, {"name": "Ravi", "course": "Backend"}]
    r = submit(client, auth, make_payload(rows))
    assert r.status_code == 202
    assert (r.json()["accepted"], r.json()["rejected"]) == (2, 1)
    items = client.get(f"/api/v1/certificate-jobs/{r.json()['job_id']}/certificates", headers=auth).json()["items"]
    bad = [i for i in items if i["status"] == "FAILED"]
    assert len(bad) == 1 and bad[0]["index"] == 1 and bad[0]["error_code"] == "VALIDATION_ERROR"
    assert "name" in bad[0]["error_message"]
    assert sum(i["status"] == "SUCCEEDED" for i in items) == 2
