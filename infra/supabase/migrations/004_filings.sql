-- 004_filings.sql — SEC EDGAR filings (Part 2.2). Spec: docs/specs/2.2.md.
-- Re-runnable: IF NOT EXISTS everywhere, no seeds (docs/decisions.md 2026-09-05).

-- One row per (ticker, accession number). The accession number identifies a
-- filing across EDGAR; the same accession can appear under two tickers that
-- share a CIK (GOOG / GOOGL), hence the composite key. A filed accession
-- never changes, so the writer uses ON CONFLICT DO NOTHING.
--   filed_on     the official filingDate (what a daily-bar join uses)
--   accepted_at  acceptanceDateTime (UTC), NULL when EDGAR gives none
--   meta         cik, reportDate, primaryDocument, primaryDocDescription, items
CREATE TABLE IF NOT EXISTS data_engine.filings (
    ticker          VARCHAR(10) NOT NULL,
    form            VARCHAR(20) NOT NULL,
    filed_on        DATE NOT NULL,
    accepted_at     TIMESTAMPTZ,
    accession       VARCHAR(25) NOT NULL,
    url             TEXT NOT NULL,
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, accession)
);

CREATE INDEX IF NOT EXISTS filings_ticker_filed_on_idx
    ON data_engine.filings (ticker, filed_on DESC);
