BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE users (
    id UUID NOT NULL, 
    email VARCHAR(254) NOT NULL, 
    password_hash VARCHAR(100) NOT NULL, 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE TABLE jobs (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    status VARCHAR(30) DEFAULT 'QUEUED' NOT NULL, 
    event_name VARCHAR(150) NOT NULL, 
    issuer_name VARCHAR(150) NOT NULL, 
    issued_on DATE NOT NULL, 
    total INTEGER NOT NULL, 
    succeeded_count INTEGER DEFAULT '0' NOT NULL, 
    failed_count INTEGER DEFAULT '0' NOT NULL, 
    idempotency_key VARCHAR(100), 
    request_hash VARCHAR(64), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT fk_jobs_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT ck_jobs_status CHECK (status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED')), 
    CONSTRAINT ck_jobs_counts_nonneg CHECK (total >= 0 AND succeeded_count >= 0 AND failed_count >= 0), 
    CONSTRAINT uq_jobs_user_idempotency UNIQUE (user_id, idempotency_key)
);

CREATE INDEX ix_jobs_user_created ON jobs (user_id, created_at);

CREATE TABLE certificates (
    id UUID NOT NULL, 
    job_id UUID NOT NULL, 
    row_index INTEGER NOT NULL, 
    recipient_name VARCHAR(100), 
    recipient_email VARCHAR(254), 
    extra JSONB, 
    status VARCHAR(20) DEFAULT 'PENDING' NOT NULL, 
    error_code VARCHAR(40), 
    error_message TEXT, 
    file_path VARCHAR(300), 
    file_size BIGINT, 
    checksum_sha256 VARCHAR(64), 
    verification_code VARCHAR(40) NOT NULL, 
    attempts INTEGER DEFAULT '0' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    generated_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT fk_certificates_job_id FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE, 
    CONSTRAINT ck_certificates_status CHECK (status IN ('PENDING', 'PROCESSING', 'SUCCEEDED', 'FAILED')), 
    CONSTRAINT uq_certificates_job_row UNIQUE (job_id, row_index), 
    CONSTRAINT uq_certificates_verification_code UNIQUE (verification_code)
);

CREATE INDEX ix_certificates_job_status ON certificates (job_id, status);

CREATE INDEX ix_certificates_status_updated ON certificates (status, updated_at);

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

COMMIT;

