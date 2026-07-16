# System Requirements Document — Phase 1 of 4
## Ultimate Institutional 5m Command Matrix

This document defines the architecture, data layer protocols, and technical scoring logic for **Phase 1** of the trading signal system. 

---

## 1. System Features Overview (Phase 1 Scope)

| Feature ID | Feature Name | Description | Institutional Value / Why |
| :--- | :--- | :--- | :--- |
| **F-1.1** | **Multi-Agent CEO Architecture** | Sequentially calls sub-agents (Tech, Inst, News), aggregates data, makes final trade decision. | Prevents monolithic crashes; allows partial functionality if one source fails. |
| **F-1.2** | **IBKR Data Layer Integration** | Direct connection to IBKR paper account via `ib_insync`. Streams 5m bars & real-time tick data. | Real-time execution accuracy; avoids yfinance IP blocking and 15-min delay. |
| **F-1.3** | **System A: 5m Technical Engine** | Generates entry signals on closed 5m candles using a 100-point scoring algorithm. | Multi-indicator confirmation filters out single-indicator noise. |
| **F-1.4** | **US Watchlist Selector** | Streamlit multiselect dropdown for up to 10 tickers from pre-loaded US stock symbol list. | Keeps computing payload safe and within IBKR API limits. |
| **F-1.5** | **Local File Persistence** | JSON/JSONL storage for positions, signal history, and system logs. | Survives system reboots or browser refreshes without losing track of open trades. |

---

## 2. Multi-Agent & CEO Decision Design (F-1.1)

The CEO orchestrator handles conflicts and computes the final signal.

### Signal Classification & Position Sizing
*   **Trend Continuation Buy**: Tech Score $\ge 80$ + Institutional confirms (System B is Buy). Full position size, wider stop.
*   **Momentum Scalp Buy**: Tech Score $65-79$ (No Inst confirmation needed) OR Tech Score $\ge 80$ + Institutional is Neutral. Half position size, tight stop.
*   **Conflict Report (No Trade)**: Tech Score $\ge 80$ but Institutional is Sell. Fires an info log detailing *why* the trade was skipped.

---

## 3. IBKR Data Streaming Protocol (F-1.2)
*   **Bar Subscription**: Subscribes to real-time 5m bars. Signals compute exactly on the bar close event.
*   **Tick Subscription**: Streams ticks continuously to calculate mid-candle volume metrics (Volume Delta, Cumulative Delta).
*   **Rate Limits**: Staggers historical data requests (max 6 requests per 10 seconds) during cache loads.

---

## 4. System A: Indicator & Entry Mechanics (F-1.3)

System A uses 7 indicators to score setup strength (Max: 100 points).

### Indicator Configurations & Entry Points

```
INDICATOR           FORMULA / CRITERIA                       POINTS   INSTITUTIONAL VALUE
─────────────────────────────────────────────────────────────────────────────────────────
VWAP Bounce         Price inside [VWAP, VWAP + 0.15% ATR]    20 pts   Institutions execute at
                    with bullish candle confirmation.                 VWAP; acts as support.

EMA 9/21 Ribbon     EMA 9 > EMA 21. Max points if cross      20 pts   Captures velocity; cross
                    happened on the last 2 candles.                   confirms trend shift.

MACD Hist Slope     Histogram value is rising 3 bars in      15 pts   Measures rate of change,
                    a row (Hist[0] > Hist[-1] > Hist[-2]).            not just current momentum.

RSI Divergence      Bullish Hidden Divergence: price lower   15 pts   Exposes seller exhaustion
                    low, but RSI makes a higher low.                  before price turns.

Volume Delta        Candle volume > 1.5x of 20-period avg.   15 pts   Filters fake-outs; smart
                                                                      money leaves a footprint.

BB Squeeze          Bollinger Bands contract inside Keltner  10 pts   Explosive moves follow
                    Channels, then expand on breakout.               volatility compression.

Order Block         Price touches the last down-close         5 pts   Retests buy order walls
                    candle before the previous up-move.               left by major players.
```

### Entry Triggering Logic
*   **High Conviction Setup**: Combined score of $\ge 80$.
*   **Moderate Conviction Setup**: Combined score of $65-79$.
*   **Execution Zone**: The CEO calculates the Entry Zone as `[Trigger Price, Trigger Price + 0.25% ATR]`. Alerts suggest executing *only* within this window.
