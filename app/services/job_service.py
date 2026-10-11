"""Business logic for jobs: create, read, retry."""

import hashlib
import json
import logging
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError, Conflict, NotFound
from app.core.security import new_verification_code
from app.db.models import (
    RETRYABLE_ERRORS,
    Certificate,
    CertStatus,
    ErrorCode,
    Job,
    JobStatus,
    User,
    utcnow,
)
from app.schemas.jobs import JobCreate
from app.services.processing import finalize_job_if_done
from app.services.validation import safe_identity, validate_recipient

log = logging.getLogger(__name__)


def _request_hash(payload: JobCreate) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def enqueue_certificates(cert_ids: list[uuid.UUID]) -> None:
    """Hand items to the queue. A broker outage must not fail the request: the items stay
    PENDING and the periodic sweeper re-offers them."""
    from app.worker.tasks import generate_certificate  # local import avoids a cycle

    for cert_id in cert_ids:
        try:
            generate_certificate.delay(str(cert_id))
        except Exception:
            log.exception("enqueue failed; sweeper will retry", extra={"certificate_id": str(cert_id)})
            break


def count_rejected(db: Session, job_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Certificate)
            .where(Certificate.job_id == job_id, Certificate.error_code == ErrorCode.VALIDATION_ERROR.value)
        )
        or 0
    )


def create_job(db: Session, user: User, payload: JobCreate, idempotency_key: str | None) -> tuple[Job, bool]:
    settings = get_settings()
    if len(payload.recipients) > settings.max_recipients:
        raise AppError(
            f"A job may contain at most {settings.max_recipients} recipients", code="VALIDATION_ERROR", status_code=422
        )

    request_hash = _request_hash(payload)
    if idempotency_key:
        existing = db.scalar(select(Job).where(Job.user_id == user.id, Job.idempotency_key == idempotency_key))
        if existing:
            if existing.request_hash != request_hash:
                raise Conflict("Idempotency-Key was already used with a different payload", code="IDEMPOTENCY_CONFLICT")
            return existing, False

    job = Job(
        id=uuid.uuid4(),
        user_id=user.id,
        status=JobStatus.QUEUED.value,
        event_name=payload.event_name,
        issuer_name=payload.issuer_name,
        issued_on=payload.issued_on,
        total=len(payload.recipients),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    certs: list[Certificate] = []
    rejected = 0
    for index, raw in enumerate(payload.recipients):
        result = validate_recipient(raw)
        cert = Certificate(
            id=uuid.uuid4(),
            job_id=job.id,
            row_index=index,
            verification_code=new_verification_code(),
        )
        if result.ok:
            cert.status = CertStatus.PENDING.value
            cert.recipient_name = result.data["name"]
            cert.recipient_email = result.data["email"]
            cert.extra = result.data["extra"]
        else:
            rejected += 1
            cert.status = CertStatus.FAILED.value
            cert.error_code = ErrorCode.VALIDATION_ERROR.value
            cert.error_message = result.message
            cert.recipient_name, cert.recipient_email = safe_identity(raw)
        certs.append(cert)
    job.failed_count = rejected
    job.certificates = certs

    try:
        db.add(job)
        db.commit()  # one transaction: job + every row, or nothing
    except IntegrityError:
        db.rollback()
        if idempotency_key:  # lost a race with an identical concurrent request
            existing = db.scalar(select(Job).where(Job.user_id == user.id, Job.idempotency_key == idempotency_key))
            if existing and existing.request_hash == request_hash:
                return existing, False
        raise

    if rejected == job.total:
        finalize_job_if_done(db, job.id)  # nothing to process: finish immediately
    else:
        enqueue_certificates([c.id for c in certs if c.status == CertStatus.PENDING.value])
    return job, True


def get_job(db: Session, user: User, job_id: uuid.UUID) -> Job:
    """Ownership is part of the query: other users' jobs are indistinguishable from missing ones."""
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    if job is None:
        raise NotFound("Job not found")
    return job


def get_certificate(db: Session, user: User, cert_id: uuid.UUID) -> Certificate:
    cert = db.scalar(
        select(Certificate)
        .join(Job, Job.id == Certificate.job_id)
        .where(Certificate.id == cert_id, Job.user_id == user.id)
    )
    if cert is None:
        raise NotFound("Certificate not found")
    return cert


def retry_failed(db: Session, user: User, job_id: uuid.UUID) -> int:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user.id).with_for_update())
    if job is None:
        raise NotFound("Job not found")
    ids = db.scalars(
        select(Certificate.id).where(
            Certificate.job_id == job.id,
            Certificate.status == CertStatus.FAILED.value,
            Certificate.error_code.in_(RETRYABLE_ERRORS),
        )
    ).all()
    if not ids:
        db.rollback()
        return 0

    db.execute(
        update(Certificate)
        .where(Certificate.id.in_(ids))
        .values(status=CertStatus.PENDING.value, error_code=None, error_message=None, attempts=0, updated_at=utcnow())
    )
    db.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(failed_count=Job.failed_count - len(ids), status=JobStatus.PROCESSING.value, finished_at=None)
    )
    db.commit()
    enqueue_certificates(list(ids))
    return len(ids)
