# Part 5: Service 3 — Risk Shield (FastAPI)

The Risk Shield is the **differentiator** — the feature no competitor does well. It continuously monitors market-wide health and broadcasts warnings when conditions deteriorate. It's the reason users trust the platform and stay subscribed even during losing streaks.

---

### What It Does (Responsibilities)

| Responsibility | Description |
|---------------|-------------|
| **Market health scoring** | Calculate a 0–100 health score from 6 independent indicators |
| **Continuous monitoring** | Check market conditions every 5 minutes during trading hours |
| **Alert broadcasting** | Publish health changes to Redis → Signal Engine + Web App react instantly |
| **Regime detection** | Classify market as HEALTHY / CAUTIOUS / DANGER / CRITICAL |
| **Historical tracking** | Log health scores over time for pattern analysis and AI learning |
| **Independent operation** | Works even if Data Engine and Signal Engine are down — monitors SPY/VIX directly |

---

### Internal Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    RISK SHIELD (FastAPI)                        │
│                    Port: 8003                                   │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  API Layer (main.py)                                      │  │
│  │                                                           │  │
│  │  GET  /health            → service status                 │  │
│  │  GET  /market/health     → current health score + regime  │  │
│  │  GET  /market/indicators → all 6 indicators breakdown     │  │
│  │  GET  /market/history    → health scores over time        │  │
│  │  GET  /market/history?days=30 → last 30 days              │  │
│  │  POST /market/check      → force immediate health check   │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Monitors (monitors/) — each runs independently           │  │
│  │                                                           │  │
│  │  vix_monitor.py          → VIX level + rate of change     │  │
│  │  breadth_monitor.py      → Advance/Decline, % above MAs  │  │
│  │  spy_monitor.py          → SPY vs 20/50 EMA distance     │  │
│  │  sector_monitor.py       → Defensive vs Offensive ratio   │  │
│  │  volume_monitor.py       → Market-wide volume anomalies   │  │
│  │  correlation_monitor.py  → Cross-asset stress signals     │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Scoring Engine (scoring/)                                │  │
│  │                                                           │  │
│  │  health_calculator.py  → Weighted average of all monitors │  │
│  │  regime_classifier.py  → Map score → HEALTHY/CAUTIOUS/etc│  │
│  │  alert_manager.py      → Decide when to broadcast alerts │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Broadcaster                                              │  │
│  │                                                           │  │
│  │  Redis pub: "risk_shield:health_changed"                  │  │
│  │    → payload: { score, regime, changed_indicators }       │  │
│  │                                                           │  │
│  │  Redis pub: "risk_shield:critical_alert"                  │  │
│  │    → payload: { message, action_required }                │  │
│  │                                                           │  │
│  │  PostgreSQL: log every health check for historical record │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

### File Structure

```
services/risk-shield/
├── Dockerfile
├── requirements.txt
├── main.py                      ← FastAPI app + routes
├── config.py                    ← Thresholds, weights, check intervals
│
├── monitors/                    ← EACH MONITOR IS INDEPENDENT
│   ├── __init__.py
│   ├── base.py                  ← Abstract monitor interface
│   ├── vix_monitor.py           ← VIX level + spike detection
│   ├── breadth_monitor.py       ← A/D line, % above 50/200 MA
│   ├── spy_monitor.py           ← SPY distance from key EMAs
│   ├── sector_monitor.py        ← Defensive vs cyclical rotation
│   ├── volume_monitor.py        ← Unusual market-wide volume
│   └── correlation_monitor.py   ← Bonds/gold/VIX correlation stress
│
├── scoring/                     ← AGGREGATION + ALERTING
│   ├── __init__.py
│   ├── health_calculator.py     ← Weighted score from all monitors
│   ├── regime_classifier.py     ← Score → regime mapping
│   └── alert_manager.py         ← Alert throttling + broadcasting
│
├── models/
│   ├── __init__.py
│   ├── health.py                ← HealthScore, Regime, Alert models
│   └── indicator.py             ← Individual indicator reading model
│
└── tests/
    ├── test_monitors.py
    ├── test_scoring.py
    └── test_alerts.py
```

---

### The 6 Indicators (How Health Score Is Calculated)

| # | Indicator | What it measures | Data source | Weight |
|---|-----------|-----------------|-------------|--------|
| 1 | **VIX Level** | Market fear/volatility | `^VIX` quote | 25% |
| 2 | **Market Breadth** | How many stocks are participating in the trend | SPY constituent data | 20% |
| 3 | **SPY Trend Health** | Is the market above/below key moving averages? | `SPY` OHLCV | 20% |
| 4 | **Sector Rotation** | Are investors fleeing to defensive sectors? | Sector ETF prices | 15% |
| 5 | **Volume Anomaly** | Unusual selling volume across the market | `SPY` + `QQQ` volume | 10% |
| 6 | **Cross-Asset Stress** | Are bonds/gold/dollar signaling panic? | `TLT`, `GLD`, `UUP` | 10% |

---

### How Each Monitor Scores (0–100 per monitor)

#### 1. VIX Monitor (Weight: 25%)

| VIX Level | Score | Interpretation |
|:---------:|:-----:|---------------|
| < 15 | 95 | Extremely calm. Low fear |
| 15–20 | 80 | Normal market conditions |
| 20–25 | 60 | Elevated uncertainty |
| 25–30 | 40 | High fear. Corrections common |
| 30–40 | 20 | Panic zone. Sell-offs likely |
| > 40 | 5 | Crisis territory (2020 COVID, 2008) |

**Bonus check:** If VIX jumps > 20% in one day → automatic –15 penalty regardless of level.

#### 2. Market Breadth Monitor (Weight: 20%)

| Metric | Healthy | Warning | Danger |
|--------|:-------:|:-------:|:------:|
| % of S&P 500 above 50-day MA | > 65% → 90 pts | 40–65% → 60 pts | < 40% → 25 pts |
| % of S&P 500 above 200-day MA | > 70% → 90 pts | 50–70% → 60 pts | < 50% → 25 pts |
| Advance/Decline ratio (5-day avg) | > 1.2 → 90 pts | 0.8–1.2 → 60 pts | < 0.8 → 25 pts |

**Final breadth score** = average of the 3 sub-metrics.

#### 3. SPY Trend Health Monitor (Weight: 20%)

| Condition | Score |
|-----------|:-----:|
| SPY above 20 EMA AND 50 EMA AND 200 EMA | 95 |
| SPY above 50 EMA and 200 EMA, below 20 EMA | 70 |
| SPY above 200 EMA only | 45 |
| SPY below all 3 EMAs | 15 |
| SPY below 200 EMA AND making lower lows | 5 |

#### 4. Sector Rotation Monitor (Weight: 15%)

Compares performance of **defensive sectors** vs **offensive sectors** over 5 trading days:

| Defensive ETFs | Offensive ETFs |
|---------------|----------------|
| `XLU` (Utilities) | `XLK` (Technology) |
| `XLP` (Consumer Staples) | `XLY` (Consumer Discretionary) |
| `XLV` (Healthcare) | `XLF` (Financials) |

| Rotation Signal | Score |
|----------------|:-----:|
| Offensive outperforming defensive by > 1% | 90 (risk-on) |
| Roughly equal performance | 65 (neutral) |
| Defensive outperforming offensive by > 1% | 30 (risk-off flight) |
| Defensive outperforming by > 2% | 10 (panic rotation) |

#### 5. Volume Anomaly Monitor (Weight: 10%)

| Condition | Score |
|-----------|:-----:|
| SPY + QQQ volume < 1.2x average | 85 (normal) |
| Volume 1.2x–1.8x average | 60 (elevated, watch closely) |
| Volume 1.8x–2.5x average on red day | 30 (distribution/selling) |
| Volume > 2.5x average on red day | 10 (capitulation-level selling) |
| Volume > 2x average on green day | 80 (accumulation — actually healthy) |

#### 6. Cross-Asset Stress Monitor (Weight: 10%)

| Signal | Score |
|--------|:-----:|
| Bonds (`TLT`) flat, Gold (`GLD`) flat, Dollar (`UUP`) flat | 80 (calm) |
| Bonds up, Gold up, Dollar up simultaneously | 25 (classic panic — flight to safety) |
| Bonds up sharply (>1% day), stocks down | 35 (risk-off rotation) |
| All assets falling including bonds | 15 (liquidity crisis — very rare, very dangerous) |

---

### Health Score → Regime Mapping

```
FINAL HEALTH SCORE = Σ (monitor_score × weight) for all 6 monitors

┌────────────────────────────────────────────────────────────┐
│                                                            │
│  100 ████████████████████ HEALTHY (70-100)                │
│   70 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│      ████████████████████ CAUTIOUS (40-69)                │
│   40 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│      ████████████████████ DANGER (20-39)                  │
│   20 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│      ████████████████████ CRITICAL (0-19)                 │
│    0                                                       │
└────────────────────────────────────────────────────────────┘
```

| Regime | Score | Color | What the user sees | System action |
|--------|:-----:|:-----:|-------------------|---------------|
| **HEALTHY** | 70–100 | 🟢 Green | "Market conditions are favorable" | Normal operation |
| **CAUTIOUS** | 40–69 | 🟡 Yellow | "Elevated risk — trade with caution" | Signal confidence threshold raised to 75 |
| **DANGER** | 20–39 | 🟠 Orange | "High risk — consider reducing exposure" | No new signals. Active SLs tightened 50% |
| **CRITICAL** | 0–19 | 🔴 Red | "⚠️ PROTECT CAPITAL — market in distress" | All signals force-closed. Push notification to all users |

---

### Alert Throttling (Prevents Notification Spam)

| Rule | Detail |
|------|--------|
| **Regime change alert** | Only fires when regime actually changes (HEALTHY → CAUTIOUS), not on every 5-min check |
| **Score change alert** | Only fires if score changes by ≥ 10 points from last alert |
| **Minimum interval** | No more than 1 alert per 15 minutes, even during rapid changes |
| **Critical override** | CRITICAL alerts bypass all throttling — immediate push always |
| **Recovery alert** | "Market health improving" alert fires when score crosses back above 70 |

---

### Data Sources (All Free)

| Indicator | Ticker(s) | Source | Cost |
|-----------|-----------|--------|------|
| VIX | `^VIX` | yfinance (dev) / Twelve Data (prod) | Free / $30 |
| SPY price + volume | `SPY` | Same as above | Included |
| QQQ volume | `QQQ` | Same as above | Included |
| Sector ETFs | `XLK, XLU, XLP, XLV, XLY, XLF` | Same as above | Included |
| Bonds | `TLT` | Same as above | Included |
| Gold | `GLD` | Same as above | Included |
| Dollar | `UUP` | Same as above | Included |
| Breadth (% above MAs) | Calculated from SPY constituents | Data Engine or Finviz | Included |

> **Key point:** Risk Shield fetches its own data for core tickers (SPY, VIX, sector ETFs). It does NOT depend on Data Engine for this. If Data Engine is down, Risk Shield still works. This is the independence principle.

---

### API Endpoints Detail

| Endpoint | Method | What it does | Who calls it |
|----------|--------|-------------|-------------|
| `/health` | GET | Service health + last check time | Docker, monitoring |
| `/market/health` | GET | Current score + regime + trend (improving/declining) | Web App (risk gauge), Signal Engine |
| `/market/indicators` | GET | All 6 indicators with individual scores + raw values | Web App (expanded health view) |
| `/market/history` | GET | Historical health scores. Params: `?days=30` | Web App (health chart), AI Agent |
| `/market/check` | POST | Force immediate health recalculation | Manual trigger, debugging |

---

### What the Health Response Looks Like

```json
{
  "score": 62,
  "regime": "CAUTIOUS",
  "trend": "declining",
  "previous_score": 71,
  "checked_at": "2026-07-15T14:35:00Z",
  "message": "Elevated risk — trade with caution",

  "indicators": {
    "vix": {
      "score": 55,
      "weight": 0.25,
      "raw_value": 22.4,
      "detail": "VIX at 22.4 — elevated uncertainty"
    },
    "breadth": {
      "score": 58,
      "weight": 0.20,
      "raw_value": { "pct_above_50ma": 52, "pct_above_200ma": 61, "ad_ratio": 0.9 },
      "detail": "52% of S&P above 50-day MA — weakening participation"
    },
    "spy_trend": {
      "score": 70,
      "weight": 0.20,
      "raw_value": { "above_20ema": true, "above_50ema": true, "above_200ema": true },
      "detail": "SPY above all key EMAs but nearing 20 EMA"
    },
    "sector_rotation": {
      "score": 55,
      "weight": 0.15,
      "raw_value": { "offensive_5d": -0.3, "defensive_5d": 0.8 },
      "detail": "Slight rotation into defensives — watch for acceleration"
    },
    "volume": {
      "score": 75,
      "weight": 0.10,
      "raw_value": { "spy_rvol": 1.1, "qqq_rvol": 1.0 },
      "detail": "Volume normal"
    },
    "cross_asset": {
      "score": 70,
      "weight": 0.10,
      "raw_value": { "tlt_1d": 0.2, "gld_1d": 0.4, "uup_1d": -0.1 },
      "detail": "Mild bond/gold bid — no panic signals"
    }
  }
}
```

---

### Scheduled Execution

| Task | Schedule | Condition |
|------|----------|-----------|
| Health check | Every 5 minutes | Only during market hours (9:00 AM – 5:00 PM ET, Mon–Fri) |
| End-of-day summary | 4:15 PM ET daily | Always (stores daily close health score) |
| Weekend baseline | Saturday 10:00 AM | Stores last known health for Monday morning reference |
| Pre-market check | 8:00 AM ET | Quick check before market opens to set initial regime |

---

### Dependencies (`requirements.txt`)

| Package | Purpose | Status |
|---------|---------|--------|
| `fastapi` | Web framework | ✅ Stable |
| `uvicorn[standard]` | ASGI server | ✅ Stable |
| `httpx` | Async HTTP for fetching VIX, SPY, ETF quotes | ✅ Stable |
| `pandas` | EMA calculations on SPY data | ✅ Stable |
| `numpy` | Statistical calculations | ✅ Stable |
| `redis` | Publish health changes, cache current score | ✅ Stable |
| `asyncpg` | Store health history in PostgreSQL | ✅ Stable |
| `huey[redis]` | Scheduled 5-minute checks | ✅ Stable |
| `yfinance` | Dev: fetch VIX/SPY/ETF quotes | ⚠️ Dev only |

---

### Security Considerations

| Threat | Mitigation |
|--------|-----------|
| **False calm** (score says healthy but market is crashing) | Multiple independent indicators. Need 3+ to agree. No single indicator can hold score above 70 alone |
| **Alert fatigue** (too many notifications) | Throttling rules: regime-change only, 15-min cooldown, score must move ≥ 10 points |
| **Stale health score served** | Every response includes `checked_at`. Frontend shows "Last checked: 2 min ago". If > 15 min stale, show warning |
| **Monitor failure** | If any single monitor throws an error, it returns its last known score with a `"stale": true` flag. Other 5 monitors continue |
| **Data provider outage** | If VIX/SPY quote fails, Risk Shield uses last known value + increments a `missed_checks` counter. After 3 consecutive misses → alert ops |

---

# Part 6: Service 4 — Web App (Next.js 16)

The Web App is what users see and interact with. It pulls data from all backend services and presents it as a clean, real-time dashboard. It handles auth, displays the watchlist, shows signal cards, renders the risk gauge, and manages user settings.

---

### What It Does (Responsibilities)

| Responsibility | Description |
|---------------|-------------|
| **Authentication** | Sign up, login, logout via Supabase Auth (email + Google) |
| **Dashboard** | Main view: watchlist, active signals, risk gauge — all on one screen |
| **Watchlist display** | Show curated stocks with scores, sectors, key metrics |
| **Signal cards** | Display actionable signals with entry/SL/TP, confidence, countdown |
| **Risk gauge** | Real-time market health meter with 6-indicator breakdown |
| **Trade tracker** | User logs trades, sees P&L history, win rate |
| **Settings** | Notification preferences, tier management, display options |
| **Real-time updates** | Server-Sent Events (SSE) for live signal + risk alerts |
| **Marketing pages** | Landing page, pricing, about — SSR for SEO |

---

### Internal Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    WEB APP (Next.js 16)                         │
│                    Port: 3000                                   │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  App Router Pages                                         │  │
│  │                                                           │  │
│  │  PUBLIC (SSR — SEO optimized):                            │  │
│  │    /                → Landing page                        │  │
│  │    /pricing         → Pricing tiers                       │  │
│  │    /login           → Auth page                           │  │
│  │    /signup          → Registration                        │  │
│  │                                                           │  │
│  │  PROTECTED (Client-side — requires auth):                 │  │
│  │    /dashboard       → Main hub (watchlist + signals + risk)│ │
│  │    /signals         → All active & past signals           │  │
│  │    /signals/[id]    → Single signal detail                │  │
│  │    /stock/[ticker]  → Stock detail + chart + zones        │  │
│  │    /trades          → Trade tracker + P&L history         │  │
│  │    /settings        → User preferences                   │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  API Routes (Next.js server-side)                         │  │
│  │                                                           │  │
│  │  /api/auth/callback   → Supabase auth callback            │  │
│  │  /api/scan/trigger    → Proxy to Data Engine /scan/run    │  │
│  │  /api/events          → SSE stream (signals + risk)       │  │
│  │                                                           │  │
│  │  Why proxy? → Keeps backend URLs private.                 │  │
│  │  Browser only talks to Next.js, never directly to         │  │
│  │  FastAPI services. This is a security layer.              │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Components                                               │  │
│  │                                                           │  │
│  │  layout/          → Shell, Sidebar, Header, Footer        │  │
│  │  dashboard/       → WatchlistGrid, SignalCards, RiskGauge │  │
│  │  stock/           → StockChart, ZoneOverlay, Indicators   │  │
│  │  signals/         → SignalCard, SignalDetail, Countdown   │  │
│  │  trades/          → TradeLog, PnLChart, WinRate           │  │
│  │  auth/            → LoginForm, SignupForm, AuthGuard      │  │
│  │  shared/          → Button, Badge, Loader, Toast          │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Data Layer                                               │  │
│  │                                                           │  │
│  │  lib/supabase.js    → Supabase client (auth + DB reads)   │  │
│  │  lib/api.js         → HTTP client for backend services    │  │
│  │  lib/sse.js         → SSE connection for real-time events │  │
│  │  hooks/             → useWatchlist, useSignals, useHealth │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

### File Structure

```
web/
├── Dockerfile
├── package.json
├── next.config.mjs
├── .env.local                   ← Supabase keys, backend URLs
│
├── app/
│   ├── layout.js                ← Root layout (fonts, theme, Supabase provider)
│   ├── globals.css              ← Design system tokens, base styles
│   │
│   ├── (public)/                ← Group: SSR pages, no auth required
│   │   ├── page.js              ← Landing page (/)
│   │   ├── pricing/page.js      ← Pricing tiers
│   │   ├── login/page.js        ← Login form
│   │   └── signup/page.js       ← Registration form
│   │
│   ├── (protected)/             ← Group: client pages, auth required
│   │   ├── layout.js            ← Auth guard wrapper + sidebar
│   │   ├── dashboard/page.js    ← Main hub
│   │   ├── signals/
│   │   │   ├── page.js          ← Signal list
│   │   │   └── [id]/page.js     ← Signal detail
│   │   ├── stock/
│   │   │   └── [ticker]/page.js ← Stock detail + chart
│   │   ├── trades/page.js       ← Trade tracker
│   │   └── settings/page.js     ← User preferences
│   │
│   └── api/                     ← Next.js API routes (server-side proxy)
│       ├── auth/callback/route.js
│       ├── scan/trigger/route.js
│       └── events/route.js      ← SSE endpoint
│
├── components/
│   ├── layout/
│   │   ├── AppShell.js          ← Main layout with sidebar
│   │   ├── Sidebar.js           ← Navigation
│   │   ├── Header.js            ← Top bar with user + health badge
│   │   └── Footer.js
│   │
│   ├── dashboard/
│   │   ├── WatchlistGrid.js     ← Stock cards grid (evolves from your StockCard)
│   │   ├── SignalPanel.js       ← Active signals sidebar/panel
│   │   ├── RiskGauge.js         ← Market health donut/meter
│   │   ├── SectorTabs.js        ← Sector filter (your existing component)
│   │   └── ScanButton.js        ← Manual scan trigger + progress
│   │
│   ├── stock/
│   │   ├── StockChart.js        ← Candlestick chart with zones overlay
│   │   ├── ZoneOverlay.js       ← S/R zone lines on chart
│   │   ├── IndicatorPanel.js    ← EMA, MACD, RSI display
│   │   └── FundamentalCard.js   ← Fundamental score breakdown
│   │
│   ├── signals/
│   │   ├── SignalCard.js        ← Compact signal: entry/SL/TP + confidence
│   │   ├── SignalDetail.js      ← Expanded: chart + zone + full breakdown
│   │   ├── Countdown.js         ← Time until signal expires
│   │   └── ConfidenceBadge.js   ← Color-coded confidence score
│   │
│   ├── trades/
│   │   ├── TradeLog.js          ← Table of logged trades
│   │   ├── PnLChart.js          ← Equity curve chart
│   │   └── WinRateCard.js       ← Win %, avg R:R, Sharpe stats
│   │
│   ├── auth/
│   │   ├── LoginForm.js
│   │   ├── SignupForm.js
│   │   └── AuthGuard.js         ← Redirect to /login if not authenticated
│   │
│   └── shared/
│       ├── Button.js
│       ├── Badge.js
│       ├── Loader.js
│       ├── Toast.js             ← Push notification toasts
│       └── EmptyState.js
│
├── hooks/
│   ├── useAuth.js               ← Supabase auth state
│   ├── useWatchlist.js          ← Fetch + cache watchlist from Data Engine
│   ├── useSignals.js            ← Fetch active signals + SSE updates
│   ├── useHealth.js             ← Fetch risk score + SSE updates
│   └── useTrades.js             ← CRUD for user's trade log
│
└── lib/
    ├── supabase.js              ← createBrowserClient / createServerClient
    ├── api.js                   ← Typed fetch wrapper for backend services
    └── sse.js                   ← Server-Sent Events connection manager
```

---

### How It Talks to Backend Services

```
BROWSER (user)
    │
    │ All requests go to Next.js (port 3000)
    │ Browser NEVER talks to FastAPI directly
    ▼
NEXT.JS SERVER (API Routes + SSR)
    │
    ├──→ Supabase (Auth + direct DB reads for user data)
    │     • Login/signup/session
    │     • User preferences
    │     • Trade log (user-owned data)
    │
    ├──→ Data Engine (port 8001) via internal HTTP
    │     • GET /scan/results → watchlist
    │     • GET /stock/{ticker} → stock detail
    │     • POST /scan/run → trigger scan
    │
    ├──→ Signal Engine (port 8002) via internal HTTP
    │     • GET /signals/active → signal cards
    │     • GET /zones/{ticker} → chart zones
    │
    └──→ Risk Shield (port 8003) via internal HTTP
          • GET /market/health → risk gauge
          • GET /market/indicators → breakdown
```

---

### Real-Time Updates (Server-Sent Events)

| What | Why SSE instead of WebSocket |
|------|---------------------------|
| Signal alerts | SSE is simpler, works through proxies/CDNs, one-directional (server → client) which is all we need |
| Risk score changes | User doesn't send data back through this channel — only receives |
| Scan progress | "Step 3/6: Filtering..." pushed to frontend |

```
Next.js /api/events/route.js
    │
    │ Subscribes to Redis pub/sub channels:
    │   • "signals:new"
    │   • "risk_shield:health_changed"
    │   • "data_engine:scan_progress"
    │
    │ Streams events to browser via SSE
    ▼
Browser (EventSource)
    │
    │ useSignals() hook receives: { type: "new_signal", data: {...} }
    │ useHealth() hook receives: { type: "health_update", data: {...} }
    │
    └──→ React state updates → UI re-renders
```

---

### What We Keep vs. Rebuild from Your Current App

| Current component | Decision | Reason |
|-------------------|----------|--------|
| Header.js | **Rebuild** | Needs sidebar layout, risk badge, user menu |
| SectorTabs.js | **Keep & refactor** | Logic is good, just restyle to match new design |
| StockCard.js | **Evolve → WatchlistGrid** | Add fundamental score, S/R status, zone proximity |
| ProStockCard.js | **Merge into WatchlistGrid** | One unified card component, not two separate ones |
| page.js (571 lines) | **Break into pages** | Split into /dashboard, /signals, /stock/[ticker] |
| Firebase deps in package.json | **Remove** | Replaced by Supabase |
| `yahoo-finance2` in package.json | **Remove** | Frontend never calls Yahoo directly. Goes through Data Engine |

---

### Auth Flow (Supabase)

```
1. User visits /login
2. Enters email + password (or clicks "Sign in with Google")
3. Supabase Auth handles verification
4. On success → redirects to /dashboard
5. Supabase sets session cookie (httpOnly, secure)
6. Every API route checks session via Supabase middleware
7. If no session → redirect to /login
```

| Auth feature | Implementation |
|-------------|----------------|
| Email + password | Supabase built-in |
| Google OAuth | Supabase built-in (configure in Supabase dashboard) |
| Session management | `@supabase/ssr` — handles cookie-based sessions for Next.js |
| Protected routes | `AuthGuard.js` component wraps `(protected)/layout.js` |
| Row Level Security | DB queries automatically scoped to `auth.uid()` |

---

### Dependencies (`package.json`)

| Package | Purpose | Status |
|---------|---------|--------|
| `next` | Framework (16.2.x) | ✅ Already installed |
| `react` / `react-dom` | UI library (19.x) | ✅ Already installed |
| `@supabase/supabase-js` | Supabase client | ✅ Stable |
| `@supabase/ssr` | Server-side auth for Next.js | ✅ Stable |
| `lightweight-charts` | TradingView candlestick charts (free, MIT license) | ✅ Stable, 10KB |
| ~~`firebase`~~ | ~~Auth/DB~~ | ❌ **Removed** — replaced by Supabase |
| ~~`yahoo-finance2`~~ | ~~Direct data fetch~~ | ❌ **Removed** — goes through Data Engine |

---

### Security Considerations

| Threat | Mitigation |
|--------|-----------|
| **XSS attacks** | React auto-escapes JSX. No `dangerouslySetInnerHTML`. CSP headers set |
| **CSRF** | Supabase uses `httpOnly` cookies + PKCE flow. No token in localStorage |
| **Backend URL exposure** | All backend calls go through Next.js API routes. FastAPI URLs never reach the browser |
| **Tier bypass** (free user accessing Pro features) | Server-side check on every API route. Supabase RLS on every DB query. Frontend hiding a button is NOT security |
| **Rate limiting** | Next.js middleware limits API route calls per IP (60/min default) |
| **SEO poisoning** | Only public pages are SSR. Dashboard is client-rendered, not indexable |
