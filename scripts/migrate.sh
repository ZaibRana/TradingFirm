#!/usr/bin/env bash
# Applies infra/supabase/migrations/NNN_*.sql to an existing Postgres volume
# that predates the docker-entrypoint-initdb.d mount (which only runs once,
# on first init of an empty volume). Idempotent: tracks applied files in
# public.schema_migrations and skips ones already recorded. Every migration
# file must be safe to re-run (CREATE ... IF NOT EXISTS) since a fresh volume
# applies 001 via docker-entrypoint-initdb.d before this script ever sees it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MIGRATIONS_DIR="$ROOT_DIR/infra/supabase/migrations"

# .env is parsed by Docker Compose, not bash, and allows unquoted values with
# spaces (e.g. EDGAR_USER_AGENT) — sourcing it as a shell script is unsafe.
# Pull out only the keys this script needs.
env_get() {
  [ -f "$ROOT_DIR/.env" ] || return 0
  grep -E "^$1=" "$ROOT_DIR/.env" | tail -n1 | cut -d= -f2- || true
}

DB_HOST="${DB_HOST:-$(env_get DB_HOST)}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-$(env_get DB_PORT)}"
DB_PORT="${DB_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-$(env_get POSTGRES_USER)}"
POSTGRES_USER="${POSTGRES_USER:-tf_user}"
POSTGRES_DB="${POSTGRES_DB:-$(env_get POSTGRES_DB)}"
POSTGRES_DB="${POSTGRES_DB:-tradingfirm}"
DB_PASSWORD="${DB_PASSWORD:-$(env_get DB_PASSWORD)}"
: "${DB_PASSWORD:?DB_PASSWORD must be set (in .env or environment)}"

export PGPASSWORD="$DB_PASSWORD"

psql_cmd() {
  psql -h "$DB_HOST" -p "$DB_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 "$@"
}

psql_cmd -q -c "CREATE TABLE IF NOT EXISTS public.schema_migrations (filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());"

shopt -s nullglob
files=("$MIGRATIONS_DIR"/[0-9][0-9][0-9]_*.sql)
if [ ${#files[@]} -eq 0 ]; then
  echo "no migration files found in $MIGRATIONS_DIR"
  exit 0
fi

IFS=$'\n' sorted=($(sort <<<"${files[*]}")); unset IFS

applied_any=0
for file in "${sorted[@]}"; do
  name="$(basename "$file")"
  already="$(psql_cmd -tAc "SELECT 1 FROM public.schema_migrations WHERE filename = '$name';")"
  if [ "$already" = "1" ]; then
    echo "skip: $name (already applied)"
    continue
  fi
  echo "applying: $name"
  psql_cmd -f "$file"
  psql_cmd -q -c "INSERT INTO public.schema_migrations (filename) VALUES ('$name');"
  applied_any=1
done

if [ "$applied_any" -eq 0 ]; then
  echo "no pending migrations"
fi
