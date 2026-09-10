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
| `risk-shield` | 8003 | **Skeleton** | Market health scoring and regime detection (Phase 3). Part 3.1 gave it `config.py` / `db.py` / `cache.py`, a bounded fail-open lifespan and the `risk.macro_briefs` table; Part 3.2 added the two data fetchers (`monitors/quotes.py`, `monitors/fred.py`); Part 3.3 added the six regime monitors, the health score and the regime classifier (`scoring/`). Nothing calls them yet. The endpoints are still `/health` (reporting `db_connected` / `redis_connected` / `fredConfigured`) and `/`. The scheduler and `/market/*` land in 3.4. |
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
  `GET /stocks/{ticker}`, `POST /stock/{ticker}/refresh`, `GET /stock/{ticker}/bars`,
  `GET /indicators/{ticker}`, `GET /dossier/{ticker}`, `GET /market/status`,
  `GET /health`.
- **`data_engine.ohlcv_bars`** (Postgres) — daily/hourly OHLCV per ticker,
  written by `db.upsert_bars()` / read by `db.get_bars()`, with bar-shaping
  logic (`db.bar_records_from_df()`) shared by every write path.
  `POST /stock/{ticker}/refresh` downloads daily (2y) + hourly (3mo) bars
  for one ticker via the provider and upserts them; a 15-minute
  Redis-backed cooldown per ticker rejects repeat calls with 429 (falls
  back to an in-memory cooldown if Redis is down), and the endpoint
  returns 503 rather than silently discarding fetched bars if Postgres is
  unavailable. The scan pipeline (`scanners/market_scanner.py`) also
  upserts daily + hourly bars — for final scan winners only, once hourly
  filtering is done — best-effort: a missing db pool or a failed upsert
  for one ticker is logged and skipped, never aborts the scan. Both write
  paths key rows on `main.normalize_ticker()` (upper-cased, stripped) so
  a ticker can't land under two different casings.
- **`providers/context/`** (Phase 2 context fetchers). `finnhub_client.py`:
  thin `httpx` client, key in the `X-Finnhub-Token` header (never the URL),
  in-process limiter of 60 calls per rolling minute plus a 1.2 s gap, typed
  errors (`FinnhubNotConfigured` before any HTTP when the key is empty,
  `FinnhubAuthError`, `FinnhubRateLimited`, `FinnhubError`), no retries.
  `finnhub.py`: `company_news`, `recommendations`, `earnings_calendar`,
  `earnings_surprises`, `profile` return raw Finnhub bodies cached in Redis
  (`tf:cache:finnhub:{kind}:{ticker}`, news 15 min, the rest 24 h, fail-open
  when Redis is down); pure converters turn them into rows and
  `sync_context()` stores news and earnings events. Tables (migration
  `003_context.sql`): `data_engine.news_items` (`ticker` NOT NULL, general
  news under `_MARKET`, unique on `(ticker, url)`) and `data_engine.events`
  (PK `(ticker, event_type, event_at)`, `meta` merged on conflict). The
  key reaches prod `data-engine` only via `FINNHUB_API_KEY`; the dev twin
  has it hard-coded empty. Tests mock HTTP with `respx` and replay
  `tests/fixtures/finnhub/AAPL_*.json`, recorded once by
  `tests/record_finnhub_live.py`. `edgar_client.py` (Part 2.2, spec
  `docs/specs/2.2.md`): SEC EDGAR over the same shape — no key, a declared
  `User-Agent` "<app> <email>" from `EDGAR_USER_AGENT` (empty →
  `EdgarNotConfigured` before any HTTP), one process-wide limiter
  `ratelimit.edgar_limiter` (rolling window, 10 per second), 403/429 →
  `EdgarRateLimited` (stop, no retry), 404 → `EdgarNotFound`. `edgar.py`:
  `cik_map()` (whole `company_tickers.json` → `{ticker: cik}`, Redis
  `tf:cache:edgar:cik_map`, 24 h), `recent_filings(ticker, forms=('8-K',
  '4'), days=30)` → `(rows, truncated)` (submissions `filings.recent`
  parsed to the rows filed within the last 90 days plus the block's oldest
  date, cached 15 min under `tf:cache:edgar:filings:{T}`; forms/days
  filtered in-process, `days` 1–90, amendments fold into the base form;
  `truncated` only when the block does not reach back to `today − days`;
  not-in-map and submissions-404 both return empty), pure
  `parse_submissions` / `filter_filings` / `filing_records`, and
  `sync_filings()` storing rows via `db.upsert_filings()` into
  `data_engine.filings` (migration `004_filings.sql`, PK `(ticker,
  accession)`, `filed_on DATE` = official filing date, `accepted_at`
  nullable, `ON CONFLICT DO NOTHING`). Fixtures
  `tests/fixtures/edgar/{company_tickers,AAPL_submissions}.json` recorded
  once by `tests/record_edgar_live.py`. Shared by both fetchers:
  `providers/context/ratelimit.py` (`RateLimiter`, injectable clock),
  `cache.cached_json()` (read-through, fail-open on Redis, wrong-shaped
  bodies are a miss) and `tickers.validate_ticker()` (1–5 letters; class
  shares deferred, `docs/decisions.md` 2026-09-09).
  `alphavantage_client.py` + `earnings.py` (Part 2.3, spec `docs/specs/2.3.md`):
  past earnings report dates, which the Finnhub free calendar does not
  carry. Primary is `DataProvider.get_earnings_dates()` (yfinance
  `Ticker.get_earnings_dates(limit=12)`; the fixture provider replays
  `tests/fixtures/earnings_dates/<T>.json`, where `null` records "this
  ticker has no earnings feed" and a missing file raises); the fallback is
  Alpha Vantage `EARNINGS`, called only when the primary yields no usable
  past date — never on a rate limit (`ProviderRateLimited`) and never when
  the bar store is empty. Report dates are validated against the stored
  daily bars (a bar date, or within one day of one) before they are written
  as `('earnings', date)` rows with `meta.earnings.{source, validated,
  hour, epsEstimate, epsReported, surprisePct}`, which merges beside 2.1's
  `meta.calendar`. `POST /stock/{ticker}/refresh` runs the step after the
  bar upserts and always answers `earningsDates: {source, stored, dropped,
  reason}`; a failure there never fails a refresh whose bars were stored.
  The Alpha Vantage key travels as the `apikey` query parameter (no header
  form exists) under the two conditions in that client module: the `httpx`
  logger pinned to WARNING and typed errors raised `from None`.
  `indicators/earnings.py` holds the pure reaction calculation (plan §3):
  `earnings_reactions(events, bars, limit=8)` pairs each confirmed report
  with the session that absorbed it (`amc` → the next session, `bmo`/`dmh`
  → the same one, at most 4 calendar days later) and returns gap % and
  close-to-close % newest first, plus `dataQuality: {source, dropped,
  disagreements}`. An unknown report hour and a cross-source date conflict
  are both settled by `calc_rvol >= 2` on the candidate session, never by
  the size of the move; when volume cannot separate them the report is
  dropped and counted. `providers/context/earnings.earnings_reaction_history()`
  is the I/O wrapper over the new generic `db.get_events(ticker,
  event_type, since, until)` and the existing `get_bars`; `reactions` is
  `null` when no confirmed report exists at all and `[]` when reports exist
  but no bars explain them.
- **Storage fallback chain**: results are always kept in an in-memory store;
  Redis and Postgres are optional — the service degrades gracefully and
  keeps working (from memory only) if either is unavailable at startup.
- **`providers/`** — a `DataProvider` abstraction so a production data
  source can be swapped in later without touching scanner logic.
  `get_provider()` knows two: `yfinance` (`yfinance_provider.py`, the live
  default) and `fixture` (`fixture_provider.py`, replays
  `tests/fixtures/{daily,hourly,info}/<TICKER>.json` with no network — for
  tests only, selectable via `DATA_PROVIDER=fixture`).
- **`requirements-dev.txt`** — `pytest` + `pytest-asyncio` + `respx` on top of
  `requirements.txt`; baked into the Dockerfile's `dev` stage only (the
  `prod` stage never sees it). Tests run in `tf-data-engine-dev`, see
  Infrastructure below and `CLAUDE.md` Commands. `pytest.ini` restricts
  discovery to `tests/test_*.py` so a bare `pytest` cannot collect the
  live-scan script `tests/full_scan_test.py`.
- **`indicators/`** — pure, no-I/O indicator functions, imported from the
  package (`from indicators import ...`; the submodule split is an
  implementation detail): `moving_averages.py` (EMA, 4H aggregation from
  hourly bars), `volatility.py` (ATR, ATRP, extension from an MA in ATR
  units, opening gap %), `momentum.py` (RSI — SMA-seeded Wilder, MACD,
  relative strength vs a benchmark in percentage points, 52-week position),
  `volume.py` (RVOL, 20-day average dollar volume), `levels.py`
  (support/resistance zones: strict fractal swings + close-binned volume
  nodes, merged within 0.5% of the group's running mean, scored 0–90,
  top 3 per side relative to the last close, returned as `Zone`
  dataclasses), `snapshot.py` (`swing_snapshot`: the plan §3 swing set +
  zones as one dict from a daily frame and optional benchmark closes),
  `sectors.py` (11 yfinance sector names → SPDR sector ETFs), `models.py`
  (`IndicatorsResponse`, camelCase aliases). Conventions in
  `docs/decisions.md` 2026-09-06 (indicator package, zones, endpoint).
- **`GET /indicators/{ticker}`** — the swing set + zones for one ticker,
  computed from stored daily bars only (never the provider). SPY and the
  sector ETF (from `data_engine.stocks.sector`, read by `db.get_stock()`)
  come from the same bar store for relative strength; a missing one nulls
  its fields and shows `bars: 0` under `benchmarks`. Cached in Redis for
  15 min (`tf:cache:indicators:{ticker}`); `cached` is set on the way out,
  and `POST /stock/{ticker}/refresh` drops the key after writing bars.
- **`GET /dossier/{ticker}?horizon=swing`** — one document per ticker
  (`dossier/`): the indicator snapshot and zones, Finnhub news, events,
  recommendations and profile, EDGAR filings, and Part 2.3's earnings
  reactions. Every section is an object with its own `status` (`ok`,
  `truncated`, `error`, `unconfigured`), so a source that is down or
  unconfigured degrades one section while the rest returns 200 — there is
  no 502 on this path. A *database* failure is the exception: it is a 503
  for the whole document (`db.DB_ERRORS`), never a degraded section. Bars
  more than one weekday behind the last close trigger one refresh through
  `main.refresh_ticker_bars()` first; if that fails or is on cooldown the
  stored bars are served with `bars.status: stale`. Caps: 30 headlines, 10
  filings, both flagged by `truncated`. Cached in Redis
  (`tf:cache:dossier:{horizon}:{ticker}`) for 15 min in market hours, 60 min
  outside, and 2 min when any section failed; `cached` is set on the way
  out. Budgets: 8 s per section, 20 s for the refresh step. A cold dossier
  costs 5 Finnhub + 2 EDGAR calls, a warm one 2, a cached one none, and the
  response reports them under `budget`.
- **Source cooldowns** (`cache.cooldown_remaining` / `start_cooldown`) — a
  Finnhub 429 parks that source for 60 s, an EDGAR 403/429 for 15 min, an
  Alpha Vantage cap for 1 h, source-wide rather than per ticker. The next
  dossier skips the source before any HTTP (`reason: cooldown`), and
  `sync_earnings_dates` skips its Alpha Vantage fallback the same way
  (`earningsDates.reason: "cooldown"`). Redis is the store; an in-memory
  clock (`cache.MemoryCooldowns`) covers Redis being absent or failing, and
  the same pair of helpers backs the per-ticker refresh cooldown.
- **`scanners/models.py`** — Pydantic models with `by_alias` field aliases
  (e.g. `market_cap` → `marketCap`) so FastAPI's snake_case internals
  serialize as the camelCase JSON the frontend expects.

## Risk Shield service

[`services/risk-shield`](../services/risk-shield) holds the Phase 3 regime
inputs (Part 3.2, spec `docs/specs/3.2.md`) and the health score built on
them (Part 3.3, spec `docs/specs/3.3.md`). All of it is library modules
with no endpoint or scheduler yet (3.4 adds both):

- **`monitors/quotes.py`** — `get_core_quotes(r, memory)`: one yfinance
  1.5.1 `download` of the 17 core tickers (`SPY QQQ RSP ^VIX TLT GLD UUP
  XLK XLU XLP XLV XLY XLF ES=F NQ=F CL=F GC=F`), daily 1y, `threads=False`,
  timeout 5 s. It returns a JSON envelope `{asOf, tickers: {T: {date[],
  open[], high[], low[], close[], volume[]}}, missing, reason}` cached under
  `tf:risk:cache:quotes`: 5 min when complete, 120 s when `partial` / `empty`.
  - **Request count:** 34 requests on a cold container (a timezone fetch
    per ticker), 17 warm.
  - **Rate limits:** yfinance 1.5.1 only logs them, so a handler on the
    `yfinance` logger detects them, behind an exact version guard.
  - **Concurrency:** a single-flight lock is held for the whole download.
- **`monitors/fred.py` + `fred_client.py`** — FRED
  `series/observations` for `VIXCLS DGS10 DGS2 T10Y2Y DFF DCOILWTICO
  CPIAUCSL UNRATE`, one request per series, 800 days back, `"."` values
  dropped. Cached per series under `tf:risk:cache:fred:{SERIES}`: 6 h, or
  120 s when empty.
  - **Key:** `FRED_API_KEY` travels in the `api_key` query parameter (FRED
    has no header form), under the Alpha Vantage conditions: the `httpx`
    logger pinned to WARNING, typed errors raised `from None`.
  - **Bounds:** 8 s per request (`httpx` timeout plus `asyncio.wait_for`);
    `ratelimit.fred_limiter` at 60/min with a 1 s gap.
  - **Snapshot:** `fred_snapshot()` walks the 8 series. It stops on a
    source-wide state and continues past a per-series error.
- **Refusals and cooldowns** — `cache.py` carries data-engine's cooldown
  helpers under `tf:risk:cooldown:{SOURCE}`:
  - yfinance: a rate limit, or an all-empty download, parks the source 15 min.
  - FRED: a 429/423 parks it 15 min, a rejected key 1 h.
  - A refusal raises (`…RateLimited`, `…CoolingDown`) and caches nothing.
  - `cached_json` refuses a `None` from a fetcher and takes a body-derived TTL (`ttl_for`).
- **Live canaries** — `tests/fred_live.py` and `tests/quotes_live.py` run
  only through the isolated `docker run` line in `docs/specs/3.2.md`:
  default bridge network, unroutable `DATABASE_URL` / `REDIS_URL`, and
  only `FRED_API_KEY` taken from `.env`. `tests/live_guard.py` refuses a
  prod-looking environment.
- **`quotes.get_quotes_view(r, memory)`** is what the monitors read.
  - Every full quotes answer is also kept 24 h under
    `tf:risk:cache:quotes_last`.
  - On a cooldown, refusal, error or empty download, the view serves that
    copy with every ticker stale.
  - A partial download is filled per ticker.
  - With nothing to serve it is empty (`source: "none"`); it never raises.
- **`monitors/series.py`** pairs tickers only through `align()`, an inner
  join on date. A same-day bar downloaded before 16:15 ET is partial and
  dropped (`zoneinfo` America/New_York).
- **`monitors/regime.py`** holds six pure monitors, each returning `{score,
  raw, detail, stale}`, registered in `MONITORS` with Part 5's weights:
  - `vix` (25) reads `^VIX` directly, intraday level included
  - `breadth` (20) is the RSP/SPY 20-day slope; `adRatio` is null
  - `spy_trend` (20) uses EMA 20/50/200 and lower lows
  - `sector_rotation` (15) is the offensive vs defensive 5-day spread
  - `volume` (10) is SPY+QQQ against the 20-day average
  - `cross_asset` (10) is the 1-day TLT/GLD/UUP/SPY moves
- **`scoring/`**:
  - `health_calculator.compute_health(r, memory)` runs the view, then the
    monitors, then the integer-weighted, half-up score. Monitors without a
    score are left out, and a covered weight below 70 gives no score.
  - `regime_classifier.classify()` maps the score to HEALTHY ≥ 70 /
    CAUTIOUS ≥ 40 / DANGER ≥ 20 / CRITICAL.
  - The thresholds Part 5 doesn't give are provisional (`docs/decisions.md`).

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
- **`tf-data-engine-dev`** — opt-in eighth container (compose profile
  `dev`, `docker compose --profile dev up -d data-engine-dev`, host port
  8011; migrations mounted read-only at `/migrations`) for running data-engine tests without touching prod
  `tf-data-engine`. Built from the data-engine Dockerfile's `dev` stage
  (dev deps, no code — the source tree is volume-mounted), provider
  hard-coded to `fixture`, its own database `tradingfirm_dev` and Redis
  DB 1, no `depends_on`. `scripts/dev-db.sh` creates that database and
  applies migrations to it (via `MIGRATE_DB=` in `scripts/migrate.sh`);
  until it runs the dev API reports `db_connected: false`. Conventions in
  `docs/decisions.md` 2026-09-06 (Part 0.7 entry).
- **`tf-risk-shield-dev` (port 8013)** is the same arrangement for
  risk-shield (Part 3.1): profile `dev`, the Dockerfile's `dev` stage,
  source volume-mounted, `tradingfirm_dev` + Redis DB 1, `FRED_API_KEY`
  hard-coded empty, and `infra/supabase/migrations` mounted read-only at
  `/migrations` for the tests that assert migration text. Phase 3 tests run
  there:
  `docker exec tf-risk-shield-dev pytest tests/test_config.py ... -v`.
- **Startup bounds differ between the two services.** risk-shield wraps
  each dependency connection in `asyncio.wait_for(config.STARTUP_TIMEOUT)`
  (5 s, worst-case boot ~10 s); data-engine does not, and a slow-but-not-
  refusing Postgres can stall its boot for up to a minute. `docs/decisions.md`
  2026-09-09 (Part 3.1) records the reasoning and leaves data-engine to a
  later refactor.

## Where things stand

The **data-engine + web dashboard** loop is the one real, working path today:
screen the market, filter candidates, enrich, display them. Everything
downstream of that — actually generating trade signals (`signal-engine`)
and grading trades with AI (`ai-agent`) — is still an empty FastAPI
scaffold with no business logic. `risk-shield` has its infrastructure
(config, pool, cache, migration, dev twin) as of Part 3.1 and its two
data fetchers (core quotes, FRED) as of Part 3.2, and its health score
and regime as of Part 3.3, but no endpoint or scheduler yet. The `scanner/`
standalone scripts predate the data-engine port and stay only as a frozen
reference — see [`.agents/AGENTS.md`](../.agents/AGENTS.md) for the full
rationale.
