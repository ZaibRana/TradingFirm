# Decisions Log

Append-only. One entry per decision: date, decision, why, what it supersedes (if anything).
Do not edit or delete past entries — if a decision changes, add a new entry that says so.

---

## 2026-09-04 — Docs discipline adopted

**Decision:** Every feature works from `docs/plan-analyst-watcher.md`. Plan files are read-only once approved; changes go here instead. `docs/overview.md` tracks as-built architecture, `docs/progress.md` tracks part-by-part status.

**Why:** Plans were being hand-edited mid-implementation, making it unclear what was approved vs. changed after the fact. Keeping decisions in a separate append-only log preserves the history.

**Supersedes:** N/A — first entry.

---

## 2026-09-04 — `plan-analyst-watcher.md` supersedes `plan-x.md` for product direction

**Decision:** `docs/plan-analyst-watcher.md` is the active plan. `docs/plan-x.md` is kept as reference; its §5 (disposable Docker environments) is parked to Phase 9 of the new plan.

**Why:** Product direction shifted from the System E / Docker-first rebuild toward Analyst + Watcher + Journal (swing trading first). The old plan's findings (§1) are still accurate and are not being redone.

**Supersedes:** `docs/plan-x.md` (kept, not deleted, per docs-discipline rule).

---

## 2026-09-05 — Migrations must be idempotent; recording init-applied files is deferred

**Decision:** Every file in `infra/supabase/migrations/` must be safe to run twice (`IF NOT EXISTS` on schemas/tables/indexes, `ADD COLUMN IF NOT EXISTS`, no plain `INSERT` seed rows). This is enforced by convention only, via a rule in `CLAUDE.md`. The root-cause fix — an init shell script that inserts every init-applied filename into `public.schema_migrations` so the fresh-volume and `migrate.sh` paths converge — is deferred until the first migration that cannot be made idempotent (e.g. seed data).

**Why:** Part 0.2 left two apply paths. A fresh `pgdata` volume runs the files through Postgres's `docker-entrypoint-initdb.d` without recording them; an existing volume goes through `scripts/migrate.sh`, which does record. The first `migrate.sh` run after a fresh boot therefore re-applies every file. Phase 1's only migration (`002_bars.sql`, one table + index) is naturally idempotent, so a convention covers it without adding infra now.

**Supersedes:** N/A — refines the Part 0.2 mechanism; does not change it.
