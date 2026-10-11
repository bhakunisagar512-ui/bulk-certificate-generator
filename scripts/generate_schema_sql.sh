#!/usr/bin/env bash
# Regenerates db/schema.sql (PostgreSQL DDL) from the Alembic migrations, offline.
# Keeps the SQL script and the migrations from ever drifting apart.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p db
DATABASE_URL="postgresql+psycopg://x:x@localhost/x" \
JWT_SECRET="schema-generation-only-schema-generation-only" \
python -m alembic upgrade head --sql 2>/dev/null > db/schema.sql
echo "wrote db/schema.sql"
