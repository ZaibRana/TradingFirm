-- ============================================================
-- 005 — RISK: macro briefs (Part 3.1)
--
-- risk.health_checks and risk.alerts already exist in 001. This file adds
-- the macro brief store that Part 3.6 writes (POST /macro/brief/generate)
-- and reads (GET /macro/brief → the latest row).
--
-- Re-runnable: IF NOT EXISTS on every object, no seed INSERT
-- (scripts/migrate.sh header; docs/decisions.md 2026-09-05). The schema is
-- created here too, so the file does not depend on 001 having run.
--
-- Numbered 005, not the plan's 004: 004_filings.sql shipped with Part 2.2
-- and migrate.sh keys public.schema_migrations by filename.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS risk;

-- One shared market view per generation, not per user: D18 puts user_id on
-- positions, verdicts and alerts, and a macro brief is none of those.
-- `regime` carries the same CHECK as risk.health_checks so the two tables
-- speak one vocabulary.
CREATE TABLE IF NOT EXISTS risk.macro_briefs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    regime          VARCHAR(20) CHECK (regime IN ('HEALTHY', 'CAUTIOUS', 'DANGER', 'CRITICAL')),
    health_score    INTEGER CHECK (health_score >= 0 AND health_score <= 100),
    brief_text      TEXT NOT NULL,
    inputs          JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- "The latest brief" is the only read Part 3.6 has.
CREATE INDEX IF NOT EXISTS idx_macro_briefs_generated_at ON risk.macro_briefs(generated_at DESC);
