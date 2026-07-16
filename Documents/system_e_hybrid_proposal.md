# System E: Recommended Hybrid Trading System Proposal
## Concept: "Smart Watchlist + Timed Entries + Risk Shield"

This document outlines a 3-layer system designed to bridge the gap between screening, signaling, and risk management.

```
┌─────────────────────────────────────────────────┐
│  LAYER 3: RISK SHIELD (always running)          │
│  Market Health Dashboard + "tighten/exit" alerts│
├─────────────────────────────────────────────────┤
│  LAYER 2: SIGNAL ENGINE                         │
│  Entry/Exit/SL when Layer 1 stock hits a zone   │
├─────────────────────────────────────────────────┤
│  LAYER 1: SMART WATCHLIST                       │
│  Fundamentals + Scanner filter → curated pool   │
└─────────────────────────────────────────────────┘
```

---

## 1. How It Works (User Experience Flow)

1. **Overnight Scan:**
   * System screens 5,000+ stocks and narrows them down to a curated pool of ~30–60 "qualified" stocks using combining fundamental factors (growth, debt, margins) and broad technical scans.
2. **Smart Watchlist:**
   * The user opens the dashboard and sees the curated list, graded by fundamental strength and proximity to key technical zones (support, demand).
3. **Zone Alerts:**
   * During market hours, the system monitors these watchlisted stocks. When a stock touches an identified support or demand zone with volume confirmation, the system triggers a push notification.
4. **Signal Card:**
   * Clicking the alert opens a clear breakdown:
     * **Action:** BUY/SELL
     * **Entry Zone:** e.g., $420.00 – $422.00
     * **Stop Loss:** e.g., $412.00
     * **Targets:** e.g., $440.00 (Risk/Reward 2.5:1)
     * **Key Metrics:** Fundamental Score (85/100), S/R zone strength, RVOL, ATR.
5. **Risk Shield (Continuous Monitoring):**
   * The system evaluates overall market health using breadth metrics (% of stocks above EMAs, VIX movement, SPY/QQQ relative trends).
   * If a market-wide correction or high-volatility event is detected, it triggers a warning: *"Market Health Declining — Tighten stops or take partial profits."*

---

## 2. Feasibility Analysis

| Metric | Rating | Rationale |
|:---|:---:|:---|
| **Can it be built?** | **8.5 / 10** | **High.** You already have 60% of the scanner components written in Python. S/R zones and basic market breadth calculations are straightforward rules-based scripts. |
| **User Demand** | **9.0 / 10** | **High.** Solves the fragmentation issue (users having to jump between TradingView for charts, Koyfin for numbers, and Telegram channels for signals). |
| **Churn Risk** | **Low** | The multi-day swing approach is more forgiving than day trading, leading to more stable, reliable user results and lower cancellation rates. |
| **Legal Risk** | **Low-Medium** | When framed as an interactive analytics and alert tool (not automated discretionary advice), legal overhead remains low. Disclaimers are still essential. |
| **Data Costs** | **Low-Medium** | Daily candles and standard fundamentals are inexpensive to license compared to real-time tick/orderbook data feeds. |

---

## 3. Recommended Pricing Tiers

* **Free Tier ($0/mo):**
  * Top 5 watchlist stocks (15-min delayed).
  * Basic Market Health indicator (Neutral/Bullish/Bearish).
  * Static chart views.
* **Starter Tier ($15/mo):**
  * Access to full daily watchlist (30-60 stocks).
  * Visible Support/Resistance zones.
  * Active Market Health Dashboard.
  * 3 entry alerts per day.
* **Pro Tier ($40/mo):**
  * Real-time entry, stop loss, and target signals.
  * Push notifications for alerts and Risk Shield warnings.
  * Fundamental quality scores.
  * Open position tracking & automated stop-loss adjustments.
* **Elite Tier ($80/mo):**
  * Core API access (allows developers to pull the watchlist/signals).
  * Backtesting history & historical signal verification dashboard.
  * Priority webhook integration.

---

## 4. Competitive Advantages

* **vs. Scanners Only (Finviz, TC2000):** Focuses the scanner results down to structured entry/exit tactics, making it immediately actionable.
* **vs. Signals Only (Signal Services):** Pre-screens with high-quality fundamentals, filtering out low-liquidity penny stocks and pump-and-dump setups.
* **vs. Fundamental Tools (Koyfin, Simply Wall St):** Tells you *when* to execute a trade, rather than just telling you if a company is cheap or expensive.
* **The Killer Feature (Risk Shield):** Actively advises users on when to step aside, providing a guard against capital destruction during sudden sell-offs.

---

## 5. Development Strategy (Implementation Milestones)

1. **Phase 1 (Smart Watchlist):** Write a clean script to combine your technical scanners with a commercial fundamental data API (FMP or EODHD).
2. **Phase 2 (Signal Engine):** Implement the math to isolate Support/Resistance levels and trigger alerts when those price points are tested on confirming volume.
3. **Phase 3 (Risk Shield):** Develop the Market Health Dashboard monitoring VIX, S&P 500, and market breadth.
4. **Phase 4 (Frontend UI):** Build a responsive dashboard connecting the database outputs to the user.
