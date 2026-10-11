from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.config import get_settings
from app.core.errors import AppError, Conflict, Unauthorized
from app.core.ratelimit import limit_by_ip
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.schemas.auth import Credentials, TokenOut, UserOut

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_login_limit = limit_by_ip("auth", lambda: get_settings().rate_limit_login_per_minute)


@router.post("/register", response_model=UserOut, status_code=201, dependencies=[Depends(_login_limit)])
def register(body: Credentials, db: DbSession):
    if not get_settings().allow_registration:
        raise AppError("Registration is disabled", code="REGISTRATION_DISABLED", status_code=403)
    if db.scalar(select(User.id).where(User.email == body.email)):
        raise Conflict("Email already registered", code="EMAIL_TAKEN")
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("Email already registered", code="EMAIL_TAKEN") from None
    return user


@router.post("/login", response_model=TokenOut, dependencies=[Depends(_login_limit)])
def login(body: Credentials, db: DbSession):
    user = db.scalar(select(User).where(User.email == body.email))
    ok = verify_password(body.password, user.password_hash if user else None)
    if not ok or user is None or not user.is_active:
        raise Unauthorized("Invalid email or password")  # same message for every failure
    token, expires_in = create_access_token(user.id)
    return TokenOut(access_token=token, expires_in=expires_in)
