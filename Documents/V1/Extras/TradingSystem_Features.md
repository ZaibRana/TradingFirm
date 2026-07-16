# Trading System Feature List

Here is the complete, final feature list — every single feature we designed, in one place.

---

## 🏗️ Core Architecture

| # | Feature | What It Does | How It Works |
|---|---------|-------------|-------------|
| A1 | **Multi-Agent CEO Orchestrator** | Central decision-maker for all signals | Calls Technical Agent, Institutional Agent, News Agent sequentially. Weighs their reports and fires the final signal. Never fires a trade on its own data. |
| A2 | **System A — 5m Technical Engine** | Scores chart setup quality (0–100 pts) | 7 indicators calculated on closed 5m candles. Generates a score. Sends report to CEO. |
| A3 | **System B — Institutional Edge Engine** | Reads where smart money is positioned | Processes order flow, volume profile, options chain, and dark pool prints from IBKR tick data. Sends BUY/SELL/Neutral verdict to CEO. |
| A4 | **News Intelligence Agent** | Tracks global macro calendar and sentiment | Queries Finnhub + FRED + Reuters + Forex Factory + VIX + 10Y Yield. Reports risk level to CEO. |
| A5 | **SEC Filing Agent** | Auto-discovers institutional market-moving filings | Scans SEC EDGAR every 10 min (day) and 15 min (night). Fires Telegram alerts independently from trade signals. |
| A6 | **Position State Machine** | Tracks open/closed positions per ticker | Reads/writes `positions.json`. EXIT signal only fires if an active BUY position exists for that ticker. |

---

## 📡 Data Layer

| # | Feature | What It Does | How It Works |
|---|---------|-------------|-------------|
| D1 | **IBKR WebSocket Live Feed** | Real-time candle and tick data | Connects to IBKR Paper account via `ib_insync`. Subscribes to 5m bar stream + tick-by-tick stream per ticker. Event-driven — signals fire on candle close, not a timer. |
| D2 | **IBKR Historical Data (Backtest)** | Clean 90-day bar history for testing | Pulls and caches 90 days of 5m bars from IBKR to local files. Re-used for backtest without repeated API calls. Avoids yfinance rate-limit and data quality issues. |
| D3 | **Rate Limit Manager** | Prevents IBKR API throttling | Staggers historical data requests to max 6 per 10 seconds. Options chain queries only fire when System A score exceeds 50. |
| D4 | **Market Hours Gate** | System only runs during US regular session | Enforces 09:25–16:05 ET, Monday–Friday. System sleeps outside hours. SEC Agent and EOD report are exceptions. |

---

## 📊 System A — 7 Indicators

| # | Indicator | Points | Entry Trigger | Why This Indicator |
|---|-----------|--------|-------------|-----------------|
| S1 | **VWAP Bounce** | 20 pts | Price retraces to [VWAP, VWAP + 0.15% ATR] and prints a bullish candle | Institutions execute block orders at VWAP. Bouncing off it = real demand, not a fake-out |
| S2 | **EMA 9/21 Ribbon** | 20 pts | EMA 9 crosses above EMA 21 within the last 2 candles | Faster than SMA, catches momentum shifts early. Ribbon width measures acceleration velocity |
| S3 | **MACD Histogram Slope** | 15 pts | Histogram rises for 3 consecutive bars | Measures rate-of-change, not just direction. Catches momentum build-up before the actual price breakout |
| S4 | **RSI Hidden Divergence** | 15 pts | Price makes higher low while RSI makes lower low | Exposes seller exhaustion. Much more reliable than simple RSI < 30 (which fires constantly in downtrends) |
| S5 | **Volume Delta Confirmation** | 15 pts | Current candle volume > 1.5× 20-bar average | Filters fake-outs. If no volume, institutions aren't participating — ignore the move |
| S6 | **Bollinger Band Squeeze** | 10 pts | BB compresses inside Keltner Channels, then expands | Volatility cycles from compressed to explosive. Catches breakout entries before the move happens |
| S7 | **Order Block Proximity** | 5 pts | Price within 0.3% of last down-close candle before a prior swing up | Identifies exact price levels where institutional limit orders are waiting |

**Higher Timeframe Context Modifier (applied on top of score):**

| 1D | 1H | Modifier |
|----|----|---------|
| Bullish | Bullish | +20 pts (ride the trend) |
| Bullish | Bearish | +10 pts (dip in uptrend) |
| Bearish | Bullish | 0 pts (neutral) |
| Bearish | Bearish | −10 pts (counter-trend — fires but flags warning) |

---

## 🏦 System B — Institutional Components

| # | Component | What It Reads | Trading Meaning |
|---|-----------|-------------|----------------|
| I1 | **Volume Delta** | Aggressive buys vs sells per 5m candle | Confirms who controls the candle — buyers or sellers |
| I2 | **Cumulative Delta** | Running session total of buy/sell pressure | Shows who controls the entire day — sustained institutional buying or selling |
| I3 | **DOM Imbalance** | Top 10 bid depth vs top 10 ask depth | Bid stack ≥ 3× ask = institutional buy wall present below price |
| I4 | **Volume Profile (POC/VAH/VAL)** | Volume-at-price for the current session | Price returning to POC = high-probability bounce. Low volume nodes = price moves through fast |
| I5 | **Options Chain Activity** | Put/Call ratio + unusual strike volume | P/C ratio spike = hedging. Call volume > 2.5× open interest at a strike = institutional positioning |
| I6 | **Dark Pool Approximation** | Block trades > 10k shares at bid/ask midpoint | Invisible institutional accumulation visible on tape as block mid-price crosses |

---

## 🚦 Signal Types & Entry Mechanics

| # | Signal Type | Triggers When | Position Size | Stop-Loss | Targets |
|---|------------|-------------|-------------|---------|--------|
| E1 | **Trend Continuation BUY** | System A ≥ 80 AND System B = BUY | Full | 1.5× ATR (locked, trails up) | Target 1 + Target 2 |
| E2 | **Momentum Scalp BUY** | System A 65–79 OR System A ≥ 80 + System B Neutral | Half | 1.0× ATR (locked, trails up) | Target 1 only (auto-exit) |
| E3 | **Conflict Info Alert** | System A ≥ 80 BUT System B = SELL | No trade | N/A | No trade — logged for review |
| E4 | **Emergency EXIT** | Price Guardian detects stop breach mid-candle | Close position | N/A | Immediate Telegram alert |

**Entry Zone Calculation:** `[Current Close, Current Close + 0.25% × ATR]` — prevents chasing breakouts.

---

## 🔴 Exit Engine

| # | Feature | Trend Continuation | Momentum Scalp |
|---|---------|------------------|---------------|
| X1 | **MACD Decline** | 3 consecutive bars | 2 consecutive bars |
| X2 | **Volume Climax** | Volume > 2.5× avg AND close in bottom 30% of candle range | Volume drops to < 50% of avg (exhaustion) |
| X3 | **RSI Momentum Break** | RSI falls below 50 after crossing above 60 | N/A |
| X4 | **EMA Structure Break** | Price closes below EMA 9 for 2 consecutive candles | N/A |
| X5 | **Trailing Stop Breach** | ATR multiplier 1.5× — ratchets up only | ATR multiplier 1.0× — ratchets up only |
| **Rule** | | **2 of 5 conditions must trigger** | **1 of 4 conditions must trigger** |

**Trailing Stop Formula:** `Stop_t = Max(Stop_prev, Close_t − (ATR × Multiplier))` — never moves down.

---

## 🛡️ Risk Management

| # | Feature | Trigger | Action |
|---|---------|---------|--------|
| R1 | **Price Guardian** | Price < stop-loss OR drop > 1.5% in < 3 min | Immediate Telegram emergency exit. No candle close needed |
| R2 | **VIX Spike Gate** | VIX > 25 | All scalp signals halted. Score penalty −20 on all signals |
| R3 | **VIX Hard Halt** | VIX > 35 | All signals fully frozen until VIX drops |
| R4 | **Circuit Breaker L1** | 2 consecutive daily losses | Warning alert sent to Telegram |
| R5 | **Circuit Breaker L2** | 3 consecutive daily losses | All scores −20. Only Dual Confirm signals fire |
| R6 | **Circuit Breaker L3** | 4 losses OR daily P&L < −2% | Complete system halt for remainder of session |
| R7 | **Signal Staleness Timer** | 5 min / 10 min / 15 min after signal fire | Follow-up alerts showing current price. Auto-expires position state at 15 min |
| R8 | **Pre-Event Compression Detector** | Major event within 72 hours AND ATR declining 3 days | Score penalty −15. Signal flagged "Pre-Event Compression" |

---

## 📰 Macro Intelligence Layer

| # | Source | What It Monitors | Action |
|---|--------|----------------|--------|
| M1 | **Finnhub Calendar** | Global scheduled events with importance rating | Triggers 3-phase macro block on HIGH-importance events |
| M2 | **FRED API** | Official US: CPI, NFP, PCE, GDP, Retail Sales | Pre-event block 30 min before, 15 min post-event fake-out window |
| M3 | **Forex Factory RSS** | ECB, BOJ, BOE, China PMI, global central banks | Sector-level score penalty for affected assets |
| M4 | **Reuters RSS** | Geopolitical news | Sentiment scoring, reduces scores if global risk-off detected |
| M5 | **VIX + 10Y Yield** | Live market fear gauge and rate pressure | Hard gates (see Risk Management) |
| M6 | **Gold (GLD) Monitor** | Risk-off indicator | Context label on signals — does not gate signals |
| M7 | **DXY Dollar Index** | Strong dollar = pressure on mega-cap earnings | Context label on signals — does not gate signals |
| M8 | **3-Phase Macro Block** | Pre-event / Fake-out / Reclaim phases | Blocks entries pre/during event. Enables special "Post-Event Opportunity" signal after VWAP reclaim |

---

## 📄 SEC Filing Agent

| # | Filing Type | What It Means | Alert Sent |
|---|------------|-------------|-----------|
| F1 | **Form 4** | Corporate insider buying > $1M | Immediately to Telegram with name, amount, ticker |
| F2 | **SC 13D** | Activist investor accumulating > 5% stake | Immediately — often precedes major price move |
| F3 | **8-K (M&A events)** | Merger, acquisition, material event | Immediately — flag for potential gap trade |
| F4 | **Auto-discovery** | Detects hot tickers NOT in your watchlist | Alert with suggestion to add to watchlist |
| **Schedule** | Day: every 10 min | After-hours: every 15 min (until 11 PM ET) | Weekends: 10 AM Saturday + Sunday sweep |

---

## 📱 Telegram Alerts

| # | Alert Type | When Sent | Key Details |
|---|-----------|---------|------------|
| T1 | **BUY Signal** | New entry signal fires | Ticker, signal type, entry zone, Target 1, Target 2, stop-loss, R:R ratio, score breakdown, time quality, macro status |
| T2 | **EXIT Signal** | Position close conditions met | Ticker, entry price, exit price, hold time, P&L %, exit reason (which conditions triggered) |
| T3 | **Conflict Report** | A ≥ 80 but B = SELL | Why trade was skipped, both system readings, logged for EOD validation |
| T4 | **Emergency EXIT** | Price Guardian mid-candle breach | Immediate — no wait for candle close |
| T5 | **System Health Alert** | Connection lost / data issue | Last known signal state, retry status, manual action instructions |
| T6 | **Daily Heartbeat** | Every hour during session (if quiet) | System status, active connections, next macro event |
| T7 | **Signal Staleness** | 5/10/15 min after signal fire | "Signal is 8 min old. Entry zone may have passed. Current price: $X" |
| T8 | **Good Morning Alert** | 09:25 AM ET daily | Today's macro calendar, VIX level, pre-market gap summary |
| T9 | **SEC Filing Alert** | After any material SEC filing detected | Filing type, company, insider name, dollar amount, link |

---

## 📋 Reporting & Analysis

| # | Report | When | What It Contains |
|---|--------|------|-----------------|
| P1 | **End-of-Day Summary** | 4:05 PM ET daily | All signals, wins/losses, P&L, System A vs B accuracy, circuit breaker usage |
| P2 | **Tomorrow's Plan** | 4:05 PM ET daily | Tomorrow's macro calendar, compression risk, watchlist gap zones, institutional positioning |
| P3 | **Weekly Performance Report** | Friday 4:30 PM ET | Weekly win rate, best/worst setups, indicator performance, auto-tuning suggestions |
| P4 | **Conflict Log Report** | Included in EOD | How many conflict skips — how many would have won/lost. CEO validation score. |

---

## 🖥️ Backtest Laboratory (UI)

| # | Feature | Detail |
|---|---------|--------|
| B1 | **90-Day Backtest** | Uses cached IBKR historical data. 1 ticker at a time. Runs as background task — doesn't block UI |
| B2 | **System A vs B vs Consensus** | Shows win rate, avg win, avg loss, EV per trade, max drawdown, Sharpe ratio for each system independently |
| B3 | **Signal History Table** | Filterable table of all historical signals: date, system, type, P&L, win/loss |
| B4 | **Signal Replay** | Click any historical signal → see exact indicator state at entry. Teaches pattern recognition |

---

## 🖥️ User Interface

| # | Feature | Detail |
|---|---------|--------|
| U1 | **Watchlist Selector** | Searchable dropdown, up to 10 tickers (max 15), pre-loaded with all US stock symbols |
| U2 | **Score Threshold Slider** | Adjust 65 (moderate) and 80 (high conviction) thresholds live |
| U3 | **Time Quality Labels** | Every signal tagged: Prime Time ✅ / Low Volume ⚠️ / Open Chaos ❌ |
| U4 | **Metrics-Only UI** | Clean cards per ticker showing: price, score, signal type, VWAP, EMA, stop-loss, position state |
| U5 | **Position State Display** | Shows OPEN / FLAT per ticker with entry price and current P&L |
| U6 | **Macro Status Banner** | Top-of-page bar showing: next event, VIX level, 10Y yield, system health |

---

## 🔒 Security & Performance

| # | Feature | Detail |
|---|---------|--------|
| SC1 | **Secrets Management** | Telegram token, user IDs in `st.secrets` (`.streamlit/secrets.toml`) — gitignored, never in code |
| SC2 | **Local JSON Storage** | No cloud DB. `positions.json`, `signal_history.jsonl`, `conflict_log.jsonl` — survives restarts |
| SC3 | **API Rate Guards** | Each external API call has a hardcoded minimum interval enforced by scheduler |
| SC4 | **Memory Management** | Session state cleaned at 4:05 PM daily. DataFrames use rolling windows, not growing history |
| SC5 | **Reconnection Logic** | IBKR disconnect → 3 retries with exponential backoff → Telegram alert if all fail |
| SC6 | **Weekend/Night Sleep** | System fully idles 11 PM – 6 AM. SEC Agent runs Saturday/Sunday 10 AM sweep only |

---

**Total Features: 65 across 10 categories.** Everything discussed, nothing missing.
