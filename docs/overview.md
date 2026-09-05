# TradingFirm — Project Overview

TradingFirm is a day-trading system that screens the US stock market, applies a
chain of technical filters, and surfaces the strongest day-trading candidates
in a live dashboard. It's built as a set of FastAPI microservices behind a
Next.js frontend, sharing Postgres and Redis as common infrastructure. Only
part of the design is actually implemented today — see [Service status](#service-status).

## Big picture

```
Finviz / Yahoo Finance
        │
        ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Data Engine │────▶│Signal Engine │────▶│  Risk Shield │────▶│   AI Agent   │
│  (Port 8001) │     │  (Port 8002) │     │  (Port 8003) │     │  (Port 8004) │
│   ✅ built   │     │  🔲 scaffold │     │  🔲 scaffold │     │  🔲 scaffold │
└──────┬───────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
       │                                                              │
       ▼                                                              ▼
┌──────────────┐                                              ┌──────────────┐
│  PostgreSQL  │◀─────────────────────────────────────────────│ Web Dashboard│
│  (Port 5432) │           HTTP proxy (Next.js API routes)     │  (Port 3000) │
└──────────────┘                                               │   ✅ built   │
       ▲                                                        └──────────────┘
       │
┌──────────────┐
│    Redis     │
│  (Port 6379) │
└──────────────┘
```

Each backend service owns its own Postgres schema (`data_engine`, `signals`,
`risk`, `users`, `ai` — see
[infra/supabase/migrations/001_initial_schema.sql](../infra/supabase/migrations/001_initial_schema.sql))
and services are meant to talk to each other only over HTTP and Redis
pub/sub, never by writing into another service's tables.

## Service status

| Service | Port | Status | What it does |
|---|---|---|---|
| `data-engine` | 8001 | **Functional** | Finviz screening → yfinance OHLCV → technical filters → enrichment. The only backend service with real logic. |
| `signal-engine` | 8002 | Empty scaffold | Intended for entry/exit signal detection (zones, patterns). Only `/health` and `/` exist. |
| `risk-shield` | 8003 | Empty scaffold | Intended for position sizing, drawdown monitoring, kill switches. Only `/health` and `/` exist. |
| `ai-agent` | 8004 | Empty scaffold | Intended for trade grading via an LLM (`LLM_PROVIDER` env var supports Gemini/Anthropic). Only `/health` and `/` exist. |
| `web` (dashboard) | 3000 | **Functional** | Next.js UI showing scan results, stock cards, market status. |

## The scanner pipeline (the core of the system)

There is now **one live scanner path**:
[`services/data-engine`](../services/data-engine), wired to the dashboard's
"Pro Scanner" tab via
[`web/app/api/scanner/pro/route.js`](../web/app/api/scanner/pro/route.js),
which proxies to it.

[`scanner/pro_scan.py`](../scanner/pro_scan.py) (+ `scan.py`,
`step1_finviz.py` … `step4_enrich.py`) is the original standalone Python
implementation `services/data-engine` was ported from. It stays in the repo
as a **frozen reference** (see [`scanner/README.md`](../scanner/README.md))
— not run by anything, not built on. The earlier JS reimplementation
(`web/lib/scanner/*.js` + `web/app/api/scanner/{discover,filter,technical}`)
and the "Scanner 1" tab/`/api/scanner/run` legacy exec path that duplicated
it have been removed.

### Pipeline steps (data-engine / `pro_scan.py`)

Implemented in
[`services/data-engine/scanners/market_scanner.py`](../services/data-engine/scanners/market_scanner.py):

1. **Pre-screen** — Finviz screens ~7,000 US stocks down to ~650 candidates
   on price, volume, and market-cap filters.
2. **Daily download** — bulk daily OHLCV (1 year) for all candidates via
   `yf.download()`, batched (≤20 tickers/call, 3s between batches,
   `threads=False`).
3. **Daily filters** — ATRP 2.5–6%, RVOL > 1.0/1.2, 52-week position 10–90%,
   IPO age > 120 days → ~20–60 pass.
4. **Hourly download** — hourly OHLCV (3 months) for daily winners only
   (~90%+ fewer API calls than downloading hourly for everything).
5. **Hourly filters** — 4H price > 50 EMA, 1H 20 EMA > 50 EMA.
6. **Enrichment** — `yf.Ticker` calls (2s delay between them) for sector,
   float, and news; gates on market cap > $500M and float 20M–1B shares.
7. **Sort** — by RVOL × ATRP, best opportunities first.

A full scan takes roughly 6 minutes and is rate-limited to one run per 10
minutes (`POST /scan/run` returns a `cooldown` status if called again too
soon, and `already_running` if a scan is mid-flight).

### Why this shape

yfinance and Finviz are unauthenticated scraping-style data sources with real
rate-limit risk — getting the user's IP blocked is treated as priority #1 to
avoid (see `.agents/AGENTS.md` G6). That drives most of the pipeline's
design: small batches, forced delays, `threads=False`, a "canary batch" that
aborts the whole scan if the first request fails, and a hard 10-minute
cooldown between scans. Both providers are explicitly dev/testing-only;
Polygon.io / FMP are the intended production upgrade path.

## Data Engine service

[`services/data-engine/main.py`](../services/data-engine/main.py) is the
FastAPI app. Key pieces:

- **Endpoints**: `POST /scan/run` (kicks off a background scan, 202
  Accepted), `GET /scan/status`, `GET /scan/results`, `GET /scan/history`,
  `GET /stocks/{ticker}`, `POST /stock/{ticker}/refresh`, `GET /market/status`,
  `GET /health`.
- **`data_engine.ohlcv_bars`** (Postgres) — daily/hourly OHLCV per ticker,
  written by `db.upsert_bars()` / read by `db.get_bars()`.
  `POST /stock/{ticker}/refresh` downloads daily (2y) + hourly (3mo) bars
  for one ticker via the provider and upserts them; a 15-minute
  Redis-backed cooldown per ticker rejects repeat calls with 429 (falls
  back to an in-memory cooldown if Redis is down), and the endpoint
  returns 503 rather than silently discarding fetched bars if Postgres is
  unavailable.
- **Storage fallback chain**: results are always kept in an in-memory store;
  Redis and Postgres are optional — the service degrades gracefully and
  keeps working (from memory only) if either is unavailable at startup.
- **`providers/`** — a `DataProvider` abstraction so a production data
  source can be swapped in later without touching scanner logic.
  `get_provider()` knows two: `yfinance` (`yfinance_provider.py`, the live
  default) and `fixture` (`fixture_provider.py`, replays
  `tests/fixtures/{daily,hourly,info}/<TICKER>.json` with no network — for
  tests only, selectable via `DATA_PROVIDER=fixture`).
- **`requirements-dev.txt`** — `pytest` + `pytest-asyncio` on top of
  `requirements.txt`; not in the prod image, installed into the dev
  container to run tests (see `CLAUDE.md` Commands).
- **`indicators/technical.py`** — ATR, ATRP, RVOL, EMA, 52-week position,
  4H aggregation from hourly bars.
- **`scanners/models.py`** — Pydantic models with `by_alias` field aliases
  (e.g. `market_cap` → `marketCap`) so FastAPI's snake_case internals
  serialize as the camelCase JSON the frontend expects.

## Web dashboard

Next.js 15 / React 19 app in [`web/`](../web). `web/app/page.js` renders the
Pro Scanner: backed by `/api/scanner/pro`, which proxies to the
`data-engine` FastAPI service — POST triggers a scan and polls
`/scan/status` until it completes, then fetches `/scan/results`.

`ProStockCard` renders individual candidates, `SectorTabs` filters by
sector, and `Header` shows market status. There is no auth yet (D18 in
`docs/plan-analyst-watcher.md` parks it deliberately); the earlier Firebase
Google-sign-in scaffolding under `web/lib/firebase/` has been removed.

## Infrastructure

- **Postgres 16** — one schema per service (`data_engine`, `signals`,
  `risk`, `users`, `ai`); scan history, stock/fundamental data, signals,
  strategies, watchlists, and an audit trail live here once tables are
  migrated in.
- **Redis 7** — scan status, pub/sub for cross-service events (e.g.
  `tf:scan:complete`, `tf:signal:new`), and cache TTLs for scan results and
  market health (see [`shared/constants.py`](../shared/constants.py)).
- **Docker Compose** — [`docker-compose.yml`](../docker-compose.yml) runs
  all 7 containers (postgres, redis, 4 FastAPI services, web) prod-like;
  `docker-compose.dev.yml` adds hot-reload. Requires `DB_PASSWORD` set in
  `.env` — compose fails fast without it.

## Where things stand

The **data-engine + web dashboard** loop is the one real, working path today:
screen the market, filter candidates, enrich, display them. Everything
downstream of that — actually generating trade signals (`signal-engine`),
managing risk (`risk-shield`), and grading trades with AI (`ai-agent`) — is
still an empty FastAPI scaffold with no business logic. The `scanner/`
standalone scripts predate the data-engine port and stay only as a frozen
reference — see [`.agents/AGENTS.md`](../.agents/AGENTS.md) for the full
rationale.
