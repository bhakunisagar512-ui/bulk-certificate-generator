"""Row-level recipient validation. A bad row is reported, never raised to the request level."""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator

CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def reject_control_chars(value: str | None) -> str | None:
    if value is not None and CONTROL_CHARS.search(value):
        raise ValueError("must not contain control characters")
    return value


class RecipientIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr | None = None
    course: str = Field(min_length=1, max_length=150)
    grade: str | None = Field(default=None, max_length=20)

    @field_validator("email", "grade", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("name", "course", "grade")
    @classmethod
    def _no_control_chars(cls, v: str | None) -> str | None:
        return reject_control_chars(v)


@dataclass
class ValidationResult:
    ok: bool
    data: dict[str, Any] | None = None
    message: str | None = None


def _safe_str(value: Any, limit: int) -> str | None:
    """Sanitised copy of untrusted input, safe to store on a rejected row."""
    if not isinstance(value, str):
        return None
    return CONTROL_CHARS.sub("", value).strip()[:limit] or None


def safe_identity(raw: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw, dict):
        return None, None
    return _safe_str(raw.get("name"), 100), _safe_str(raw.get("email"), 254)


def validate_recipient(raw: Any) -> ValidationResult:
    if not isinstance(raw, dict):
        return ValidationResult(False, message="recipient must be an object")
    try:
        parsed = RecipientIn.model_validate(raw)
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "recipient"
            parts.append(f"{field}: {err['msg']}")
        return ValidationResult(False, message="; ".join(parts)[:1000])
    return ValidationResult(
        True,
        data={
            "name": parsed.name,
            "email": str(parsed.email).lower() if parsed.email else None,
            "extra": {"course": parsed.course, "grade": parsed.grade},
        },
    )
