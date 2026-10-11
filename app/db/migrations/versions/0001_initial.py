"""initial schema: users, jobs, certificates

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
JOB_STATUSES = "'QUEUED', 'PROCESSING', 'COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED'"
CERT_STATUSES = "'PENDING', 'PROCESSING', 'SUCCEEDED', 'FAILED'"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="QUEUED"),
        sa.Column("event_name", sa.String(150), nullable=False),
        sa.Column("issuer_name", sa.String(150), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(100)),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_jobs_user_id", ondelete="CASCADE"),
        sa.CheckConstraint(f"status IN ({JOB_STATUSES})", name="ck_jobs_status"),
        sa.CheckConstraint("total >= 0 AND succeeded_count >= 0 AND failed_count >= 0", name="ck_jobs_counts_nonneg"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_jobs_user_idempotency"),
    )
    op.create_index("ix_jobs_user_created", "jobs", ["user_id", "created_at"])

    op.create_table(
        "certificates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("recipient_name", sa.String(100)),
        sa.Column("recipient_email", sa.String(254)),
        sa.Column("extra", JSON_TYPE),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("error_code", sa.String(40)),
        sa.Column("error_message", sa.Text()),
        sa.Column("file_path", sa.String(300)),
        sa.Column("file_size", sa.BigInteger()),
        sa.Column("checksum_sha256", sa.String(64)),
        sa.Column("verification_code", sa.String(40), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_certificates_job_id", ondelete="CASCADE"),
        sa.CheckConstraint(f"status IN ({CERT_STATUSES})", name="ck_certificates_status"),
        sa.UniqueConstraint("job_id", "row_index", name="uq_certificates_job_row"),
        sa.UniqueConstraint("verification_code", name="uq_certificates_verification_code"),
    )
    op.create_index("ix_certificates_job_status", "certificates", ["job_id", "status"])
    op.create_index("ix_certificates_status_updated", "certificates", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_certificates_status_updated", table_name="certificates")
    op.drop_index("ix_certificates_job_status", table_name="certificates")
    op.drop_table("certificates")
    op.drop_index("ix_jobs_user_created", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("users")
