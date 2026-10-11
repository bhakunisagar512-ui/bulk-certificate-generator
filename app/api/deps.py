import re
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AppError, Unauthorized
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db

bearer_scheme = HTTPBearer(auto_error=False, description="JWT from POST /api/v1/auth/login")
DbSession = Annotated[Session, Depends(get_db)]

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


def get_current_user(
    db: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("Missing bearer token")
    user_id = decode_access_token(creds.credentials)
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Invalid or expired token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    if idempotency_key is None:
        return None
    if not _IDEMPOTENCY_RE.match(idempotency_key):
        raise AppError(
            "Idempotency-Key must be 1-100 chars of letters, digits, . _ : -", code="VALIDATION_ERROR", status_code=422
        )
    return idempotency_key
