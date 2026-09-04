# Progress

One row per part from `docs/plan-analyst-watcher.md`. Updated at the end of every part, before the commit.

| Part | Status | Commit | Date | Notes |
|---|---|---|---|---|
| 0.1 | done | 3ed39cf | 2026-09-04 | `.env.example` + real `.env` created; `docker-compose up -d` boots all 7 containers; `curl :8001/health` → `db_connected: true`. Blocker along the way: host `docker.sock` belonged to a different macOS user account and was unreachable until the user restarted Docker Desktop under this account. |
| 0.2 | done | 05128f5 | 2026-09-04 | Mounted `infra/supabase/migrations` read-only into postgres's `/docker-entrypoint-initdb.d/`; fresh `pgdata` volume now auto-applies `001_initial_schema.sql` (`\dn` shows `ai`, `data_engine`, `public`, `risk`, `signals`, `users`). Added `scripts/migrate.sh` for existing volumes — tracks applied files in `public.schema_migrations`, rerun is a no-op. Blocker: this Mac has no `psql` client installed, so `migrate.sh` was verified by running it inside the `tf-postgres` container (which already has `bash` + `psql`) rather than installing a Postgres client on the host. Also fixed mid-part: `migrate.sh` originally `source`d `.env` directly, which broke on `EDGAR_USER_AGENT`'s unquoted value containing a space — switched to grepping individual keys instead of sourcing the whole file. |
