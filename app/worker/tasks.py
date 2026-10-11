"""Celery tasks. State lives in PostgreSQL; the queue only carries certificate ids."""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Certificate, ErrorCode, Job, JobStatus, utcnow
from app.db.session import SessionLocal
from app.services import processing
from app.services.exceptions import StorageError
from app.services.renderer import CertificateData, get_renderer
from app.services.storage import get_storage, storage_key
from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)


def _build_data(cert: Certificate, job: Job) -> CertificateData:
    s = get_settings()
    extra = cert.extra or {}
    return CertificateData(
        recipient_name=cert.recipient_name or "",
        course=extra.get("course", ""),
        grade=extra.get("grade"),
        event_name=job.event_name,
        issuer_name=job.issuer_name,
        issued_on=job.issued_on,
        verification_code=cert.verification_code,
        verify_url=f"{s.public_base_url.rstrip('/')}/verify/{cert.verification_code}",
    )


@celery_app.task(name="certgen.generate_certificate", bind=True, max_retries=3)
def generate_certificate(self, certificate_id: str) -> str:
    cert_id = uuid.UUID(certificate_id)
    with SessionLocal() as db:
        cert = processing.claim_certificate(db, cert_id)
        if cert is None:
            return "skipped"  # already handled by another worker: duplicate delivery is harmless
        job_id = cert.job_id
        job = db.get(Job, job_id)

        try:  # 1) render: a failure here is permanent for this item only
            pdf = get_renderer().render(_build_data(cert, job))
        except Exception:
            log.exception("render failed", extra={"certificate_id": certificate_id})
            processing.mark_failed(
                db, cert_id, job_id, ErrorCode.RENDER_ERROR.value, "Could not render the certificate"
            )
            processing.finalize_job_if_done(db, job_id)
            return "failed"

        try:  # 2) store: IO problems are transient and retried with backoff
            stored = get_storage().save(storage_key(job_id, cert_id), pdf)
        except StorageError as exc:
            if self.request.retries < self.max_retries:
                processing.reset_to_pending(db, cert_id)
                raise self.retry(exc=exc, countdown=min(5 * 2**self.request.retries, 120)) from exc
            log.error("storage failed permanently", extra={"certificate_id": certificate_id})
            processing.mark_failed(
                db, cert_id, job_id, ErrorCode.STORAGE_ERROR.value, "Could not store the certificate file"
            )
            processing.finalize_job_if_done(db, job_id)
            return "failed"

        processing.mark_succeeded(db, cert_id, job_id, stored.key, stored.size, stored.sha256)
        processing.finalize_job_if_done(db, job_id)
        return "ok"


@celery_app.task(name="certgen.recover_stuck")
def recover_stuck() -> int:
    from app.services.job_service import enqueue_certificates

    s = get_settings()
    with SessionLocal() as db:
        ids = processing.recover_stuck(db, timeout_seconds=s.stuck_timeout_seconds, max_attempts=s.max_attempts)
    if ids:
        log.warning("re-enqueued stuck certificates", extra={"count": len(ids)})
        enqueue_certificates(ids)
    return len(ids)


@celery_app.task(name="certgen.cleanup_old_jobs")
def cleanup_old_jobs(batch: int = 100) -> int:
    """Retention: delete finished jobs (rows and files) older than RETENTION_DAYS."""
    s = get_settings()
    if s.retention_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=s.retention_days)
    storage = get_storage()
    removed = 0
    with SessionLocal() as db:
        jobs = db.scalars(
            select(Job)
            .where(
                Job.created_at < cutoff,
                Job.status.in_(
                    [JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_ERRORS.value, JobStatus.FAILED.value]
                ),
            )
            .limit(batch)
        ).all()
        for job in jobs:
            for path in db.scalars(
                select(Certificate.file_path).where(Certificate.job_id == job.id, Certificate.file_path.is_not(None))
            ):
                try:
                    storage.delete(path)
                except OSError:
                    log.warning("could not delete file", extra={"job_id": str(job.id)})
            db.delete(job)
            removed += 1
        db.commit()
    return removed
