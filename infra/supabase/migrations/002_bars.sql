-- ============================================================
-- TradingFirm — Migration 002: OHLCV bar store
-- ============================================================
-- Stored bars for the analyst/watcher stack (Phase 1). One row
-- per (ticker, interval, ts). Re-runnable: IF NOT EXISTS only,
-- no seed INSERTs.
-- ============================================================

CREATE TABLE IF NOT EXISTS data_engine.ohlcv_bars (
    ticker          VARCHAR(10) NOT NULL,
    interval        VARCHAR(10) NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    open            DOUBLE PRECISION NOT NULL,
    high            DOUBLE PRECISION NOT NULL,
    low             DOUBLE PRECISION NOT NULL,
    close           DOUBLE PRECISION NOT NULL,
    volume          BIGINT NOT NULL,
    PRIMARY KEY (ticker, interval, ts)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_bars_ticker_interval_ts
    ON data_engine.ohlcv_bars (ticker, interval, ts DESC);
