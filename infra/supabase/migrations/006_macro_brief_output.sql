-- ============================================================
-- 006 — RISK: macro brief output columns (Part 3.6a, spec decision 1)
--
-- Part 3.6b writes one row per generated brief:
--   brief       the ai-agent response as returned ({regimeView, keyRisks[],
--               upcoming[], oneParagraph}); brief_text = oneParagraph
--   trigger     what caused the generation
-- Bounded and allow_nan=False are enforced by 3.6b's writer; the database
-- only checks that brief is a JSON object.
--
-- Re-runnable: ADD COLUMN IF NOT EXISTS skips the column and its inline
-- CHECK on a rerun. No seed INSERT.
--
-- NOT NULL with no default: ADD COLUMN ... NOT NULL fails on a table that
-- already holds rows. risk.macro_briefs held 0 rows in tradingfirm and
-- tradingfirm_dev on 2026-09-10, and nothing writes one before 3.6b. A
-- default would hide a writer that forgot the field.
-- ============================================================

ALTER TABLE risk.macro_briefs ADD COLUMN IF NOT EXISTS brief JSONB NOT NULL
    CHECK (jsonb_typeof(brief) = 'object');

ALTER TABLE risk.macro_briefs ADD COLUMN IF NOT EXISTS trigger TEXT NOT NULL
    CHECK (trigger IN ('slot', 'regime_change', 'critical', 'manual'));
