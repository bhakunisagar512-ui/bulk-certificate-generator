"""State transitions for certificates and jobs.

Every transition is a conditional UPDATE (compare-and-set), so duplicate task delivery,
worker crashes and concurrent workers can never double-count or skip a state.
"""

import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Certificate, CertStatus, Job, JobStatus, utcnow

OPEN_JOB_STATES = (JobStatus.QUEUED.value, JobStatus.PROCESSING.value)


def claim_certificate(db: Session, cert_id: uuid.UUID) -> Certificate | None:
    """PENDING -> PROCESSING. Returns None if another worker already owns it."""
    res = db.execute(
        update(Certificate)
        .where(Certificate.id == cert_id, Certificate.status == CertStatus.PENDING.value)
        .values(status=CertStatus.PROCESSING.value, attempts=Certificate.attempts + 1, updated_at=utcnow())
    )
    if res.rowcount != 1:
        db.rollback()
        return None
    cert = db.get(Certificate, cert_id)
    db.execute(
        update(Job)
        .where(Job.id == cert.job_id, Job.status == JobStatus.QUEUED.value)
        .values(status=JobStatus.PROCESSING.value, started_at=utcnow())
    )
    db.commit()
    return cert


def mark_succeeded(db: Session, cert_id: uuid.UUID, job_id: uuid.UUID, file_path: str, size: int, sha256: str) -> bool:
    res = db.execute(
        update(Certificate)
        .where(Certificate.id == cert_id, Certificate.status == CertStatus.PROCESSING.value)
        .values(
            status=CertStatus.SUCCEEDED.value,
            file_path=file_path,
            file_size=size,
            checksum_sha256=sha256,
            error_code=None,
            error_message=None,
            generated_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    changed = res.rowcount == 1
    if changed:
        db.execute(update(Job).where(Job.id == job_id).values(succeeded_count=Job.succeeded_count + 1))
    db.commit()
    return changed


def mark_failed(db: Session, cert_id: uuid.UUID, job_id: uuid.UUID, error_code: str, message: str) -> bool:
    res = db.execute(
        update(Certificate)
        .where(Certificate.id == cert_id, Certificate.status == CertStatus.PROCESSING.value)
        .values(
            status=CertStatus.FAILED.value, error_code=error_code, error_message=message[:1000], updated_at=utcnow()
        )
    )
    changed = res.rowcount == 1
    if changed:
        db.execute(update(Job).where(Job.id == job_id).values(failed_count=Job.failed_count + 1))
    db.commit()
    return changed


def reset_to_pending(db: Session, cert_id: uuid.UUID) -> None:
    db.execute(
        update(Certificate)
        .where(Certificate.id == cert_id, Certificate.status == CertStatus.PROCESSING.value)
        .values(status=CertStatus.PENDING.value, updated_at=utcnow())
    )
    db.commit()


def finalize_job_if_done(db: Session, job_id: uuid.UUID) -> bool:
    """Move the job to its final status exactly once, when every item is accounted for."""
    row = db.execute(
        select(Job.total, Job.succeeded_count, Job.failed_count, Job.status).where(Job.id == job_id)
    ).one_or_none()
    if row is None:
        return False
    total, ok, failed, status = row
    if status not in OPEN_JOB_STATES or ok + failed < total:
        return False
    if failed == 0:
        final = JobStatus.COMPLETED
    elif ok == 0:
        final = JobStatus.FAILED
    else:
        final = JobStatus.COMPLETED_WITH_ERRORS
    res = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_(OPEN_JOB_STATES))
        .values(status=final.value, finished_at=utcnow())
    )
    db.commit()
    return res.rowcount == 1


def recover_stuck(db: Session, *, timeout_seconds: int, max_attempts: int, batch: int = 500) -> list[uuid.UUID]:
    """Sweeper: rescue items whose worker died, and re-offer items that were never picked up.

    Returns certificate ids that must be (re)enqueued.
    """
    cutoff = utcnow() - timedelta(seconds=timeout_seconds)
    to_enqueue: list[uuid.UUID] = []
    touched_jobs: set[uuid.UUID] = set()

    stuck = db.execute(
        select(Certificate.id, Certificate.job_id, Certificate.attempts)
        .where(Certificate.status == CertStatus.PROCESSING.value, Certificate.updated_at < cutoff)
        .limit(batch)
    ).all()
    for cert_id, job_id, attempts in stuck:
        if attempts >= max_attempts:
            mark_failed(db, cert_id, job_id, "STUCK_TIMEOUT", "Generation did not finish after several attempts")
            touched_jobs.add(job_id)
        else:
            res = db.execute(
                update(Certificate)
                .where(Certificate.id == cert_id, Certificate.status == CertStatus.PROCESSING.value)
                .values(status=CertStatus.PENDING.value, updated_at=utcnow())
            )
            db.commit()
            if res.rowcount == 1:
                to_enqueue.append(cert_id)

    stale = (
        db.execute(
            select(Certificate.id)
            .where(Certificate.status == CertStatus.PENDING.value, Certificate.updated_at < cutoff)
            .limit(batch)
        )
        .scalars()
        .all()
    )
    for cert_id in stale:
        res = db.execute(
            update(Certificate)
            .where(Certificate.id == cert_id, Certificate.status == CertStatus.PENDING.value)
            .values(updated_at=utcnow())  # touch so it is not re-offered every minute
        )
        db.commit()
        if res.rowcount == 1:
            to_enqueue.append(cert_id)

    for job_id in touched_jobs:
        finalize_job_if_done(db, job_id)
    return to_enqueue
