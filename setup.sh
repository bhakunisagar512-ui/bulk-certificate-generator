#!/usr/bin/env bash
# One-command setup for the Bulk Certificate Generator.
#
#   ./setup.sh            build and start everything with Docker (default)
#   ./setup.sh docker     same as above
#   ./setup.sh local      create a virtualenv, install dependencies, run the tests
#   ./setup.sh test       run the tests in the existing virtualenv
#   ./setup.sh down       stop the Docker stack (data is kept)
#   ./setup.sh reset      stop the stack AND delete its volumes (destroys data)
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-docker}"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed."; }

random_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-48
  else python3 -c 'import secrets; print(secrets.token_urlsafe(36))'; fi
}

set_var() {  # set_var KEY VALUE  -> replaces KEY=... in .env
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' .env > "$tmp"
  cat "$tmp" > .env && rm -f "$tmp"
}

ensure_env() {
  if [ -f .env ]; then
    log ".env already exists, keeping it"
    return
  fi
  [ -f .env.example ] || die ".env.example is missing"
  log "Creating .env with freshly generated secrets"
  cp .env.example .env
  chmod 600 .env
  local pg_pass jwt
  pg_pass="$(random_secret)"
  jwt="$(random_secret)$(random_secret)"
  set_var POSTGRES_PASSWORD "$pg_pass"
  set_var JWT_SECRET "$jwt"
  set_var DATABASE_URL "postgresql+psycopg://certgen:${pg_pass}@localhost:5432/certgen"
}

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then docker-compose "$@"
  else die "Docker Compose is not installed."; fi
}

case "$MODE" in
  docker)
    need docker
    docker info >/dev/null 2>&1 || die "Docker daemon is not running."
    ensure_env
    log "Building and starting containers (first build can take a few minutes)"
    compose up -d --build
    port="$(grep -E '^API_PORT=' .env | cut -d= -f2 || true)"; port="${port:-8000}"
    log "Waiting for the API to become healthy"
    for _ in $(seq 1 60); do
      if curl -fsS "http://localhost:${port}/health" >/dev/null 2>&1; then
        log "Ready: http://localhost:${port}/docs"
        compose ps
        exit 0
      fi
      sleep 2
    done
    warn "API did not become healthy in time. Recent logs:"
    compose logs --tail=50 api worker || true
    exit 1
    ;;

  dev)
    need python3
    [ -d .venv ] || { log "Creating virtualenv"; python3 -m venv .venv; }
    # shellcheck disable=SC1091
    . .venv/bin/activate
    log "Installing dependencies"
    pip install -q --upgrade pip
        pip install -q -r requirements-dev.txt
    # Quick-start settings: SQLite file + Celery running tasks inline, so Postgres and Redis are not needed.
    # (Certificates are generated inside the request here; use ./setup.sh docker for the real async setup.)
    mkdir -p storage
    export ENVIRONMENT=development
    export DATABASE_URL="sqlite:///$(pwd)/storage/dev.db"
    export STORAGE_DIR="$(pwd)/storage/certificates"
    export CELERY_ALWAYS_EAGER=true
    export RATE_LIMIT_ENABLED=false
    export JWT_SECRET="${JWT_SECRET:-$(random_secret)$(random_secret)}"
    log "Applying database migrations"
    python -m alembic upgrade head
    log "Starting API on http://localhost:${API_PORT:-8000}/docs  (Ctrl+C to stop)"
    log "In another terminal run: python scripts/demo_client.py"
    exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${API_PORT:-8000}" --reload
    ;;

  local)
    need python3
    [ -d .venv ] || { log "Creating virtualenv"; python3 -m venv .venv; }
    # shellcheck disable=SC1091
    . .venv/bin/activate
    log "Installing dependencies"
    pip install -q --upgrade pip
    pip install -q -r requirements-dev.txt
    log "Running tests"
    python -m pytest
    log "Done. Activate with: source .venv/bin/activate"
    ;;
  test)
    [ -d .venv ] || die "No .venv found. Run ./setup.sh local first."
    # shellcheck disable=SC1091
    . .venv/bin/activate
    python -m pytest
    ;;
  down)
    need docker
    compose down
    ;;
  reset)
    need docker
    read -r -p "This deletes the database and all generated PDFs. Continue? [y/N] " ans
    [ "${ans:-N}" = "y" ] || { log "Aborted"; exit 0; }
    compose down -v
    ;;
  *)
    die "Unknown mode '$MODE'. Use: docker | dev | local | test | down | reset"
    ;;
esac
