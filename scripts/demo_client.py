import json
import time

import requests

BASE_URL = "http://localhost:8000"
EMAIL = "demo@example.com"
PASSWORD = "S3cure-pass!"

JOB = {
    "event_name": "Python Bootcamp 2026",
    "issuer_name": "Acme Academy",
    "issued_on": "2026-10-09",
    "recipients": [
        {"name": "Asha Verma", "email": "asha@example.com", "course": "Backend Engineering", "grade": "A"},
        {"name": "Ravi Kumar", "email": "ravi@example.com", "course": "Backend Engineering", "grade": "B+"},
        {"name": "", "email": "not-an-email", "course": "Backend Engineering"},  # invalid on purpose
    ],
}


def show(response, sent=None):
    """Print the request and the response."""
    print("\n" + "=" * 60)
    print("REQUEST :", response.request.method, response.request.url)
    if sent:
        print(json.dumps(sent, indent=2, ensure_ascii=False))
    print("RESPONSE:", response.status_code)
    if "json" in response.headers.get("content-type", ""):
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    else:
        print(f"<{len(response.content)} bytes of file data>")


# 1. Register (if the user already exists you get 409, that is fine)
body = {"email": EMAIL, "password": PASSWORD}
r = requests.post(f"{BASE_URL}/api/v1/auth/register", json=body)
show(r, body)

# 2. Login to get the JWT token
r = requests.post(f"{BASE_URL}/api/v1/auth/login", json=body)
show(r, body)
headers = {"Authorization": "Bearer " + r.json()["access_token"]}

# 3. Create the bulk job
r = requests.post(f"{BASE_URL}/api/v1/certificate-jobs", json=JOB, headers=headers)
show(r, JOB)
job_id = r.json()["job_id"]

# 4. Wait until the job is finished
while True:
    r = requests.get(f"{BASE_URL}/api/v1/certificate-jobs/{job_id}", headers=headers)
    if r.json()["status"] in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
        break
    time.sleep(1)
show(r)

# 5. See the result of every certificate
r = requests.get(f"{BASE_URL}/api/v1/certificate-jobs/{job_id}/certificates", headers=headers)
show(r)

# 6. Download all certificates as a ZIP
r = requests.get(f"{BASE_URL}/api/v1/certificate-jobs/{job_id}/download", headers=headers)
show(r)
with open("certificates.zip", "wb") as f:
    f.write(r.content)
print("\nSaved certificates.zip")