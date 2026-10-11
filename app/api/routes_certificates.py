import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession
from app.core.errors import Conflict, NotFound
from app.db.models import CertStatus
from app.services import job_service
from app.services.export import iter_file, safe_filename
from app.services.storage import get_storage

router = APIRouter(prefix="/api/v1/certificates", tags=["certificates"])


@router.get("/{certificate_id}/download")
def download_certificate(certificate_id: uuid.UUID, db: DbSession, user: CurrentUser):
    cert = job_service.get_certificate(db, user, certificate_id)
    if cert.status != CertStatus.SUCCEEDED.value or not cert.file_path:
        raise Conflict("Certificate is not available", code="NOT_READY")
    try:
        fh = get_storage().open(cert.file_path)
    except FileNotFoundError:
        raise NotFound("Certificate file is no longer available") from None
    filename = f"{safe_filename(cert.recipient_name)}_{cert.row_index:04d}.pdf"
    return StreamingResponse(
        iter_file(fh),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store"},
    )
