# Part 3: Service 1 — Data Engine (FastAPI)

The Data Engine is the **foundation of everything**. It fetches market data, runs your scanner filters, calculates indicators, and enriches stocks with fundamentals. Every other service depends on its output.

---

### What It Does (Responsibilities)

| Responsibility | Description | Existing Code |
|---------------|-------------|---------------|
| **Candidate screening** | Fetch 1500+ tickers from Finviz (dev) or FMP (prod) | [get_candidates()](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L50-L115) |
| **Bulk data download** | Get daily + hourly OHLCV for all candidates | [download_data()](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L120-L136) |
| **Indicator calculation** | EMA, ATR, ATRP, MACD, RVOL, ADX | [ema(), calc_atr()](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L150-L177) + [step3_filters.py](file:///Users/zubair/Desktop/TradingFirm/scanner/step3_filters.py) |
| **Filter pipeline** | Apply all technical filters (ATRP, RVOL, EMA alignment, 52w position, 5M tradability) | [apply_filters()](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L182-L260) |
| **Enrichment** | Float, sector, industry, market cap, news | [enrich()](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L265-L330) |
| **Fundamental scoring** | P/E, EPS growth, debt/equity, ROE — **NEW** | New code |
| **Data serving** | Expose results via REST API to other services | New code (currently saves to `pro_results.json`) |

---

### Internal Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     DATA ENGINE (FastAPI)                       │
│                     Port: 8001                                  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  API Layer (main.py)                                      │  │
│  │                                                           │  │
│  │  GET  /health              → service health check         │  │
│  │  POST /scan/run            → trigger full scan pipeline   │  │
│  │  GET  /scan/results        → latest watchlist             │  │
│  │  GET  /scan/status         → is scan running? progress?   │  │
│  │  GET  /stock/{ticker}      → single stock full data       │  │
│  │  GET  /stock/{ticker}/ohlcv → raw OHLCV candles           │  │
│  │  GET  /fundamentals/{ticker} → fundamental score + data   │  │
│  │  GET  /indicators/{ticker}  → calculated indicators       │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Pipeline (scanners/pipeline.py)                          │  │
│  │                                                           │  │
│  │  Step 1: Screen candidates ──→ providers/                 │  │
│  │  Step 2: Bulk download     ──→ providers/                 │  │
│  │  Step 3: Apply filters     ──→ indicators/                │  │
│  │  Step 3.5: 5M tradability  ──→ indicators/                │  │
│  │  Step 4: Enrich            ──→ providers/                 │  │
│  │  Step 5: Fundamental score ──→ providers/ (NEW)           │  │
│  │  Step 6: Save to DB + Cache                               │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Providers (DATA ADAPTER LAYER)                           │  │
│  │  ┌─────────────────────┐  ┌─────────────────────────────┐│  │
│  │  │ yfinance_provider.py│  │ fmp_provider.py             ││  │
│  │  │ (DEV — active now)  │  │ (PROD — swap later)         ││  │
│  │  │                     │  │                              ││  │
│  │  │ get_candidates()    │  │ get_candidates()             ││  │
│  │  │ get_ohlcv()         │  │ get_ohlcv()                  ││  │
│  │  │ get_fundamentals()  │  │ get_fundamentals()           ││  │
│  │  │ get_news()          │  │ get_news()                   ││  │
│  │  └─────────────────────┘  └─────────────────────────────┘│  │
│  │                                                           │  │
│  │  Selected by: DATA_PROVIDER=yfinance (env variable)       │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Indicators (pure math — no external dependencies)        │  │
│  │                                                           │  │
│  │  ema()          → Exponential Moving Average              │  │
│  │  calc_atr()     → Average True Range                     │  │
│  │  calc_atrp()    → ATR as % of price                      │  │
│  │  macd()         → MACD line, signal, histogram            │  │
│  │  adx()          → Average Directional Index               │  │
│  │  calc_rvol()    → Relative Volume (time-adjusted)         │  │
│  │  aggregate_4h() → Resample 1H → 4H candles               │  │
│  │  calc_sr()      → Support/Resistance levels (NEW)         │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

### File Structure

```
services/data-engine/
├── Dockerfile
├── requirements.txt
├── main.py                     ← FastAPI app + routes
├── config.py                   ← Settings (env vars, provider selection)
│
├── providers/                  ← DATA ADAPTER LAYER
│   ├── __init__.py             ← Factory: returns correct provider based on env
│   ├── base.py                 ← Abstract base class (interface contract)
│   ├── yfinance_provider.py    ← Dev provider (yfinance + finviz)
│   └── fmp_provider.py         ← Prod provider (FMP + Twelve Data)
│
├── indicators/                 ← PURE MATH (no API calls)
│   ├── __init__.py
│   ├── moving_averages.py      ← ema(), sma()
│   ├── volatility.py           ← calc_atr(), calc_atrp()
│   ├── momentum.py             ← macd(), adx(), rsi()
│   ├── volume.py               ← calc_rvol(), volume_profile()
│   ├── levels.py               ← calc_sr(), pivot_points() (NEW)
│   └── timeframes.py           ← aggregate_4h(), aggregate_weekly()
│
├── scanners/                   ← FILTER PIPELINES
│   ├── __init__.py
│   ├── pipeline.py             ← Main scan orchestrator (Step 1→6)
│   ├── technical_filters.py    ← ATRP, RVOL, EMA alignment, 52w
│   ├── tradability_filters.py  ← 5M checks (big body, vol distribution)
│   └── fundamental_filters.py  ← P/E, growth, debt filters (NEW)
│
├── models/                     ← Pydantic schemas
│   ├── __init__.py
│   ├── stock.py                ← StockResult, ScanResult models
│   └── indicators.py           ← IndicatorSet model
│
└── tests/
    ├── test_indicators.py      ← Unit tests for pure math
    ├── test_filters.py         ← Unit tests for filter logic
    └── test_providers.py       ← Integration tests for data fetch
```

---

### The Provider Interface (How API Swapping Works)

```python
# providers/base.py — The contract every provider must follow

from abc import ABC, abstractmethod
import pandas as pd

class DataProvider(ABC):
    """Every data provider (yfinance, FMP, etc.) implements this interface."""

    @abstractmethod
    async def get_candidates(self, price_min=10, price_max=40, 
                              min_volume=1_000_000) -> list[str]:
        """Return list of ticker symbols matching broad filters."""
        ...

    @abstractmethod
    async def get_ohlcv(self, ticker: str, period: str = "1y",
                         interval: str = "1d") -> pd.DataFrame:
        """Return DataFrame with columns: Open, High, Low, Close, Volume.
        Index: DatetimeIndex. All values adjusted for splits."""
        ...

    @abstractmethod
    async def get_bulk_ohlcv(self, tickers: list[str], period: str = "1y",
                              interval: str = "1d") -> dict[str, pd.DataFrame]:
        """Bulk download. Returns {ticker: DataFrame}."""
        ...

    @abstractmethod
    async def get_fundamentals(self, ticker: str) -> dict:
        """Return dict with keys: sector, industry, marketCap, 
        floatShares, pe, eps, revenueGrowth, debtToEquity, roe, 
        shortName, etc."""
        ...

    @abstractmethod
    async def get_news(self, ticker: str, limit: int = 3) -> list[dict]:
        """Return list of {title, url, publisher} dicts."""
        ...
```

```python
# providers/__init__.py — Factory pattern

import os
from .base import DataProvider

def get_provider() -> DataProvider:
    """Return the correct provider based on environment variable."""
    provider = os.getenv("DATA_PROVIDER", "yfinance")

    if provider == "yfinance":
        from .yfinance_provider import YFinanceProvider
        return YFinanceProvider()
    elif provider == "fmp":
        from .fmp_provider import FMPProvider
        return FMPProvider()
    else:
        raise ValueError(f"Unknown DATA_PROVIDER: {provider}")
```

> **Swapping from dev to prod = changing one env variable:**
> `DATA_PROVIDER=yfinance` → `DATA_PROVIDER=fmp`

---

### API Endpoints Detail

| Endpoint | Method | What it does | Who calls it |
|----------|--------|-------------|-------------|
| `/health` | GET | Returns `{"status": "ok", "last_scan": "..."}` | Docker health check, monitoring |
| `/scan/run` | POST | Triggers full pipeline (async via Huey). Returns `{"job_id": "..."}` | Web App (manual scan button), Huey cron (scheduled) |
| `/scan/status` | GET | Returns progress: `{"running": true, "step": "3/6", "progress": "Filtering 400 tickers"}` | Web App (progress bar) |
| `/scan/results` | GET | Returns latest watchlist from cache/DB. Fast (<50ms via Redis) | Web App (dashboard load), Signal Engine |
| `/scan/results?sector=Technology` | GET | Filtered results by sector | Web App (sector tabs) |
| `/stock/{ticker}` | GET | Full data for one stock: OHLCV + indicators + fundamentals + news | Web App (stock detail page) |
| `/stock/{ticker}/ohlcv` | GET | Raw candle data. Params: `?interval=1h&period=3mo` | Signal Engine (for S/R calculation) |
| `/fundamentals/{ticker}` | GET | Fundamental score + raw metrics | Web App, AI Agent |
| `/indicators/{ticker}` | GET | Calculated indicators: EMA, ATR, MACD, RSI, RVOL | Signal Engine, AI Agent |

---

### What Changes vs. Your Current Code

| Current (pro_scan.py) | New (Data Engine) | Why |
|----------------------|-------------------|-----|
| Runs as a CLI script | Runs as a FastAPI web server | Other services call it via HTTP. No more file-based communication |
| Saves to `pro_results.json` | Saves to PostgreSQL + Redis cache | Persistent, queryable, shareable across services |
| `yfinance.download()` called directly | Called via provider abstraction layer | Swappable. Never touch filter logic when changing data source |
| All logic in one 523-line file | Split into providers/, indicators/, scanners/ | Each piece is testable independently |
| `print()` for progress | Progress stored in Redis, queryable via `/scan/status` | Frontend can show a real progress bar |
| Sync execution (blocks for 6+ min) | Async background task via Huey | API returns immediately, scan runs in background |
| No error recovery | Failed tickers retry with backoff | One bad ticker doesn't kill the whole scan |

---

### What We Keep From Your Code (Unchanged Logic)

These are proven — we don't reinvent them, we just reorganize:

| Logic | Source | Destination |
|-------|--------|-------------|
| EMA calculation | [pro_scan.py L152-154](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L152-L154) | `indicators/moving_averages.py` |
| ATR calculation | [pro_scan.py L157-164](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L157-L164) | `indicators/volatility.py` |
| 4H aggregation | [pro_scan.py L167-177](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L167-L177) | `indicators/timeframes.py` |
| ATRP filter (2.5–6%) | [pro_scan.py L191-200](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L191-L200) | `scanners/technical_filters.py` |
| Time-adjusted RVOL | [pro_scan.py L202-222](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L202-L222) | `scanners/technical_filters.py` |
| 4H price > 50 EMA | [pro_scan.py L224-231](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L224-L231) | `scanners/technical_filters.py` |
| 1H EMA 20 > EMA 50 | [pro_scan.py L233-239](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L233-L239) | `scanners/technical_filters.py` |
| 52-week position | [pro_scan.py L241-249](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L241-L249) | `scanners/technical_filters.py` |
| 5M tradability checks | [pro_scan.py L378-456](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L378-L456) | `scanners/tradability_filters.py` |
| MACD + ADX | [step3_filters.py L25-64](file:///Users/zubair/Desktop/TradingFirm/scanner/step3_filters.py#L25-L64) | `indicators/momentum.py` |
| Enrichment | [pro_scan.py L265-330](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py#L265-L330) | `providers/yfinance_provider.py` |

---

### What's NEW (Not in Your Current Code)

| New Feature | What it does | Why |
|------------|-------------|-----|
| **Fundamental scoring** | Scores stocks 1–100 based on P/E, EPS growth, debt/equity, ROE, revenue growth | Layer 1 of System E — filters out junk |
| **S/R level calculation** | Identifies support/resistance zones from fractal highs/lows + volume clusters | Feeds into Signal Engine (Part 4) |
| **Background task execution** | Scans run as Huey background tasks, not blocking the API | Users don't wait 6 min for a response |
| **Redis caching** | Last scan results cached in Redis for <50ms reads | Dashboard loads instantly |
| **Database persistence** | Results saved to PostgreSQL with timestamps | Historical tracking, AI learning data |

---

### Dependencies (`requirements.txt`)

| Package | Purpose | Status |
|---------|---------|--------|
| `fastapi` | Web framework | ✅ Stable |
| `uvicorn[standard]` | ASGI server | ✅ Stable |
| `httpx` | Async HTTP client (replaces `requests`) | ✅ Stable |
| `pandas` | DataFrame operations | ✅ Stable |
| `numpy` | Numerical calculations | ✅ Stable |
| `pydantic` | Data validation (comes with FastAPI) | ✅ Stable |
| `redis` | Redis client for caching/pub-sub | ✅ Stable |
| `huey[redis]` | Background task queue | ✅ Stable |
| `asyncpg` | Async PostgreSQL driver | ✅ Stable |
| `yfinance` | Dev data provider | ⚠️ Dev only |
| `finvizfinance` | Dev screener | ⚠️ Dev only |

---

### Security Considerations

| Threat | Mitigation |
|--------|-----------|
| **API abuse** (someone spamming `/scan/run`) | Rate limiting via FastAPI middleware. Max 1 scan per 5 min per user |
| **Data injection** (malformed ticker symbols) | Pydantic validates all inputs. Tickers must match `^[A-Z]{1,5}$` |
| **Provider API keys exposed** | Keys stored in `.env`, loaded via `config.py`, never in code |
| **Denial of service via bulk download** | Max 2000 tickers per scan. Timeout per ticker (30s). Skip and continue on failure |
| **Stale data served as fresh** | Every response includes `last_updated` timestamp. Frontend shows staleness warning if > 4 hours |

---

# Part 4: Service 2 — Signal Engine (FastAPI)

The Signal Engine takes the Data Engine's curated watchlist and turns it into **actionable trades** — exact entry zones, stop losses, targets, and risk/reward ratios. It's where analysis becomes action.

---

### What It Does (Responsibilities)

| Responsibility | Description |
|---------------|-------------|
| **S/R zone detection** | Calculate support, resistance, supply, and demand zones for each watchlisted stock |
| **Signal generation** | When price approaches a zone with volume confirmation → generate BUY/SELL signal |
| **Entry/Exit calculation** | Exact entry zone, stop loss, and 1–3 take profit targets |
| **Risk/Reward scoring** | Calculate R:R ratio. Reject signals below 1.5:1 |
| **Confidence grading** | Combine technical strength + AI rating into a 1–100 confidence score |
| **Signal lifecycle** | Track signals from PENDING → TRIGGERED → HIT_TARGET / STOPPED_OUT / EXPIRED |
| **Crash response** | Listen to Risk Shield. If market health drops → pause new signals, tighten active stops |

---

### Internal Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    SIGNAL ENGINE (FastAPI)                      │
│                    Port: 8002                                   │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  API Layer (main.py)                                      │  │
│  │                                                           │  │
│  │  GET  /health                → service status             │  │
│  │  POST /signals/generate      → run signal scan on watchlist│ │
│  │  GET  /signals/active        → all live signals           │  │
│  │  GET  /signals/history       → past signals + outcomes    │  │
│  │  GET  /signals/{id}          → single signal detail       │  │
│  │  POST /signals/{id}/update   → manual close / adjust      │  │
│  │  GET  /zones/{ticker}        → S/R zones for a stock      │  │
│  │  GET  /zones/{ticker}/chart  → zones + OHLCV for charting │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Zone Detection (zones/)                                  │  │
│  │                                                           │  │
│  │  fractal_levels.py   → Swing high/low pivots              │  │
│  │  volume_clusters.py  → Price levels with heaviest volume  │  │
│  │  pivot_points.py     → Classic/Fibonacci/Camarilla pivots │  │
│  │  zone_merger.py      → Combine all methods → final zones  │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Signal Logic (signals/)                                  │  │
│  │                                                           │  │
│  │  detector.py         → "Is price near a zone + confirmed?"│  │
│  │  calculator.py       → Entry, SL, TP1/TP2/TP3 math       │  │
│  │  risk_reward.py      → R:R ratio, position sizing hints   │  │
│  │  confidence.py       → Technical score + AI agent query   │  │
│  │  lifecycle.py        → Track signal state transitions     │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Event Listeners                                          │  │
│  │                                                           │  │
│  │  Redis sub: "risk_shield:health_changed"                  │  │
│  │    → If health < 40: pause new signals                    │  │
│  │    → If health < 20: tighten all active stop losses       │  │
│  │                                                           │  │
│  │  Redis sub: "data_engine:scan_complete"                   │  │
│  │    → Auto-run signal generation on fresh watchlist        │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

### File Structure

```
services/signal-engine/
├── Dockerfile
├── requirements.txt
├── main.py                      ← FastAPI app + routes
├── config.py                    ← Settings (thresholds, R:R minimums)
│
├── zones/                       ← S/R DETECTION
│   ├── __init__.py
│   ├── fractal_levels.py        ← Swing highs/lows from price action
│   ├── volume_clusters.py       ← Volume-weighted price levels
│   ├── pivot_points.py          ← Classic, Fibonacci, Camarilla pivots
│   └── zone_merger.py           ← Combine methods → ranked zone list
│
├── signals/                     ← SIGNAL GENERATION
│   ├── __init__.py
│   ├── detector.py              ← Zone proximity + confirmation checks
│   ├── calculator.py            ← Entry, SL, TP math
│   ├── risk_reward.py           ← R:R ratio + position sizing
│   ├── confidence.py            ← Technical score + AI agent call
│   └── lifecycle.py             ← PENDING → TRIGGERED → outcome
│
├── models/
│   ├── __init__.py
│   ├── zone.py                  ← Zone, ZoneType (support/resistance/demand/supply)
│   └── signal.py                ← Signal, SignalStatus, SignalOutcome
│
└── tests/
    ├── test_zones.py
    ├── test_signals.py
    └── test_calculator.py
```

---

### How S/R Zones Are Detected (3 Methods Combined)

| Method | What it does | Strength |
|--------|-------------|----------|
| **Fractal swing levels** | Finds local highs/lows where price reversed (2 bars before and after the pivot) | Best for clean trend-following levels |
| **Volume clusters** | Groups candles by price range, finds where most volume traded (volume profile) | Best for institutional activity zones |
| **Pivot points** | Classic daily/weekly pivots (P, R1, R2, S1, S2) | Best for intraday reference levels |

```
Zone Merger Logic:
─────────────────
1. Run all 3 methods → get raw level lists
2. Cluster levels within 0.5% of each other → merge into "zones"
3. Score each zone:
   - Confirmed by 2+ methods? → +30 points
   - Volume cluster present?   → +25 points
   - Tested 2+ times?          → +20 points
   - Recent (within 20 bars)?  → +15 points
   - Clean bounce (wick, not break)? → +10 points
4. Rank zones by score → top 3 support, top 3 resistance
```

---

### How a Signal Is Generated

```
EVERY 15 MINUTES (or on scan_complete event):
│
├── 1. Fetch watchlist from Data Engine (/scan/results)
│      → 30-60 qualified stocks
│
├── 2. For each stock:
│   │
│   ├── Get OHLCV from Data Engine (/stock/{ticker}/ohlcv)
│   │
│   ├── Calculate S/R zones (zone_merger)
│   │
│   ├── Check: Is current price within 1% of a high-score zone?
│   │   │
│   │   NO  → Skip (not near any zone)
│   │   │
│   │   YES → Continue to confirmation
│   │
│   ├── Confirmation checks:
│   │   ├── Volume spike? (current bar volume > 1.5x average) ✅/❌
│   │   ├── Bullish candle at support? (hammer/engulfing)     ✅/❌
│   │   ├── RSI divergence?                                    ✅/❌
│   │   ├── EMA alignment from Data Engine?                    ✅/❌
│   │   └── Need at least 2 of 4 confirmations
│   │
│   ├── Calculate signal:
│   │   ├── Entry zone: Current zone midpoint ± 0.3%
│   │   ├── Stop Loss: Below zone by 1 ATR
│   │   ├── TP1: Next resistance zone (1:1 R:R minimum)
│   │   ├── TP2: Second resistance zone
│   │   ├── TP3: Major resistance (if exists)
│   │   └── R:R ratio must be ≥ 1.5:1 or signal is rejected
│   │
│   ├── Score confidence (0–100):
│   │   ├── Zone strength score (0–40)
│   │   ├── Confirmation count (0–30)
│   │   ├── Fundamental score from Data Engine (0–15)
│   │   ├── AI Agent rating (0–15) — if available, else skip
│   │   └── Risk Shield health penalty (0 to –20)
│   │
│   └── If confidence ≥ 60 → PUBLISH signal
│       ├── Save to PostgreSQL
│       ├── Cache in Redis
│       └── Publish to Redis: "signals:new" → Web App picks it up
│
└── Done
```

---

### Signal Lifecycle (State Machine)

```
    ┌──────────┐
    │ PENDING  │  Signal generated, price not yet at entry
    └────┬─────┘
         │ price enters entry zone
         ▼
    ┌──────────┐
    │TRIGGERED │  User alerted. Trade is "live"
    └────┬─────┘
         │
    ┌────┴─────────────┬──────────────┬──────────────┐
    │                  │              │              │
    ▼                  ▼              ▼              ▼
┌──────────┐  ┌──────────────┐ ┌──────────┐ ┌──────────┐
│HIT_TP1   │  │ STOPPED_OUT  │ │ EXPIRED  │ │ ADJUSTED │
│HIT_TP2   │  │ (SL hit)     │ │ (48h no  │ │ (Risk    │
│HIT_TP3   │  │              │ │  trigger)│ │  Shield) │
└──────────┘  └──────────────┘ └──────────┘ └──────────┘
```

| State | What happens |
|-------|-------------|
| **PENDING** | Signal exists but price hasn't reached entry zone yet. Show to user as "watching" |
| **TRIGGERED** | Price entered the zone. Push notification sent. Timer starts |
| **HIT_TP1/TP2/TP3** | Price reached target. Signal recorded as WIN with R:R achieved |
| **STOPPED_OUT** | Price hit stop loss. Signal recorded as LOSS |
| **EXPIRED** | 48 hours passed without trigger. Signal auto-removed |
| **ADJUSTED** | Risk Shield forced SL tightening or early exit |

---

### What the Signal Card Looks Like (Data Model)

```json
{
  "id": "sig_20260715_MSFT_001",
  "ticker": "MSFT",
  "name": "Microsoft Corporation",
  "direction": "LONG",
  "status": "PENDING",
  "created_at": "2026-07-15T14:30:00Z",

  "entry_zone": { "low": 419.50, "high": 421.20 },
  "stop_loss": 412.80,
  "targets": {
    "tp1": { "price": 428.00, "rr": "1.6:1" },
    "tp2": { "price": 435.50, "rr": "3.2:1" },
    "tp3": { "price": 442.00, "rr": "4.8:1" }
  },

  "confidence": 78,
  "confidence_breakdown": {
    "zone_strength": 35,
    "confirmations": 22,
    "fundamental_score": 12,
    "ai_rating": 9,
    "risk_penalty": 0
  },

  "zone": {
    "type": "SUPPORT",
    "level": 420.00,
    "strength_score": 85,
    "methods": ["fractal", "volume_cluster"],
    "times_tested": 3
  },

  "context": {
    "sector": "Technology",
    "atrp": 3.2,
    "rvol": 2.1,
    "fundamental_score": 82,
    "market_health": 72
  }
}
```

---

### How Signal Engine Responds to Risk Shield

| Risk Shield health | Signal Engine reaction |
|:---:|---|
| **70–100** (Healthy) | Normal operation. All signals generated as usual |
| **40–69** (Cautious) | New signals require confidence ≥ 75 (raised from 60). Warning label on all signals |
| **20–39** (Danger) | No new signals generated. Active signals: SL tightened by 50% |
| **0–19** (Critical) | All active signals force-closed. "PROTECT CAPITAL" alert pushed to all users |

---

### API Endpoints Detail

| Endpoint | Method | What it does | Who calls it |
|----------|--------|-------------|-------------|
| `/health` | GET | Service health + last signal time | Docker, monitoring |
| `/signals/generate` | POST | Run signal detection on current watchlist | Huey cron (every 15 min), manual trigger |
| `/signals/active` | GET | All PENDING + TRIGGERED signals | Web App (signal cards) |
| `/signals/active?ticker=MSFT` | GET | Signals for one stock | Web App (stock detail) |
| `/signals/history` | GET | Past signals with outcomes (wins/losses) | Web App (track record), AI Agent (learning) |
| `/signals/history?days=30` | GET | Last 30 days of signal outcomes | AI Agent (pattern analysis) |
| `/signals/{id}` | GET | Full signal detail | Web App (signal card expand) |
| `/signals/{id}/update` | POST | User closes trade manually, logs actual P&L | Web App (trade tracker) |
| `/zones/{ticker}` | GET | S/R zones for a stock | Web App (chart overlay) |
| `/zones/{ticker}/chart` | GET | Zones + OHLCV candles combined | Web App (interactive chart) |

---

### Dependencies (`requirements.txt`)

| Package | Purpose | Status |
|---------|---------|--------|
| `fastapi` | Web framework | ✅ Stable |
| `uvicorn[standard]` | ASGI server | ✅ Stable |
| `httpx` | Async calls to Data Engine + AI Agent | ✅ Stable |
| `pandas` | Zone calculations on DataFrames | ✅ Stable |
| `numpy` | Math for zone scoring, R:R calc | ✅ Stable |
| `redis` | Subscribe to events, cache signals | ✅ Stable |
| `asyncpg` | Async PostgreSQL driver | ✅ Stable |
| `pydantic` | Signal/Zone data models | ✅ Stable |

---

### Security Considerations

| Threat | Mitigation |
|--------|-----------|
| **Signal tampering** | Signals are immutable once created. Only `status` field can change, and only via valid state transitions |
| **Fake trade logging** | User-reported P&L is stored separately from system-calculated outcomes. Never mixed |
| **Unauthorized signal access** | Supabase RLS ensures users only see signals for their tier (Free users see limited, Pro see all) |
| **Stale signals acted on** | Every signal has `expires_at`. Frontend shows countdown. Expired signals auto-grey out |
| **Signal flooding** | Max 10 active signals per user. New signals queue if limit hit |
