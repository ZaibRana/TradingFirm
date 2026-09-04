# Plan X — From "working scanner" to System E, with disposable Docker environments

Status: **proposal, no code written.** Every phase below still goes through G1 (3-sentence spec, your approval) before files are touched.

Sources: `docs/overview.md` (what exists), `Documents/system_e_hybrid_proposal.md` + `Documents/final_comparison_and_conclusions.md` (what we want), `Documents/architecture_desig/part_1..8` (how the target is designed), `.agents/AGENTS.md` (rules).

---

## 1. Where we actually are

`docs/overview.md` is accurate. Things it does **not** say that matter for this plan (verified by reading the repo):

| Finding | Evidence | Why it matters |
|---|---|---|
| **Compose has probably never fully worked.** No `.env` exists anywhere, and compose fails fast without `DB_PASSWORD`. | `ls .env*` → nothing | The "prod-like" stack is untested. Step 1 of any Docker work is making it boot. |
| **Postgres schema is never applied.** `infra/supabase/migrations/001_initial_schema.sql` is not mounted into the postgres container. | `docker-compose.yml` postgres service has only the `pgdata` volume | `data-engine` will log `DB save failed` on every scan; results live only in memory/Redis. A "temp production" DB would be empty of tables. |
| **No healthchecks on the 4 FastAPI services or web.** Only postgres/redis have them. | `docker-compose.yml` | `depends_on: service_started` lets web start before data-engine can answer. Temp envs need "is it actually up?" |
| **`shared/` is not importable from any container.** Each Dockerfile's build context is its own service dir. | `services/*/Dockerfile` `COPY . .` | `shared/constants.py` channel names are duplicated by hand in `data-engine/cache.py`. Signal Engine will need the same constants → drift risk. |
| **Data Engine discards OHLCV after the scan (G8, correct).** No `/stock/{ticker}/ohlcv` endpoint. | `main.py` endpoints; scanner `del` + `gc.collect()` | Part 4 has Signal Engine fetch OHLCV from Data Engine for zone detection. Today that would mean a **second live yfinance download per stock**. This is the biggest G6 landmine in the Documents' design and must be solved before Signal Engine starts. |
| **Web still carries `firebase` + `yahoo-finance2`.** Part 2/6 say remove both. | `web/package.json` | Dead weight in the image; Firebase auth conflicts with the Supabase plan. |
| **Three scanner pipelines** (already in overview). `web/lib/scanner` has no UI caller; `scanner/*.py` is still called by the "Scanner 1" tab via `exec`. | `web/app/api/scanner/run/route.js` | `exec`ing a Python script from the Next.js container is impossible inside Docker (no Python in the node image). "Scanner 1" is already broken in any containerised deployment. |
| Docker Desktop 29.x and Compose v5.3 are installed. | `docker --version`, `docker-compose --version` | Tooling is there. (Daemon couldn't be reached from my sandbox, so "compose up works" is unverified.) |

**Bottom line:** the *only* working path is data-engine → web "Pro Scanner" tab, running locally without Docker. Everything else is scaffold or legacy.

---

## 2. What you want (System E) vs. what exists

| System E layer / Documents part | Target | Repo today | Gap size |
|---|---|---|---|
| Layer 1 — Smart Watchlist (Part 3) | Scanner + fundamental score + persisted results + `/stock/{t}/ohlcv`, `/fundamentals/{t}`, `/indicators/{t}` | Scanner ✅, persistence ✅ (but schema unapplied), fundamentals ❌, extra endpoints ❌ | Medium |
| Layer 2 — Signal Engine (Part 4) | S/R zones (fractal + volume cluster + pivots), signal calc, lifecycle, Redis listeners | `/health` only | **Large** — the core product |
| Layer 3 — Risk Shield (Part 5) | 6 monitors, weighted health score, regime, alert throttling, 5-min schedule | `/health` only | Medium — pure math on ~12 tickers |
| Web App (Part 6) | Auth, dashboard, signal cards, risk gauge, stock detail chart, trades, SSE | Single-page scanner UI, no auth | Large |
| AI Agent (Part 7) | Claude primary / Gemini fallback, grading, weekly learning loop | `/health` only | Medium, but **last** — needs signal outcomes to exist first |
| DB schema (Part 8) | 5 schemas, 16 tables, RLS | SQL written ✅, never applied ❌, RLS ❌ | Small |
| Deployment (Part 8) | Compose locally, same images to Cloud Run | Compose written, unverified | Small–Medium |
| Scheduler (Part 2) | Huey | FastAPI `BackgroundTasks` | Deferred (see §7) |

---

## 3. Decision: continue on this repo, or fresh small start?

**Recommendation: continue on this repo, but shrink it first.** Not a rewrite, and not "keep everything".

Why not fresh:
- The Documents' target folder layout (Part 1) **is already this repo**: `services/{data-engine,signal-engine,risk-shield,ai-agent}`, `web/`, `shared/`, `infra/`, two compose files. A fresh start would recreate the same skeleton.
- `services/data-engine` is ~1,800 lines of the hardest, most fragile work already done and hardened: provider abstraction, batch/delay/canary logic, `threads=False`, cooldown, in-memory → Redis → Postgres fallback chain, camelCase aliasing, and a mocked unit-test suite. Rewriting it means re-earning every rate-limit lesson in `.agents/AGENTS.md` Part 2 against live yfinance — exactly what G6 says not to do.
- The DB schema for all five services is already written and matches Part 8 nearly line for line.

Why "shrink" (this is the "small start" you're asking about, applied inside the repo):
- **Freeze `scanner/`** (pipeline #1) as a read-only reference. Remove the "Scanner 1" tab and `web/app/api/scanner/run/route.js` from the dashboard. It cannot work in Docker anyway.
- **Delete `web/lib/scanner/*` and `web/lib/data/*`** (pipeline #2) and `yahoo-finance2`. No caller, and the frontend must never talk to Yahoo directly (Part 6).
- **Remove `firebase`** from web once nothing imports it (auth is a later phase; Supabase is the chosen direction).
- Result: **one** scanner implementation, one dashboard path, and G10 satisfied. This is a half-day of deletions, not a rebuild.

What a fresh start would be right for: nothing here. The one legitimate "fresh" piece is Signal Engine — it starts from an empty scaffold regardless.

---

## 4. Rules that shape every phase (from `.agents/AGENTS.md`)

- **G1**: each phase below opens with a 3-sentence spec you approve before code.
- **G6 / Part 2 rate limits**: no new live-API path without batch ≤20, delays, canary, `threads=False`. Every new service that touches yfinance is counted against the **same IP**. Risk Shield's 5-minute loop and Signal Engine's OHLCV needs are both designed below to add **zero** new per-stock downloads.
- **G7**: every new module ships with a pytest file that mocks the provider. Only `test_*.py` files; never run bare `pytest` in `data-engine` (see `CLAUDE.md`).
- **G8**: OHLCV that we now choose to keep is stored compactly (Postgres/Redis), never as DataFrames in `app.state`.
- **G10**: simplest version first — Huey, Supabase Auth, SSE, and the AI Agent are all deferred until the thing they serve exists.

---

## 5. Docker: temporary production environments

### 5.1 What "temp production environment" means here

A stack you can bring up in one command, on this MacBook (later a $20 VPS), that:
- runs the **production images** (multi-stage builds, no source volume mounts, `uvicorn` without `--reload`, `next start`),
- starts from a **fresh, migrated database** every time,
- is **isolated by name** so two can coexist (e.g. `main` and `feature-signals`),
- can be **torn down completely** (`down -v`) with nothing left behind,
- and, critically, **does not multiply live yfinance/Finviz traffic**.

### 5.2 Design

**A. Three compose layers, one base**

| File | Role | Exists? |
|---|---|---|
| `docker-compose.yml` | Base: images, ports, env, healthchecks | ✅ (needs healthchecks + init SQL) |
| `docker-compose.dev.yml` | Overlay: volume mounts, `--reload`, `npm run dev` | ✅ |
| `docker-compose.prod.yml` | Overlay: `restart: unless-stopped`, resource limits, `DATA_PROVIDER` pinned, **no host ports for backend services** (only `web` exposed, matching Part 8 "Docker internal network") | ❌ new |

Temp env = base + prod overlay + a unique project name:

```bash
docker compose -p tf-feature-signals \
  -f docker-compose.yml -f docker-compose.prod.yml \
  --env-file envs/feature-signals.env up -d --build
```

Project name (`-p`) prefixes containers, network, and volumes, so `pgdata` becomes `tf-feature-signals_pgdata`. Two stacks never collide. Host port for `web` comes from the env file (`WEB_PORT=3001`) so several dashboards can run side by side.

**B. Migrations applied on first boot**

Mount `infra/supabase/migrations/` into `postgres:/docker-entrypoint-initdb.d/` (read-only). Postgres runs `*.sql` there once when the data volume is empty, which is exactly the temp-env case. Later, real migration tooling (Alembic or plain numbered SQL + a tiny runner) can replace this; for now it closes the "tables never created" gap with a one-line volume.

**C. Healthchecks everywhere, `depends_on: service_healthy` everywhere**

Each FastAPI Dockerfile gets a `HEALTHCHECK` hitting `/health` (curl or python one-liner); `web` gets one on `/`. `web` then depends on `data-engine: service_healthy`. This turns "the stack is up" into a verifiable statement (`docker compose ps` shows `healthy`) — needed for G11 honesty and for the smoke script below.

**D. Environment files, not one `.env`**

- `.env.example` committed (all keys, no secrets) — `.gitignore` already whitelists it.
- `envs/*.env` git-ignored; one per temp env. Mandatory keys: `DB_PASSWORD`, `DATA_PROVIDER`, `WEB_PORT`.
- Per-service `config.py` defaults already read env vars, so no code change.

**E. The one rule that makes temp envs safe: only one stack may talk to live yfinance**

Every temp stack runs on the same public IP. Three stacks each running a 6-minute scan is three times the traffic Yahoo sees from you. So:

- Add a **`fixture` data provider** to `data-engine/providers/` implementing `DataProvider` by replaying recorded data from `services/data-engine/fixtures/` (one recorded scan: candidates list, daily + hourly bars for the winners, enrichment dicts — saved as parquet/JSON by a one-time, deliberately-run recording script with the normal delays).
- `docker-compose.prod.yml` defaults `DATA_PROVIDER=fixture`. A temp env therefore boots, scans in seconds, fills Postgres and the dashboard with realistic data, and **never touches the network**.
- Exactly one designated stack (your "live" one) sets `DATA_PROVIDER=yfinance`. The cooldown and single-scan lock already exist per process; the fixture default is what extends that guarantee across stacks.
- Bonus: the fixture provider is also what Signal Engine and Risk Shield tests will use (G7), and it makes CI possible without any API call.

**F. Scripts (`scripts/`)**

| Script | Does |
|---|---|
| `env-up.sh <name>` | Validates env file, `compose -p tf-<name> ... up -d --build`, waits for all healthy, prints URLs |
| `env-smoke.sh <name>` | `GET /health` on every service, `POST /scan/run`, poll `/scan/status`, assert `passedCount > 0`, `GET /scan/history` returns 1 row (proves DB write) |
| `env-down.sh <name>` | `compose -p tf-<name> down -v --remove-orphans` |
| `env-ls.sh` | Lists running `tf-*` projects |

Plain bash, no Make dependency. Each one is small enough to read in full.

**G. Local ↔ cloud continuity (Part 1 / Part 8)**

Nothing above blocks the "same containers on a VPS" step: copy the repo, copy one env file, run `env-up.sh`. Cloud Run / Supabase managed is a later env-var swap (`DATABASE_URL`, `REDIS_URL`), as Part 8 says. `infra/cloud-run/` stays empty until then.

### 5.3 Acceptance for the Docker phase

- `scripts/env-up.sh demo` on a clean machine → all 7 containers `healthy` in < 5 min (first build).
- `scripts/env-smoke.sh demo` passes with `DATA_PROVIDER=fixture` and no network access (verify with Docker network `internal: true` on a test run).
- Two envs (`demo`, `demo2`) run simultaneously on different `WEB_PORT`s.
- `env-down.sh demo` leaves no `tf-demo*` container, network, or volume.

---

## 6. Phased build plan

Each phase: goal → deliverable → files → tests → notes. Order is chosen so every phase ends with something runnable in a temp env, and so downstream services never need a new live-API path.

### Phase 0 — Shrink & make the stack boot (½–1 day)

- Deletions from §3 (freeze `scanner/`, remove pipeline #2, remove "Scanner 1" tab, drop `yahoo-finance2` + `firebase`).
- `.env.example`; verify `docker compose up` actually works with a real `.env` (first time ever, as far as the repo shows).
- Update `docs/overview.md` and `CLAUDE.md` "three pipelines" wording to "one pipeline + frozen reference".
- Tests: existing `test_scanner_pipeline.py` still green; `npm run build` green with the deps removed.
- Commit: `refactor: single scanner path — freeze scanner/, drop JS reimplementation`.

### Phase 1 — Docker temp environments (1–2 days)

Everything in §5. Includes the **fixture provider + recording script**, because §5.E depends on it and it is small (implements the same 6 methods as `yfinance_provider.py`, reading from disk).

- Files: `docker-compose.prod.yml`, `Dockerfile` healthchecks ×5, `scripts/env-*.sh`, `providers/fixture_provider.py`, `tests/record_fixture.py` (live, manual, run once), `tests/test_fixture_provider.py`.
- Notes: the recording run is a **real scan** — announce it, run it once, commit the fixture (size check: bars for ~40 winners × 1y daily + 3mo hourly is a few MB as parquet).
- Commit(s): `feat: docker prod overlay + env scripts`, `feat: fixture data provider`.

### Phase 2 — Data Engine additions that Signal Engine and Risk Shield need (2–3 days)

The Documents' Part 3 asks for more than we need now. Build only what Layer 2/3 consume:

1. **Persist bars for scan winners.** At the end of a scan, write compact daily (1y) and hourly (3mo) OHLCV for the passed stocks to `data_engine.ohlcv_bars` (new table: `ticker, interval, ts, o, h, l, c, v`, PK `(ticker, interval, ts)`) and drop the DataFrames as today. This is the **single design change that makes Signal Engine safe** — it reads bars we already paid for; no second download.
2. **`GET /stock/{ticker}/ohlcv?interval=1d|1h`** — served from Postgres only. Returns 404 if the ticker isn't a recent winner. Never falls through to the provider.
3. **`GET /indicators/{ticker}`** — computed from stored bars via existing `indicators/technical.py`.
4. **Fundamental score (Part 3 "NEW")** — *deferred to Phase 5+*. yfinance `.info` fundamentals are unstable (Part 2 gotcha; AGENTS.md says wrap in try/except). Layer 1 works without it; add when a paid provider exists.
5. `shared/` importability: either (a) `COPY ../shared` via a repo-root build context for each service, or (b) accept duplication and add a test that asserts `cache.py` constants equal `shared/constants.py`. Recommend **(b)** now, (a) when a third consumer appears (G10).

- Tests: migration applies on fresh Postgres in a temp env; `test_ohlcv_store.py` (mocked pool); endpoint tests with FastAPI `TestClient` and fixture provider.
- Commit: `feat: persist winner OHLCV + /stock/{t}/ohlcv + /indicators/{t}`.

### Phase 3 — Signal Engine, in three vertical slices (1–2 weeks)

Follows Part 4 structure (`zones/`, `signals/`, `models/`) but ships in slices that each end runnable:

**3a. Zones only.** `zones/fractal_levels.py`, `volume_clusters.py`, `pivot_points.py`, `zone_merger.py` (0.5% clustering, the scoring rubric from Part 4). `GET /zones/{ticker}` reads bars from Data Engine `/stock/{t}/ohlcv`, writes `signals.zones`. Pure math → 100% unit-testable on fixture bars.
Acceptance: for a fixture ticker, returns top-3 support / top-3 resistance with scores and `methods[]`.

**3b. Signals.** `signals/detector.py` (within 1% of a zone + ≥2 of 4 confirmations), `calculator.py` (entry ± 0.3%, SL = zone − 1 ATR, TP1–3 from next zones), `risk_reward.py` (reject < 1.5:1), `confidence.py` (zone 0–40, confirmations 0–30, fundamental 0–15 → **0 until Phase 2.4 exists**, AI 0–15 → 0 until Phase 6, risk penalty 0 to −20 → 0 until Phase 4). `POST /signals/generate`, `GET /signals/active`, `GET /signals/{id}`. Publishes `tf:signal:new`.
Acceptance: fixture scan → ≥1 signal with the exact JSON shape in Part 4 (camelCase at the API edge, as `CLAUDE.md` requires).

**3c. Lifecycle + listeners.** `signals/lifecycle.py` state machine (PENDING → TRIGGERED → HIT_TPx / STOPPED_OUT / EXPIRED / ADJUSTED, 48h expiry) with transitions logged to `signals.signal_logs`. Redis subscriber on `tf:scan:complete` auto-runs generation. `GET /signals/history`, `POST /signals/{id}/update`.
**Design constraint:** lifecycle needs *current price* to detect triggers. Live intraday quoting for 30–60 tickers every few minutes is a new external-API load. Plan: (i) in fixture mode, replay the hourly bars; (ii) in live mode, one batched `yf.download(period="1d", interval="15m")` for the watchlist every 15 min (≤3 calls of 20 tickers with the standard delay) — **this needs its own G1 approval and a canary test** before it runs against live Yahoo. Nothing more frequent.

- Tests: `test_zones.py`, `test_signals.py`, `test_calculator.py`, `test_lifecycle.py` — all on fixture data, no network.
- Commits per slice.

### Phase 4 — Risk Shield (3–5 days)

Part 5's 6 monitors, health calculator, regime classifier, alert manager with throttling. Deliberate simplifications:

- **One batched download** for all core tickers (`^VIX, SPY, QQQ, XLK, XLU, XLP, XLV, XLY, XLF, TLT, GLD, UUP` = 12 tickers, one `yf.download` call) per check. Cache in Redis with the 5-minute TTL from `shared/constants.py`. Check interval 5 min during market hours = ~80 calls/day. State this number in the G1 spec so we're consciously accepting it.
- **Breadth monitor:** "% of S&P 500 above 50/200 MA" from constituents is 500 more tickers — **not acceptable** under G6. Substitute: `RSP/SPY` ratio (equal-weight vs cap-weight) as a participation proxy, plus A/D from the Finviz screener counts we already scrape. Documented deviation from Part 5.
- Scheduler: `asyncio` loop inside the FastAPI lifespan (market-hours gated by the existing `market_status.py` logic). Huey is not needed for one periodic task (G10).
- Endpoints per Part 5; publishes `tf:risk:health` / `tf:risk:alert`; Signal Engine (3b) subscribes and applies the Part 4 reaction table (raise threshold to 75 / pause / force-close).
- Tests: each monitor on synthetic frames (VIX 45 → score 5, etc.), throttling logic, regime boundaries.

### Phase 5 — Web App, incremental (1–2 weeks)

Part 6 is a big redesign. Do it in the order users get value, keeping the current page working throughout:

1. `/dashboard` route = current Pro Scanner view moved into an `AppShell` with sidebar; `WatchlistGrid` from merged `StockCard`/`ProStockCard`; keep `SectorTabs`.
2. `RiskGauge` (Phase 4 `/market/health` via a Next.js API proxy `/api/risk/health`).
3. `SignalPanel` + `SignalCard` (Phase 3b `/signals/active` via `/api/signals`).
4. `/stock/[ticker]` with `lightweight-charts` candlesticks (bars from `/stock/{t}/ohlcv`) + `ZoneOverlay` from `/zones/{t}`.
5. Real-time: **poll every 30s first** (G10). SSE via a Redis subscriber in a Next.js route handler once polling feels wrong, not before.
6. Auth (Supabase Auth per Part 2/6) — **last** in this phase, and only when there's a second user. Until then the dashboard is single-tenant behind Docker's network. `users.*` tables and RLS wait for this.

All backend URLs stay server-side (existing proxy pattern in `web/app/api/scanner/pro/route.js`); browser never calls FastAPI directly (Part 6).

### Phase 6 — AI Agent (1 week, after ≥ 4 weeks of signal outcomes exist)

Part 7 as written: `providers/{base,claude_provider,gemini_provider}.py`, `grading/signal_grader.py`, prompts in `prompts/*.md`, strict JSON output, retries → fallback, daily call cap in config. The learning loop needs `signals.signals` rows with outcomes, so this phase is date-gated on Phase 3c having run live for a while. `LLM_PROVIDER` env default: compose currently says `gemini`; Part 7 says `claude` primary. Decide at G1 time (see §8).

---

## 7. Deliberate deviations from the Documents

| Documents say | This plan does | Why |
|---|---|---|
| Huey task queue (Part 2/3/5/7) | `BackgroundTasks` + one asyncio loop per service | One periodic task per service doesn't justify a worker process, a beat process, and a broker config. Revisit when retries/backoff across restarts are actually needed. |
| Signal Engine fetches OHLCV from Data Engine on demand (Part 4) | Data Engine **persists winner bars at scan time**; endpoint serves from DB only | Removes a second live download per stock. G6. |
| Breadth from S&P 500 constituents (Part 5) | `RSP/SPY` ratio + Finviz A/D counts | 500 extra tickers per 5-min check is not survivable on yfinance. |
| Supabase Auth + RLS in Phase 4 of the Documents' timeline | Auth last, only when multi-user | G10. Single-tenant dashboard behind Docker network is sufficient for months. |
| Remove Firebase now (Part 2) | Remove in Phase 0 | Agree — nothing depends on it. |
| Fundamental score as a Data Engine step (Part 3) | Deferred until a paid fundamentals provider | yfinance `.info` is the least reliable part of the library (AGENTS.md Part 2). |
| SSE from day one (Part 6) | 30s polling first | Simplest thing that works; SSE is a contained upgrade later. |
| Cloud Run configs in `infra/cloud-run/` | Stays empty until a VPS run has proven the compose stack | Part 1's own path is "single VPS with the same compose" before Cloud Run. |

---

## 8. Decisions I need from you before Phase 0 (G1)

1. **Confirm "continue + shrink"** over a fresh repo (§3).
2. **OK to delete** `web/lib/scanner/*`, `web/lib/data/*`, the "Scanner 1" tab and `/api/scanner/run`, and to treat `scanner/` as frozen reference (not deleted)?
3. **Fixture-by-default for temp envs** (§5.E): every non-designated stack replays recorded data. Yes/no?
4. **One live recording run** is required to create the fixture (a full ~6-minute scan). When do you want it run?
5. **Phase order**: Data Engine additions → Signal Engine → Risk Shield → Web → AI, as above? Or Risk Shield before Signal Engine (it's smaller and gives the dashboard a visible new thing sooner)?
6. **Signal lifecycle live pricing** (Phase 3c): accept one batched 15-minute intraday download for the watchlist during market hours, or run lifecycle on hourly bars only (cheaper, slower triggers)?
7. **AI provider default**: compose says `gemini`, Part 7 says `claude` primary with Gemini fallback. Pick one for the env default (affects nothing until Phase 6).

---

## 9. Things this plan will not do

- No new code path to yfinance/Finviz without a G1 spec that states calls-per-day.
- No running bare `pytest` in `services/data-engine` (live-scan collection hazard, see `CLAUDE.md`).
- No hand-editing `scanner/results.json` / `status.json`.
- No writes across service schemas; Signal Engine reads Data Engine data over HTTP only.
- No Kubernetes, no Cloud Run, no Supabase managed until the compose stack has run on a VPS.
