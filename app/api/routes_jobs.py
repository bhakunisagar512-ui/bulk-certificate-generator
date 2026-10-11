import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, get_idempotency_key
from app.config import get_settings
from app.core.errors import AppError, Conflict
from app.core.ratelimit import hit
from app.db.models import Certificate, CertStatus, Job
from app.schemas.certificates import CertificateList, CertificateOut
from app.schemas.jobs import JobCreate, JobCreated, JobOut, RetryOut
from app.services import job_service
from app.services.export import build_zip, iter_file, safe_filename
from app.services.storage import get_storage

router = APIRouter(prefix="/api/v1/certificate-jobs", tags=["jobs"])


def _job_out(job: Job) -> JobOut:
    return JobOut(
        job_id=job.id,
        status=job.status,
        event_name=job.event_name,
        total=job.total,
        succeeded=job.succeeded_count,
        failed=job.failed_count,
        pending=job.pending_count,
        progress_percent=job.progress_percent,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        download_url=(f"/api/v1/certificate-jobs/{job.id}/download" if job.succeeded_count > 0 else None),
    )


def _cert_out(c: Certificate) -> CertificateOut:
    ready = c.status == CertStatus.SUCCEEDED.value
    return CertificateOut(
        id=c.id,
        index=c.row_index,
        recipient_name=c.recipient_name,
        recipient_email=c.recipient_email,
        status=c.status,
        error_code=c.error_code,
        error_message=c.error_message,
        generated_at=c.generated_at,
        verification_code=c.verification_code if ready else None,
        download_url=f"/api/v1/certificates/{c.id}/download" if ready else None,
    )


@router.post("", response_model=JobCreated, status_code=202)
def create_job(
    body: JobCreate,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    idempotency_key: Annotated[str | None, Depends(get_idempotency_key)],
):
    hit("jobs", str(user.id), get_settings().rate_limit_jobs_per_minute)
    job, created = job_service.create_job(db, user, body, idempotency_key)
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    rejected = job_service.count_rejected(db, job.id)
    return JobCreated(
        job_id=job.id,
        status=job.status,
        total=job.total,
        accepted=job.total - rejected,
        rejected=rejected,
        status_url=f"/api/v1/certificate-jobs/{job.id}",
    )


@router.get("/{job_id}", response_model=JobOut)
def job_status(job_id: uuid.UUID, db: DbSession, user: CurrentUser):
    return _job_out(job_service.get_job(db, user, job_id))


@router.get("/{job_id}/certificates", response_model=CertificateList)
def list_certificates(
    job_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    status: Annotated[str | None, Query(pattern="^(PENDING|PROCESSING|SUCCEEDED|FAILED)$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
):
    job_service.get_job(db, user, job_id)
    query = select(Certificate).where(Certificate.job_id == job_id)
    if status:
        query = query.where(Certificate.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Certificate.row_index).offset((page - 1) * page_size).limit(page_size)).all()
    return CertificateList(items=[_cert_out(c) for c in rows], page=page, page_size=page_size, total=total)


@router.get("/{job_id}/download")
def download_zip(job_id: uuid.UUID, db: DbSession, user: CurrentUser):
    job = job_service.get_job(db, user, job_id)
    if job.succeeded_count == 0:
        raise Conflict("No certificates are ready for download yet", code="NOT_READY")
    archive = build_zip(db, get_storage(), job)
    return StreamingResponse(
        iter_file(archive),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="certificates_{safe_filename(job.event_name, "job")}.zip"',
            "X-Job-Status": job.status,  # PROCESSING means the ZIP holds only what is ready so far
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/{job_id}/retry-failed", response_model=RetryOut, status_code=202)
def retry_failed(job_id: uuid.UUID, db: DbSession, user: CurrentUser):
    hit("jobs", str(user.id), get_settings().rate_limit_jobs_per_minute)
    count = job_service.retry_failed(db, user, job_id)
    if count == 0:
        raise AppError("No retryable failed certificates in this job", code="NOTHING_TO_RETRY", status_code=409)
    return RetryOut(job_id=job_id, requeued=count)
