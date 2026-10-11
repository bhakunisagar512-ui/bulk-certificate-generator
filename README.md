# Bulk Certificate Generator API

Submit a list of recipients and get one PDF certificate per recipient. Jobs run in the
background, every certificate has its own status, one failure never blocks the others, and
results can be downloaded one by one or as a ZIP.

**Stack:** FastAPI, PostgreSQL, Celery + Redis, ReportLab, JWT authentication, Docker.

---

## Quick start (Docker)

```bash
./setup.sh
```

That creates `.env` with freshly generated secrets, builds the images, runs the database
migrations and starts the API, worker, scheduler, PostgreSQL and Redis.
Then open **http://localhost:8000/docs**.

### Run without Docker (quickest way to try it)

```bash
./setup.sh dev          # SQLite + tasks run inline: no Postgres or Redis needed
```

This is for trying the API only: certificates are generated inside the request instead of by a
background worker. Use `./setup.sh` (Docker) for the real asynchronous setup.

### Demo script

With the API running (either mode), in a second terminal:

```bash
python scripts/demo_client.py
```

It registers a user, logs in, submits a sample bulk job (one row is invalid on purpose), polls the status,
lists the items, downloads one PDF and the ZIP into `demo_output/`, and verifies a certificate, printing every
request and response. It uses only the standard library. Options: `--base-url`, `--email`, `--password`,
`--out`, `--no-download`.

Other commands:

| Command | What it does |
|---|---|
| `./setup.sh local` | virtualenv + dependencies + run the test-suite (no Docker needed) |
| `./setup.sh test` | run the tests again in the existing virtualenv |
| `./setup.sh down` | stop the stack, keep the data |
| `./setup.sh reset` | stop the stack and delete database and PDFs |

Requirements: Docker with Compose v2 (for the stack), Python 3.12 (for `local`).

## Try it

```bash
BASE=http://localhost:8000

# 1. create an account and log in (JWT)
curl -s $BASE/api/v1/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"S3cure-pass!"}'
TOKEN=$(curl -s $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"S3cure-pass!"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 2. submit a bulk job (one row is deliberately invalid)
curl -s $BASE/api/v1/certificate-jobs -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-1' -d '{
    "event_name": "Python Bootcamp 2026",
    "issuer_name": "Acme Academy",
    "issued_on": "2026-10-09",
    "recipients": [
      {"name": "Asha Verma", "email": "asha@example.com", "course": "Backend Engineering", "grade": "A"},
      {"name": "", "email": "not-an-email", "course": "Backend Engineering"},
      {"name": "Ravi Kumar", "course": "Backend Engineering"}
    ]}'
# -> 202 {"job_id": "...", "accepted": 2, "rejected": 1, ...}

# 3. poll status, list items, download
JOB=<job_id from above>
curl -s $BASE/api/v1/certificate-jobs/$JOB -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/certificate-jobs/$JOB/certificates?status=FAILED" -H "Authorization: Bearer $TOKEN"
curl -s -o certificates.zip $BASE/api/v1/certificate-jobs/$JOB/download -H "Authorization: Bearer $TOKEN"
```

## API

All `/api/v1` endpoints except `auth/*` need `Authorization: Bearer <token>`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/auth/register` | create a user (201) |
| POST | `/api/v1/auth/login` | returns a JWT access token |
| POST | `/api/v1/certificate-jobs` | create a bulk job (202); optional `Idempotency-Key` header |
| GET | `/api/v1/certificate-jobs/{id}` | status, counts, progress |
| GET | `/api/v1/certificate-jobs/{id}/certificates` | items; `?status=`, `?page=`, `?page_size=` |
| GET | `/api/v1/certificate-jobs/{id}/download` | ZIP of succeeded PDFs + `summary.csv` |
| POST | `/api/v1/certificate-jobs/{id}/retry-failed` | re-queue retryable failures |
| GET | `/api/v1/certificates/{id}/download` | one PDF |
| GET | `/verify/{code}` | public authenticity check (no auth) |
| GET | `/health`, `/health/live` | readiness (DB + Redis) / liveness |

Errors always look like `{"error": {"code", "message", "details"}}`.
Status codes: 202 accepted, 401 bad/missing token, 404 not found *or not yours*, 409 conflict
(not ready, idempotency mismatch), 413 body too large, 422 validation, 429 rate limited.

## How it works

```
client -> FastAPI -> PostgreSQL (job + one row per recipient)
              \-> Redis queue -> Celery worker(s) -> PDF -> storage volume
```

1. `POST /certificate-jobs` validates the request, stores the job and **one row per recipient in
   a single transaction**, enqueues one task per valid row, and returns `202` immediately.
2. Invalid rows are stored as `FAILED / VALIDATION_ERROR` with the reason; they do not reject the request.
3. A worker atomically claims a row (`PENDING -> PROCESSING`), renders the PDF, stores it, marks the row
   `SUCCEEDED` (or `FAILED`) and increments the job counters. The last item finalises the job as
   `COMPLETED`, `COMPLETED_WITH_ERRORS` or `FAILED`.
4. Clients poll the job and download results.

### Design decisions

- **Asynchronous, not synchronous.** A request can hold up to 1000 recipients; rendering inside the request
  would hold a web worker for a long time and risk gateway timeouts. A queue gives instant responses,
  independent scaling, retries and natural per-item status. The price is Redis plus a worker, which Compose hides.
- **Celery + Redis over `BackgroundTasks`.** Background tasks die with the process and cannot retry.
- **One task per certificate.** Failure isolation, parallelism, and single-item retry.
- **State lives in PostgreSQL, not in the queue.** The queue only carries ids, so a lost or duplicated
  message cannot corrupt state.
- **Compare-and-set transitions.** Every state change is a conditional `UPDATE`, so duplicate delivery,
  concurrent workers and retries cannot double-count. Counters live on the job row (O(1) progress reads).
- **Request vs row validation.** Structural problems (missing fields, too many recipients) return 422;
  bad recipients are recorded per row so 999 good ones are not rejected for one bad one.
- **Storage behind an interface** (`LocalStorage` today, S3 later). Files are written atomically.
- **Migrations, not `create_all`.** The schema is defined by Alembic; `db/schema.sql` is generated from it.

### Failure handling

| Failure | Behaviour |
|---|---|
| Bad recipient data | row `FAILED` (`VALIDATION_ERROR`), job continues |
| Render bug for one item | row `FAILED` (`RENDER_ERROR`), others unaffected; retry with `retry-failed` |
| Disk/storage error | retried 3 times with backoff, then `FAILED` (`STORAGE_ERROR`) |
| Worker dies mid-task | message is redelivered (`acks_late`); a scheduler sweeps rows stuck in `PROCESSING` |
| Redis down while submitting | request still succeeds; rows stay `PENDING` and the sweeper re-queues them |
| Client retries a request | same `Idempotency-Key` returns the original job (`200`, `Idempotent-Replay: true`) |

## Security

- **JWT (HS256, algorithm allow-list).** Signature, `exp`, `iat`, `sub` and a token-type claim are required;
  `alg=none` and wrong-secret tokens are rejected. Secret must be at least 32 characters; placeholder
  secrets are refused in production.
- **Passwords:** bcrypt (cost 12), constant-time-ish login, same error for unknown user and wrong password.
- **Ownership on every query.** Other users' jobs and PDFs return 404, never 403, and never leak existence.
- **Input hardening:** strict schemas (`extra=forbid`), length limits, control characters rejected, request
  body size cap, recipients cap, parameterised SQL only.
- **PDF safety:** text is drawn with the canvas API, so markup in names is never interpreted.
- **File safety:** storage keys are server-generated UUIDs; the storage layer blocks path traversal;
  downloads are streamed through authenticated endpoints with sanitised filenames and `no-store`.
- **CSV injection:** cells beginning with `= + - @` are neutralised in `summary.csv`.
- **Rate limiting** (Redis) on login/register, job creation and verification.
- **Secrets only from the environment.** `.env` is git-ignored and created with mode 600; Compose refuses to
  start without required secrets; Postgres and Redis are not published to the host.
- **Containers:** non-root user, `no-new-privileges`, health checks.
- **Headers:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, HSTS in production, restrictive CORS (off by default).
- **Errors:** unexpected exceptions return a generic 500; details go to structured logs (no names/emails logged).

**Known limitations (be upfront about these in review):**
JWTs are stateless: there is no refresh token or revocation list, so a token is valid until it expires
(default 60 min). Registration is open (switch off with `ALLOW_REGISTRATION=false`) and has no email
verification. Redis has no password and relies on the private Docker network. TLS must be terminated by a
reverse proxy. Local-disk storage means one shared volume (use S3 for multi-host). Rate limiting keys on the
direct peer address.

## Configuration

Everything comes from environment variables (see `.env.example`):

| Variable | Purpose | Default |
|---|---|---|
| `POSTGRES_USER/PASSWORD/DB` | database credentials (Compose builds `DATABASE_URL`) | generated by `setup.sh` |
| `JWT_SECRET` | signing key, 32+ chars | generated by `setup.sh` |
| `JWT_EXPIRE_MINUTES` | token lifetime | 60 |
| `MAX_RECIPIENTS` / `MAX_BODY_BYTES` | request limits | 1000 / 2 MB |
| `RATE_LIMIT_*` | per-minute limits | 20 jobs, 10 logins, 60 verifies |
| `RETENTION_DAYS` | delete finished jobs and files after N days (0 = off) | 30 |
| `STUCK_TIMEOUT_SECONDS` | when the sweeper rescues stuck items | 300 |
| `ALLOW_REGISTRATION`, `ENABLE_DOCS`, `CORS_ORIGINS`, `PUBLIC_BASE_URL`, `API_PORT` | misc | see file |

## Database

`app/db/migrations/versions/0001_initial.py` is the source of truth and runs automatically when the API
container starts. `db/schema.sql` is the same schema as plain PostgreSQL DDL, regenerated with
`scripts/generate_schema_sql.sh`. Tables: `users`, `jobs`, `certificates`, with CHECK constraints on
statuses, foreign keys with cascade, a unique `(user_id, idempotency_key)`, and indexes for status lookups
and the sweeper. A test verifies that the migration and the ORM models never drift apart.

## Tests

```bash
| `./setup.sh dev` | run the API without Docker (SQLite, tasks inline) |
./setup.sh local        # or: pytest

```

Tests run on SQLite with Celery in eager mode, so they need no Postgres or Redis. They cover job creation,
validation, rendering, status and progress, failure isolation, retries, sweeper recovery, downloads, JWT
attacks, cross-user access, injection strings, path traversal, rate limiting and migrations.

## Project layout

```
app/
  main.py            app factory, middleware (request id, security headers, body limit)
  config.py          settings from environment
  api/               routes: auth, jobs, certificates, public (verify, health); deps (JWT user)
  core/              security (bcrypt, JWT), errors, logging, rate limiting
  db/                models, session, Alembic migrations
  schemas/           Pydantic request/response models
  services/          job_service, validation, processing (state machine), renderer, storage, export
  worker/            celery_app, tasks (generate, sweeper, retention cleanup)
db/schema.sql        generated PostgreSQL DDL
tests/               pytest suite
```

## Future scope

Multiple templates, email delivery, S3 storage with pre-signed URLs, refresh tokens and revocation,
webhooks on job completion, CSV upload for very large lists, metrics and tracing.
