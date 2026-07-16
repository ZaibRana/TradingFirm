# TradingFirm

Professional day trading system with automated stock scanning, signal detection, and risk management.

## What This Is

An end-to-end trading platform that automates the stock discovery pipeline: screen thousands of stocks, apply technical filters, and surface the best day trading candidates — all in real-time. Built as a microservices architecture, designed to scale from a personal tool to a production-grade system.

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Backend** | Python 3.12, FastAPI | Microservices (4 services) |
| **Frontend** | Next.js 15, React 19 | Real-time dashboard |
| **Database** | PostgreSQL 16 | Scan history, stock data, trade logs |
| **Cache** | Redis 7 | Scan status, pub/sub, rate limit tracking |
| **Infrastructure** | Docker Compose | Local dev (7 containers) |
| **Data Sources** | yfinance, finvizfinance | Market data (dev/testing only) |

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Data Engine │────▶│Signal Engine │────▶│  Risk Shield │────▶│   AI Agent   │
│  (Port 8001) │     │  (Port 8002) │     │  (Port 8003) │     │  (Port 8004) │
└──────┬───────┘     └──────────────┘     └──────────────┘     └──────────────┘
       │                                                              │
       ▼                                                              ▼
┌──────────────┐                                              ┌──────────────┐
│  PostgreSQL  │◀─────────────────────────────────────────────│ Web Dashboard│
│  (Port 5432) │                                              │  (Port 3000) │
└──────────────┘                                              └──────────────┘
       ▲
       │
┌──────────────┐
│    Redis     │
│  (Port 6379) │
└──────────────┘
```

### Services

| Service | Status | Description |
|---------|--------|-------------|
| **Data Engine** | ✅ Built | Finviz screening → yfinance OHLCV → technical filters → enrichment |
| **Signal Engine** | 🔲 Scaffolded | Entry/exit signal detection (zones, patterns) |
| **Risk Shield** | 🔲 Scaffolded | Position sizing, drawdown monitoring, kill switches |
| **AI Agent** | 🔲 Scaffolded | Trade grading, pattern recognition |
| **Web Dashboard** | ✅ Built | Scan results, stock cards, market status display |

## Scanner Pipeline

The core of this system — based on the reference implementation in `scanner/pro_scan.py`:

1. **Finviz Screen** → ~7,000 US stocks → ~650 candidates (price, volume, market cap filters)
2. **Daily Download** → Bulk OHLCV (1 year) via yfinance
3. **Technical Filters** → ATRP 2.5-6%, RVOL >1.0, 52-week 10-90%, EMA trends
4. **5M Tradability Check** *(advanced)* → Day direction, green/red ratio, big body candles
5. **Enrichment** → Sector, industry, float, market cap gate ($500M), float gate (20M-1B)
6. **Quality Sort** → RVOL × ATRP (best opportunities first)

## Database Schema

5 schemas, 16 tables:

- `scans` — Scan history and results
- `signals` — Entry/exit signals and zones
- `strategies` — Trading strategies and performance
- `watchlist` — User watchlists and alerts
- `audit` — Trade log, daily journal, error tracking

## Project Structure

```
TradingFirm/
├── .agents/AGENTS.md         # Development rules (19 rules + lessons learned)
├── services/
│   ├── data-engine/          # FastAPI — scanner, providers, indicators
│   ├── signal-engine/        # FastAPI — entry/exit signals
│   ├── risk-shield/          # FastAPI — position sizing, risk limits
│   └── ai-agent/             # FastAPI — trade grading
├── web/                      # Next.js dashboard
├── scanner/pro_scan.py       # Original working scanner (reference)
├── infra/                    # Docker, SQL migrations
├── shared/                   # Common models and constants
├── Documents/                # Architecture specs, critical reviews
├── docker-compose.yml        # Production compose
└── docker-compose.dev.yml    # Dev compose with hot-reload
```

## Running Locally

```bash
# Start all services
docker compose up -d

# Check health
curl http://localhost:8001/health

# Run a scan
curl -X POST http://localhost:8001/scan/run \
  -H "Content-Type: application/json" \
  -d '{"price_min": 10, "price_max": 40}'

# Check scan status
curl http://localhost:8001/scan/status
```

## Development Rules

All rules are in `.agents/AGENTS.md`. Key principles:

- **One Strike Rule** — if something fails, stop and investigate immediately
- **Test 1 → 5 → N** — never scale without verifying small first
- **No scraper libraries in production** — yfinance/finviz are for dev only
- **Protect the IP** — rate limits, delays, cooldowns on all external API calls
- **Strict garbage control** — clean up DataFrames, connections, and memory
- **Commit alongside development** — push to GitHub after each tested feature

## Known Issues

- `finvizfinance==1.3.0` doubles ticker first characters (patched in code)
- `yfinance` in Docker needs `threads=False` + browser session headers
- Pipeline currently downloads hourly data for all tickers (optimization pending)
- Step 3.5 (5M tradability check) not yet ported to data-engine service

## End Goal

A complete trading assistant that:
1. **Scans** the market automatically before and during trading hours
2. **Detects** entry/exit signals using technical zones and patterns
3. **Manages risk** with position sizing, drawdown limits, and kill switches
4. **Grades trades** with AI to improve over time
5. Runs as a **self-contained Docker stack** — one command to start everything
