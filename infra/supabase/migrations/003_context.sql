-- 003_context.sql — Phase 2 context tables (Part 2.1).
-- Re-runnable: IF NOT EXISTS everywhere, no seeds (docs/decisions.md 2026-09-05).

-- Ticker news. `ticker` is NOT NULL on purpose: a nullable column inside a
-- UNIQUE constraint does not dedup (Postgres treats NULLs as distinct), so
-- general-market news uses the sentinel '_MARKET' (db.MARKET_TICKER).
CREATE TABLE IF NOT EXISTS data_engine.news_items (
    id              BIGSERIAL PRIMARY KEY,
    ticker          VARCHAR(10) NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,
    source          VARCHAR(100),
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    summary         TEXT,
    sentiment       JSONB,
    created_at      TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT news_items_ticker_url_key UNIQUE (ticker, url)
);

CREATE INDEX IF NOT EXISTS news_items_ticker_published_idx
    ON data_engine.news_items (ticker, published_at DESC);

-- Dated events per ticker. event_type: 'earnings' (report date, from the
-- Finnhub calendar), 'earnings_surprise' (fiscal period end, from
-- /stock/earnings), later 'exdiv'. `meta` is merged on conflict
-- (existing || new), so each writer keeps its own nested key.
CREATE TABLE IF NOT EXISTS data_engine.events (
    ticker          VARCHAR(10) NOT NULL,
    event_type      VARCHAR(30) NOT NULL,
    event_at        TIMESTAMPTZ NOT NULL,
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, event_type, event_at)
);

CREATE INDEX IF NOT EXISTS events_ticker_event_at_idx
    ON data_engine.events (ticker, event_at DESC);
