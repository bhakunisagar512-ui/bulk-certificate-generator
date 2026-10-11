"""Application settings. Every credential and tunable is read from the environment."""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_SECRETS = {"change-me", "change-me-to-a-long-random-string-of-at-least-32-chars"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    environment: str = "development"

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    allow_registration: bool = True

    storage_dir: str = "./storage/certificates"
    public_base_url: str = "http://localhost:8000"

    max_recipients: int = 1000
    max_body_bytes: int = 2 * 1024 * 1024

    rate_limit_enabled: bool = True
    rate_limit_jobs_per_minute: int = 20
    rate_limit_login_per_minute: int = 10
    rate_limit_verify_per_minute: int = 60

    retention_days: int = 30
    stuck_timeout_seconds: int = 300
    max_attempts: int = 5

    cors_origins: str = ""
    enable_docs: bool = True

    celery_always_eager: bool = False

    @field_validator("jwt_algorithm")
    @classmethod
    def _only_hmac(cls, v: str) -> str:
        # Pin to HMAC algorithms; never allow "none" or asymmetric confusion.
        if v not in {"HS256", "HS384", "HS512"}:
            raise ValueError("JWT_ALGORITHM must be HS256, HS384 or HS512")
        return v

    @field_validator("jwt_secret")
    @classmethod
    def _secret_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return v

    @model_validator(mode="after")
    def _no_placeholder_in_production(self) -> "Settings":
        if self.environment == "production" and self.jwt_secret in _PLACEHOLDER_SECRETS:
            raise ValueError("Set a real JWT_SECRET before running in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
