# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

TradingFirm — a day-trading system that screens the market, applies technical filters, and surfaces trade candidates. FastAPI microservices + a Next.js dashboard, with Postgres and Redis as shared infrastructure.

**Service status** (don't assume otherwise): `data-engine` and the web dashboard are functional. `signal-engine`, `risk-shield`, and `ai-agent` are empty FastAPI scaffolds — only `/health` and `/` exist.

## Read `.agents/AGENTS.md` first

12 global rules + project-specific rules govern how work is done here and take priority over generic habits. The ones most likely to bite:

- **G1 — Ask before building.** Get a 3-sentence spec approved before touching files for any new feature/service.
- **G3.5 — Consult before fixing.** When something errors, present the root cause and 2 options; don't auto-apply a fix.
- **G6 — Protect external APIs.** Never run untested code against live yfinance/Finviz at scale — delays and canary batches are mandatory, and getting the user's IP rate-limited is priority #1 to avoid.
- **G7 — Tests alongside code.** Every module gets a unit test; tests must not call external APIs (mock the provider).
- **G8 — Clean up memory.** `del` DataFrames + `gc.collect()` after use; never store raw DataFrames in `app.state`.

## Commands

### Data engine (Python)
```bash
cd services/data-engine
pip install -r requirements.txt
uvicorn main:app --reload --port 8001          # run standalone

pytest tests/test_scanner_pipeline.py -v        # run the safe unit tests
```

### Web dashboard (Next.js)
```bash
cd web
npm install
npm run dev      # localhost:3000
npm run build
npm run lint
```

### Full stack (Docker)
```bash
docker compose up -d                                              # prod-like
docker compose -f docker-compose.yml -f docker-compose.dev.yml up # hot-reload dev

curl http://localhost:8001/health
curl -X POST http://localhost:8001/scan/run -H "Content-Type: application/json" \
  -d '{"price_min": 10, "price_max": 40}'
curl http://localhost:8001/scan/status
curl http://localhost:8001/scan/results
```
Requires a `.env` with `DB_PASSWORD` set — compose fails fast (`:?`) without it.

## Conventions

- **Commit prefixes**: `feat:`, `fix:`, `refactor:`, `docs:` — commit after every tested chunk of work (per G12).
- **API JSON is camelCase, Python internals are snake_case.** `services/data-engine/scanners/models.py` aliases every Pydantic field (e.g. `market_cap` → `marketCap`, `duration_seconds` → `durationSeconds`) so the FastAPI response matches what `web/` expects. Add new response fields the same way — don't return snake_case to the frontend.
- **yfinance/Finviz calls are rate-limit-sensitive by convention, not just by rule**: batch size ≤20 tickers per `yf.download()`, 3s between batches, 1.5–2s between `yf.Ticker` calls, `threads=False` always, never pass `session=`. Full rationale in `.agents/AGENTS.md` Part 2.

## Never touch / handle with care

- **Do not run bare `pytest` or `pytest tests/`** in `services/data-engine`. `tests/full_scan_test.py` matches pytest's default `*_test.py` discovery pattern and has an unguarded `asyncio.run(main())` at module scope — collecting it fires a real Finviz + yfinance scan. Always target `test_scanner_pipeline.py` explicitly (or any new `test_*.py` file).
- **`tests/smoke_test_pipeline.py` and `tests/full_scan_test.py`** are live-API verification scripts, not pytest suites — run manually and deliberately (`python3 tests/full_scan_test.py`), never in CI.
- **The scan pipeline is implemented three separate times** — know which one you're editing before changing scan logic, since a fix in one does not propagate to the others:
  1. `scanner/pro_scan.py` (+ `scan.py`, `step1_finviz.py`…`step4_enrich.py`) — original standalone Python reference, run via local venv.
  2. `web/lib/scanner/*.js` + `web/app/api/scanner/{discover,filter,technical}/route.js` — independent JS reimplementation using `yahoo-finance2`, no Python involved.
  3. `services/data-engine` — the FastAPI port of `pro_scan.py`. This is the one wired to the dashboard's primary "Pro Scanner" tab (`web/app/api/scanner/pro/route.js` proxies to it). `web/app/api/scanner/run/route.js` is the legacy path that still `exec`s `scanner/scan.py` directly.
- **`scanner/results.json` and `scanner/status.json`** are generated output, not source — don't hand-edit them.
- **Never write into another service's Postgres schema.** Each service owns its own (`data_engine`, `signals`, `risk`, `users`, `ai` in `infra/supabase/migrations/001_initial_schema.sql`); cross-service communication is HTTP + Redis pub/sub only.
