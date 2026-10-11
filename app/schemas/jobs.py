from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.validation import reject_control_chars


class JobCreate(BaseModel):
    """Request-level schema. Recipients stay loosely typed on purpose: each row is
    validated separately so one bad row never rejects the whole request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_name: str = Field(min_length=1, max_length=150)
    issuer_name: str = Field(min_length=1, max_length=150)
    issued_on: date
    recipients: list[dict[str, Any] | Any] = Field(min_length=1)

    @field_validator("event_name", "issuer_name")
    @classmethod
    def _no_control_chars(cls, v: str) -> str:
        return reject_control_chars(v)


class JobCreated(BaseModel):
    job_id: UUID
    status: str
    total: int
    accepted: int
    rejected: int
    status_url: str


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    status: str
    event_name: str
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    download_url: str | None


class RetryOut(BaseModel):
    job_id: UUID
    requeued: int
