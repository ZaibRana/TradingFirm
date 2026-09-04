#!/usr/bin/env bash
# Applies infra/supabase/migrations/NNN_*.sql to a running postgres container
# whose volume predates the docker-entrypoint-initdb.d mount added in
# docker-compose.yml (that mount only runs once, on first init of an empty
# volume). Runs entirely inside the container via `docker exec`, reusing its
# own psql binary and its own POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB env
# vars (already set there by docker-compose.yml's `environment:` block) —
# no Postgres client and no .env parsing needed on the host.
# Idempotent: tracks applied files in public.schema_migrations and skips
# ones already recorded. Every migration file must be safe to re-run
# (CREATE ... IF NOT EXISTS) since a fresh volume applies 001 via
# docker-entrypoint-initdb.d before this script ever sees it.
set -euo pipefail

CONTAINER="${POSTGRES_CONTAINER:-tf-postgres}"
MIGRATIONS_DIR="/docker-entrypoint-initdb.d"  # same files, as mounted into the container

if ! docker exec "$CONTAINER" true 2>/dev/null; then
  echo "error: container '$CONTAINER' is not running (set POSTGRES_CONTAINER to override)" >&2
  exit 1
fi

# Runs a psql command inside the container, mapping its POSTGRES_* env vars
# to the PG* names psql expects.
psql_in() {
  docker exec -i "$CONTAINER" bash -c '
    PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" PGDATABASE="$POSTGRES_DB" \
      exec psql -v ON_ERROR_STOP=1 "$@"
  ' _ "$@"
}

psql_in -q -c "CREATE TABLE IF NOT EXISTS public.schema_migrations (filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());"

names=()
while IFS= read -r line; do
  [ -n "$line" ] && names+=("$line")
done < <(docker exec "$CONTAINER" bash -c "cd '$MIGRATIONS_DIR' && ls [0-9][0-9][0-9]_*.sql 2>/dev/null | sort")

if [ ${#names[@]} -eq 0 ]; then
  echo "no migration files found in $CONTAINER:$MIGRATIONS_DIR"
  exit 0
fi

applied_any=0
for name in "${names[@]}"; do
  already="$(psql_in -tAc "SELECT 1 FROM public.schema_migrations WHERE filename = '$name';")"
  if [ "$already" = "1" ]; then
    echo "skip: $name (already applied)"
    continue
  fi
  echo "applying: $name"
  psql_in -f "$MIGRATIONS_DIR/$name"
  psql_in -q -c "INSERT INTO public.schema_migrations (filename) VALUES ('$name');"
  applied_any=1
done

if [ "$applied_any" -eq 0 ]; then
  echo "no pending migrations"
fi
