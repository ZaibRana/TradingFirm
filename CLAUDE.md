# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project

TradingFirm — a day-trading system that screens the market, applies technical filters, and surfaces trade candidates. FastAPI microservices + a Next.js dashboard, with Postgres and Redis as shared infrastructure.

**Service status** (don't assume otherwise): `data-engine` and the web dashboard are functional. `signal-engine`, `risk-shield`, and `ai-agent` are empty FastAPI scaffolds — only `/health` and `/` exist.

## Read `.agents/AGENTS.md` first

12 global rules + project rules take priority over generic habits. The ones most likely to bite:

- **G1 — Ask before building.** 3-sentence spec approved before touching files for any new feature/service.
- **G3.5 — Consult before fixing.** On an error, present the root cause and 2 options; don't auto-apply a fix.
- **G6 — Protect external APIs.** Never run untested code against live yfinance/Finviz at scale; delays and canary batches are mandatory. Getting the user's IP rate-limited is the #1 thing to avoid.
- **G7 — Tests alongside code.** Every module gets a unit test; tests never call external APIs (mock the provider).
- **G8 — Clean up memory.** `del` DataFrames + `gc.collect()` after use; never store raw DataFrames in `app.state`.

## Docs discipline

- **Active plan: `docs/plan-analyst-watcher.md`.** Read it before any part. Its §0 decisions are binding. The user says which part to build.
- **Plan files are read-only** once approved. If a part proves the plan wrong, record the change in `docs/decisions.md` ("supersedes D-n") and note it in `docs/progress.md`.
- **`docs/overview.md` describes what exists now**, never future state. Update it when a part changes architecture, adds a service/endpoint, or adds a third-party API or library.
- **`docs/decisions.md` is append-only.** One entry per decision: date, decision, why, supersedes.
- **`docs/progress.md`**: one row per part (status, commit, date, notes). Update at the end of every part, before the commit.
- **Never delete a plan file.** Superseded plans get a status banner and stay in `docs/`.

## Commands

### Data engine (Python)
```bash
cd services/data-engine
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```
Tests run inside the dev container (host pandas ≠ pinned version); deps in `requirements-dev.txt`, not the prod image:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d data-engine
docker exec tf-data-engine pip install -r requirements-dev.txt
docker exec tf-data-engine pytest tests/test_fixture_provider.py tests/test_provider_factory.py tests/test_scanner_pipeline.py -v
docker compose up -d data-engine     # restore prod build
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
Requires `.env` with `DB_PASSWORD` set — compose fails fast without it.

## Conventions

- **Commit prefixes** `feat:`, `fix:`, `refactor:`, `docs:`; commit after every tested chunk (G12).
- **API JSON is camelCase, Python is snake_case.** `scanners/models.py` aliases every Pydantic field (`market_cap` → `marketCap`). Add new response fields the same way.
- **yfinance/Finviz limits**: ≤20 tickers per `yf.download()`, 3s between batches, 1.5–2s between `yf.Ticker` calls, `threads=False`, never pass `session=`. Rationale in `.agents/AGENTS.md` Part 2.

## Never touch / handle with care

- **Never run bare `pytest` or `pytest tests/`** in `services/data-engine`: `tests/full_scan_test.py` matches discovery and fires a real Finviz + yfinance scan on collection. Always name the `test_*.py` files.
- **`tests/smoke_test_pipeline.py`, `tests/full_scan_test.py`, `tests/record_fixture_live.py`** are live-API scripts. Run manually and deliberately, never in CI.
- **One live scan pipeline: `services/data-engine`** (proxied by `web/app/api/scanner/pro/route.js`). `scanner/` is a frozen reference — don't build on it (`scanner/README.md`). Its `results.json`/`status.json` are generated output.
- **Migrations must be re-runnable**: `IF NOT EXISTS` everywhere, no plain `INSERT` seeds. Why: `scripts/migrate.sh` header, `docs/decisions.md` 2026-09-05.
- **Never write into another service's Postgres schema** (`data_engine`, `signals`, `risk`, `users`, `ai`). Cross-service communication is HTTP + Redis pub/sub only.
