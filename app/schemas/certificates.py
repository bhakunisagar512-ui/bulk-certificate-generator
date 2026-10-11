from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CertificateOut(BaseModel):
    id: UUID
    index: int
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    verification_code: str | None = None
    generated_at: datetime | None = None
    download_url: str | None = None


class CertificateList(BaseModel):
    items: list[CertificateOut]
    page: int
    page_size: int
    total: int


class VerificationOut(BaseModel):
    valid: bool
    recipient_name: str | None
    course: str | None
    event_name: str
    issuer_name: str
    issued_on: str
