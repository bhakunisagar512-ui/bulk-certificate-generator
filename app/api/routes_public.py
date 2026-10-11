import logging

import redis
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from app.api.deps import DbSession
from app.config import get_settings
from app.core.errors import NotFound
from app.core.ratelimit import get_redis, limit_by_ip
from app.db.models import Certificate, CertStatus, Job
from app.schemas.certificates import VerificationOut

log = logging.getLogger(__name__)
router = APIRouter(tags=["public"])

_verify_limit = limit_by_ip("verify", lambda: get_settings().rate_limit_verify_per_minute)


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health")
def health(db: DbSession):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        log.exception("database health check failed")
        checks["database"] = "error"
    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except redis.RedisError:
        checks["redis"] = "error"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks}, status_code=200 if healthy else 503
    )


@router.get("/verify/{verification_code}", response_model=VerificationOut, dependencies=[Depends(_verify_limit)])
def verify(verification_code: str, db: DbSession):
    """Public authenticity check. Exposes the minimum: never the email or internal ids."""
    if len(verification_code) > 40:
        raise NotFound("Certificate not found")
    row = db.execute(
        select(Certificate, Job)
        .join(Job, Job.id == Certificate.job_id)
        .where(Certificate.verification_code == verification_code, Certificate.status == CertStatus.SUCCEEDED.value)
    ).first()
    if row is None:
        raise NotFound("Certificate not found")
    cert, job = row
    return VerificationOut(
        valid=True,
        recipient_name=cert.recipient_name,
        course=(cert.extra or {}).get("course"),
        event_name=job.event_name,
        issuer_name=job.issuer_name,
        issued_on=job.issued_on.isoformat(),
    )
