import time
import uuid

import jwt

from app.config import get_settings
from tests.conftest import register_and_login


def test_register_and_login_returns_jwt(client):
    headers = register_and_login(client)
    token = headers["Authorization"].split()[1]
    claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    assert claims["typ"] == "access" and "exp" in claims and "sub" in claims


def test_password_is_stored_hashed(client, db):
    from sqlalchemy import select

    from app.db.models import User

    register_and_login(client, "hash@example.com", "S3cure-pass!")
    user = db.scalar(select(User).where(User.email == "hash@example.com"))
    assert user.password_hash.startswith("$2") and "S3cure-pass!" not in user.password_hash


def test_duplicate_registration_conflicts(client):
    register_and_login(client)
    r = client.post("/api/v1/auth/register", json={"email": "OWNER@example.com", "password": "S3cure-pass!"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "EMAIL_TAKEN"


def test_wrong_password_and_unknown_user_look_identical(client):
    register_and_login(client)
    bad_pw = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "wrong-password"})
    no_user = client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "wrong-password"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json() == no_user.json()


def test_weak_or_oversized_password_rejected(client):
    assert client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "short"}).status_code == 422
    assert (
        client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "x" * 80}).status_code == 422
    )


def test_protected_endpoints_require_token(client):
    assert client.get(f"/api/v1/certificate-jobs/{uuid.uuid4()}").status_code == 401
    assert client.post("/api/v1/certificate-jobs", json={}).status_code == 401


def test_garbage_token_rejected(client):
    r = client.get(f"/api/v1/certificate-jobs/{uuid.uuid4()}", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_token_signed_with_wrong_secret_rejected(client):
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": int(time.time()), "exp": int(time.time()) + 600, "typ": "access"},
        "x" * 40,
        algorithm="HS256",
    )
    r = client.get(f"/api/v1/certificate-jobs/{uuid.uuid4()}", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_alg_none_token_rejected(client):
    unsigned = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": int(time.time()), "exp": int(time.time()) + 600, "typ": "access"},
        key=None,
        algorithm="none",
    )
    r = client.get(f"/api/v1/certificate-jobs/{uuid.uuid4()}", headers={"Authorization": f"Bearer {unsigned}"})
    assert r.status_code == 401


def test_expired_token_rejected(client):
    register_and_login(client)
    from sqlalchemy import select

    from app.db.models import User
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        uid = s.scalar(select(User.id))
    expired = jwt.encode(
        {"sub": str(uid), "iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600, "typ": "access"},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    r = client.get(f"/api/v1/certificate-jobs/{uuid.uuid4()}", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


def test_token_without_access_type_rejected(client):
    from sqlalchemy import select

    from app.db.models import User
    from app.db.session import SessionLocal

    register_and_login(client)
    with SessionLocal() as s:
        uid = s.scalar(select(User.id))
    tok = jwt.encode(
        {"sub": str(uid), "iat": int(time.time()), "exp": int(time.time()) + 600, "typ": "refresh"},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    r = client.get(f"/api/v1/certificate-jobs/{uuid.uuid4()}", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401
