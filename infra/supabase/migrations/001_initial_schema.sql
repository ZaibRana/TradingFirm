-- ============================================================
-- TradingFirm System E — Initial Database Schema
-- Migration 001: Create all schemas, tables, and indexes
-- ============================================================
-- Each service owns its own schema. Services communicate via
-- APIs, never by writing to another service's tables directly.
-- ============================================================

-- ── SCHEMAS ──
CREATE SCHEMA IF NOT EXISTS data_engine;
CREATE SCHEMA IF NOT EXISTS signals;
CREATE SCHEMA IF NOT EXISTS risk;
CREATE SCHEMA IF NOT EXISTS users;
CREATE SCHEMA IF NOT EXISTS ai;

-- ============================================================
-- DATA ENGINE TABLES
-- ============================================================

-- Master stock record
CREATE TABLE IF NOT EXISTS data_engine.stocks (
    ticker          VARCHAR(10) PRIMARY KEY,
    name            VARCHAR(255),
    sector          VARCHAR(100),
    industry        VARCHAR(100),
    market_cap      BIGINT,
    float_shares    BIGINT,
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Each scan run
CREATE TABLE IF NOT EXISTS data_engine.scan_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scanned_at      TIMESTAMPTZ DEFAULT now(),
    market_status   VARCHAR(20),
    total_screened  INTEGER,
    total_passed    INTEGER,
    duration_seconds FLOAT,
    stocks          JSONB
);

-- Fundamental data per stock
CREATE TABLE IF NOT EXISTS data_engine.fundamentals (
    ticker          VARCHAR(10) PRIMARY KEY REFERENCES data_engine.stocks(ticker) ON DELETE CASCADE,
    pe_ratio        FLOAT,
    eps_growth      FLOAT,
    revenue_growth  FLOAT,
    debt_to_equity  FLOAT,
    roe             FLOAT,
    fundamental_score INTEGER,
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- SIGNALS TABLES
-- ============================================================

-- Every signal generated
CREATE TABLE IF NOT EXISTS signals.signals (
    id                      VARCHAR(50) PRIMARY KEY,
    ticker                  VARCHAR(10) NOT NULL,
    direction               VARCHAR(5) NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    status                  VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                            CHECK (status IN ('PENDING', 'TRIGGERED', 'HIT_TP1', 'HIT_TP2', 'HIT_TP3', 'STOPPED_OUT', 'EXPIRED', 'ADJUSTED')),
    entry_low               FLOAT,
    entry_high              FLOAT,
    stop_loss               FLOAT,
    tp1                     FLOAT,
    tp2                     FLOAT,
    tp3                     FLOAT,
    rr_ratio                FLOAT,
    confidence              INTEGER CHECK (confidence >= 0 AND confidence <= 100),
    confidence_breakdown    JSONB,
    zone_data               JSONB,
    context                 JSONB,
    created_at              TIMESTAMPTZ DEFAULT now(),
    triggered_at            TIMESTAMPTZ,
    closed_at               TIMESTAMPTZ,
    expires_at              TIMESTAMPTZ
);

-- Cached S/R zones per stock
CREATE TABLE IF NOT EXISTS signals.zones (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker          VARCHAR(10) NOT NULL,
    zone_type       VARCHAR(20) NOT NULL CHECK (zone_type IN ('SUPPORT', 'RESISTANCE', 'DEMAND', 'SUPPLY')),
    price_low       FLOAT NOT NULL,
    price_high      FLOAT NOT NULL,
    strength_score  INTEGER CHECK (strength_score >= 0 AND strength_score <= 100),
    methods         TEXT[],
    times_tested    INTEGER DEFAULT 0,
    calculated_at   TIMESTAMPTZ DEFAULT now()
);

-- Signal lifecycle event log
CREATE TABLE IF NOT EXISTS signals.signal_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id       VARCHAR(50) NOT NULL REFERENCES signals.signals(id) ON DELETE CASCADE,
    event           VARCHAR(50) NOT NULL,
    old_status      VARCHAR(20),
    new_status      VARCHAR(20),
    metadata        JSONB,
    logged_at       TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- RISK TABLES
-- ============================================================

-- Health score history
CREATE TABLE IF NOT EXISTS risk.health_checks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    checked_at      TIMESTAMPTZ DEFAULT now(),
    score           INTEGER CHECK (score >= 0 AND score <= 100),
    regime          VARCHAR(20) CHECK (regime IN ('HEALTHY', 'CAUTIOUS', 'DANGER', 'CRITICAL')),
    indicators      JSONB,
    trend           VARCHAR(20) CHECK (trend IN ('improving', 'declining', 'stable'))
);

-- Risk alerts
CREATE TABLE IF NOT EXISTS risk.alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_type      VARCHAR(50) NOT NULL,
    severity        VARCHAR(20) NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    message         TEXT NOT NULL,
    metadata        JSONB,
    acknowledged    BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- USERS TABLES
-- ============================================================
-- NOTE: In production with Supabase, users.profiles.id will
-- reference auth.users(id) and RLS policies will use auth.uid().
-- For local dev, we use a standalone UUID primary key.

-- User profiles
CREATE TABLE IF NOT EXISTS users.profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE,
    display_name    VARCHAR(100),
    tier            VARCHAR(20) DEFAULT 'free' CHECK (tier IN ('free', 'pro', 'premium')),
    created_at      TIMESTAMPTZ DEFAULT now(),
    last_login      TIMESTAMPTZ
);

-- User-logged trades
CREATE TABLE IF NOT EXISTS users.trades (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users.profiles(id) ON DELETE CASCADE,
    signal_id       VARCHAR(50) REFERENCES signals.signals(id) ON DELETE SET NULL,
    ticker          VARCHAR(10) NOT NULL,
    direction       VARCHAR(5) NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price     FLOAT,
    exit_price      FLOAT,
    shares          INTEGER,
    pnl             FLOAT,
    pnl_percent     FLOAT,
    notes           TEXT,
    traded_at       TIMESTAMPTZ,
    logged_at       TIMESTAMPTZ DEFAULT now()
);

-- User preferences
CREATE TABLE IF NOT EXISTS users.preferences (
    user_id             UUID PRIMARY KEY REFERENCES users.profiles(id) ON DELETE CASCADE,
    notification_email  BOOLEAN DEFAULT true,
    notification_push   BOOLEAN DEFAULT true,
    risk_tolerance      VARCHAR(20) DEFAULT 'moderate' CHECK (risk_tolerance IN ('conservative', 'moderate', 'aggressive')),
    sectors_filter      TEXT[],
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- User watchlists
CREATE TABLE IF NOT EXISTS users.watchlists (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users.profiles(id) ON DELETE CASCADE,
    name            VARCHAR(100) NOT NULL DEFAULT 'Default',
    tickers         TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- AI TABLES
-- ============================================================

-- AI signal grades
CREATE TABLE IF NOT EXISTS ai.grades (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id       VARCHAR(50) NOT NULL REFERENCES signals.signals(id) ON DELETE CASCADE,
    provider        VARCHAR(20) NOT NULL,
    model           VARCHAR(50) NOT NULL,
    score           INTEGER CHECK (score >= 0 AND score <= 100),
    conviction      VARCHAR(10) CHECK (conviction IN ('LOW', 'MEDIUM', 'HIGH')),
    reasoning       TEXT,
    factors         JSONB,
    was_correct     BOOLEAN,
    graded_at       TIMESTAMPTZ DEFAULT now()
);

-- Weekly AI self-assessment
CREATE TABLE IF NOT EXISTS ai.accuracy_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    week_start      DATE NOT NULL,
    total_graded    INTEGER,
    correct_count   INTEGER,
    accuracy        FLOAT,
    by_sector       JSONB,
    by_zone_type    JSONB,
    self_assessment TEXT,
    logged_at       TIMESTAMPTZ DEFAULT now()
);

-- AI-generated reports
CREATE TABLE IF NOT EXISTS ai.reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_type     VARCHAR(50) NOT NULL,
    period_start    DATE,
    period_end      DATE,
    content         JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Learned patterns
CREATE TABLE IF NOT EXISTS ai.patterns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_type    VARCHAR(50) NOT NULL,
    description     TEXT,
    confidence      FLOAT,
    occurrences     INTEGER DEFAULT 0,
    metadata        JSONB,
    discovered_at   TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- INDEXES
-- ============================================================

-- Data Engine
CREATE INDEX IF NOT EXISTS idx_stocks_sector ON data_engine.stocks(sector);
CREATE INDEX IF NOT EXISTS idx_scan_results_scanned_at ON data_engine.scan_results(scanned_at DESC);

-- Signals
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals.signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals.signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals.signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_ticker_status ON signals.signals(ticker, status);
CREATE INDEX IF NOT EXISTS idx_zones_ticker ON signals.zones(ticker);
CREATE INDEX IF NOT EXISTS idx_zones_ticker_type ON signals.zones(ticker, zone_type);
CREATE INDEX IF NOT EXISTS idx_signal_logs_signal_id ON signals.signal_logs(signal_id);

-- Risk
CREATE INDEX IF NOT EXISTS idx_health_checks_checked_at ON risk.health_checks(checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON risk.alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON risk.alerts(severity);

-- Users
CREATE INDEX IF NOT EXISTS idx_trades_user_id ON users.trades(user_id);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON users.trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_traded_at ON users.trades(traded_at DESC);
CREATE INDEX IF NOT EXISTS idx_watchlists_user_id ON users.watchlists(user_id);

-- AI
CREATE INDEX IF NOT EXISTS idx_grades_signal_id ON ai.grades(signal_id);
CREATE INDEX IF NOT EXISTS idx_grades_graded_at ON ai.grades(graded_at DESC);
CREATE INDEX IF NOT EXISTS idx_accuracy_logs_week ON ai.accuracy_logs(week_start DESC);
