"""Password hashing, JWT handling and small helpers. No secrets are hard-coded."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings
from app.core.errors import Unauthorized

# Pre-computed hash used to keep login timing constant when the email is unknown.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=12))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, hashed: str | None) -> bool:
    """Constant-ish time: always performs one bcrypt comparison."""
    target = hashed.encode("ascii") if hashed else _DUMMY_HASH
    try:
        ok = bcrypt.checkpw(password.encode("utf-8"), target)
    except ValueError:
        return False
    return ok and hashed is not None


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=s.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    token = jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)
    return token, s.jwt_expire_minutes * 60


def decode_access_token(token: str) -> uuid.UUID:
    s = get_settings()
    try:
        payload = jwt.decode(
            token,
            s.jwt_secret,
            algorithms=[s.jwt_algorithm],  # fixed allow-list blocks alg=none / confusion
            options={"require": ["exp", "iat", "sub"]},
        )
        if payload.get("typ") != "access":
            raise Unauthorized("Invalid token")
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise Unauthorized("Invalid or expired token") from None


def new_verification_code() -> str:
    return secrets.token_urlsafe(16)
