# Progress

One row per part from `docs/plan-analyst-watcher.md`. Updated at the end of every part, before the commit.

| Part | Status | Commit | Date | Notes |
|---|---|---|---|---|
| 0.1 | done | 3ed39cf | 2026-09-04 | `.env.example` + real `.env` created; `docker-compose up -d` boots all 7 containers; `curl :8001/health` → `db_connected: true`. Blocker along the way: host `docker.sock` belonged to a different macOS user account and was unreachable until the user restarted Docker Desktop under this account. |
| 0.2 | done | 05128f5, (pending) | 2026-09-04 | Mounted `infra/supabase/migrations` read-only into postgres's `/docker-entrypoint-initdb.d/`; fresh `pgdata` volume now auto-applies `001_initial_schema.sql` (`\dn` shows `ai`, `data_engine`, `public`, `risk`, `signals`, `users`). `scripts/migrate.sh` handles existing volumes — tracks applied files in `public.schema_migrations`, rerun is a no-op. Went through two revisions after review: v1 ran `psql` on the host and `source`d `.env` for creds, which broke both on this Mac having no `psql` client and on `.env`'s unquoted `EDGAR_USER_AGENT` value containing a space (bash `source` treats it as two words). v2 runs entirely via `docker exec` into `tf-postgres`, reusing the container's own `psql` and `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` env vars — no host Postgres client or `.env` parsing at all. Also hit and fixed `mapfile` not existing on macOS's stock bash 3.2 (swapped for a `while read` loop). Verified by running `./scripts/migrate.sh` directly from the host shell (not inside a container). |
