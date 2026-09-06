#!/usr/bin/env bash
# Part 0.7: creates the data-engine-dev database (default: tradingfirm_dev)
# inside the running tf-postgres container if it does not exist, then applies
# all migrations to it via scripts/migrate.sh (MIGRATE_DB override).
# Idempotent: re-running reports "exists" and "no pending migrations".
# Runs entirely via `docker exec`, same as migrate.sh — no host psql needed.
set -euo pipefail

CONTAINER="${POSTGRES_CONTAINER:-tf-postgres}"
DEV_DB="${DEV_DB:-tradingfirm_dev}"

if ! [[ "$DEV_DB" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "error: DEV_DB '$DEV_DB' must match ^[a-z_][a-z0-9_]*$" >&2
  exit 1
fi

if ! docker exec "$CONTAINER" true 2>/dev/null; then
  echo "error: container '$CONTAINER' is not running (set POSTGRES_CONTAINER to override)" >&2
  exit 1
fi

docker exec -i -e DEV_DB="$DEV_DB" "$CONTAINER" bash -s <<'EOS'
set -euo pipefail
export PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" PGDATABASE="$POSTGRES_DB"
if [ "$(psql -tAc "SELECT 1 FROM pg_database WHERE datname = '$DEV_DB'")" = "1" ]; then
  echo "exists: $DEV_DB"
else
  psql -v ON_ERROR_STOP=1 -q -c "CREATE DATABASE \"$DEV_DB\""
  echo "created: $DEV_DB"
fi
EOS

MIGRATE_DB="$DEV_DB" POSTGRES_CONTAINER="$CONTAINER" "$(dirname "$0")/migrate.sh"
