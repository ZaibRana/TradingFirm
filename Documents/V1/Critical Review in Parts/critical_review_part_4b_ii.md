# Critical System Review — Part 4b-ii of 7
## 📊 Data Honesty Audit — News Agent

---

### Overall Naming Issue

**The agent is called "News Agent" but it doesn't read news.**

What it actually does:
- ✅ Checks economic calendar for macro events (CPI, FOMC, Jobs)
- ✅ Reads VIX value
- ✅ Reads 10-year yield
- ✅ Gates signals during high-impact events
- ❌ Does NOT read news articles
- ❌ Does NOT perform sentiment analysis
- ❌ Does NOT scan headlines for breaking news

**Better name:** `MacroGateAgent` or `MarketRegimeAgent`

---

### Field 1: `is_macro_gated` — Event Blocking Flag

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ✅ Yes — Finnhub `/calendar/economic` returns upcoming events with date/time |
| **Is it correct?** | ✅ Yes — simple time comparison: if event is within T-30 minutes, gate is ON |
| **What user expects** | "System pauses before major economic releases" |
| **What it actually delivers** | Exactly that. This is one of our most honest features. |
| **Caveat** | Finnhub's free calendar doesn't cover ALL events. It covers major US events (CPI, FOMC, NFP, GDP) but may miss smaller ones (regional Fed speeches, Treasury auctions). |
| **Honesty rating** | 8/10 — Delivers what it promises. Minor gap on event coverage. |

---

### Field 2: `macro_phase` — Event Lifecycle (PRE_EVENT → FAKEOUT → RECLAIM → CLEAR)

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ✅ Yes — timer-based state machine using event schedule |
| **Is it correct?** | ✅ Yes — PRE_EVENT = T-30min, FAKEOUT = T+0 to T+15, RECLAIM = T+15 to T+30, CLEAR = no event |
| **Does it provide edge?** | ✅ Good — the FAKEOUT phase (first 15 min after data release) is genuinely dangerous. Initial moves often reverse. Blocking signals here prevents chasing fake breakouts. |
| **Caveat** | The fixed timing (15 min fakeout, 30 min total) is a simplification. Some events resolve in 5 minutes, others take 2 hours. |
| **Honesty rating** | 7/10 — Real concept, fixed timing is approximate but safe (errs on the side of caution). |

---

### Field 3: `vix_value` — CBOE Volatility Index

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ✅ Yes — Finnhub `/quote?symbol=VIX` or `^VIX` |
| **Is it correct?** | ✅ Yes — Finnhub returns the current VIX value from CBOE |
| **What user expects** | "VIX > 25 means market is scared, don't scalp" |
| **What it actually means** | VIX measures implied volatility of S&P 500 options for the next 30 days. Higher VIX = market expects bigger moves. It does NOT mean the market will go down — it means uncertainty is high. |
| **Does it provide edge?** | ✅ Good — using VIX as a regime filter is a well-established practice. Our thresholds (25 = halt scalps, 35 = halt all) are reasonable. |
| **Data quality** | 🟢 HIGH — VIX is a single standardized number from CBOE |
| **Honesty rating** | 9/10 — Delivers exactly what it promises. One of our most reliable data points. |

---

### Field 4: `yield_10y` — US 10-Year Treasury Yield

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ✅ Yes — FRED API series `DGS10` (daily) or Finnhub quote for `^TNX` (intraday) |
| **Is it correct?** | ✅ Yes — FRED is the official source for Treasury yields |
| **What user expects** | "Rising yields = bad for growth stocks" |
| **What it actually means** | The yield on 10-year US government bonds. Rising yields increase the discount rate for future earnings, pressuring growth/tech stocks. Falling yields are bullish for growth. |
| **Does it provide edge?** | ⚠️ Moderate — the relationship is real but operates on a multi-day timeframe, not 5-minute bars. A 0.02% yield move during one trading day has minimal impact on individual stock prices. |
| **Caveat** | FRED updates `DGS10` daily (not intraday). For intraday yield movement, we need Finnhub's `^TNX` quote. Our TDD doesn't specify which source we use for intraday vs daily. |
| **Honesty rating** | 7/10 — Data is accurate. The implied 5-minute relevance is overstated. Better as a daily regime filter. |

---

### Field 5: `gold_price` — Gold Spot Price

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ⚠️ Partially — Finnhub free tier may not include commodity quotes. Can use `GLD` ETF as proxy via IBKR. |
| **Does it provide edge?** | ⚠️ Weak for stock trading — gold is a macro indicator but its 5-minute movements rarely affect individual US stock prices |
| **Honesty rating** | 5/10 — Deliverable but low relevance for 5-minute stock signals. Adds noise, not signal. |

---

### Field 6: `dxy_value` — US Dollar Index

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ⚠️ Partially — DXY is not on Finnhub free tier. Can use `UUP` ETF as proxy via IBKR. |
| **Does it provide edge?** | ⚠️ Weak for individual US stock trading — DXY matters for multinational earnings but not 5-minute entries |
| **Honesty rating** | 5/10 — Same as gold. Deliverable as a proxy but low 5-minute relevance. |

---

### Field 7: `risk_level` — Composite Risk Assessment (LOW/MEDIUM/HIGH/HALTED)

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ✅ Yes — computed from VIX thresholds + macro gate status |
| **Is it correct?** | ✅ Yes — deterministic rules: VIX > 35 = HALTED, VIX > 25 = HIGH, macro_gated = HIGH, else from yield/gold context |
| **Does it provide edge?** | ✅ Good — consolidating multiple macro signals into one actionable level is valuable for the CEO's decision logic |
| **Honesty rating** | 8/10 — Honest and useful composite. |

---

### Field 8: `next_event_name` / `next_event_minutes` — Event Countdown

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ✅ Yes — from Finnhub economic calendar |
| **Is it correct?** | ✅ Yes — simple time math |
| **Does it provide edge?** | ✅ Good — displaying "CPI Release in 22 minutes" on the dashboard and in Telegram alerts is genuinely useful for awareness |
| **Honesty rating** | 8/10 — Delivers exactly what it promises. |

---

## Part 4b-ii Summary: News Agent Honesty Scorecard

| # | Field | Deliverable? | Edge? | Honesty |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `is_macro_gated` | ✅ Yes | ✅ Good | 8/10 |
| 2 | `macro_phase` | ✅ Yes | ✅ Good | 7/10 |
| 3 | `vix_value` | ✅ Yes | ✅ Good | 9/10 |
| 4 | `yield_10y` | ✅ Yes | ⚠️ Moderate (daily, not 5m) | 7/10 |
| 5 | `gold_price` | ⚠️ Proxy only | ⚠️ Weak for stocks | 5/10 |
| 6 | `dxy_value` | ⚠️ Proxy only | ⚠️ Weak for stocks | 5/10 |
| 7 | `risk_level` | ✅ Yes | ✅ Good | 8/10 |
| 8 | `next_event` | ✅ Yes | ✅ Good | 8/10 |
| **Average** | | | | **7.1/10** |

> **Bottom line:** The News Agent is our most honest agent. Its core function (macro event gating + VIX regime filtering) is well-designed and genuinely protective. The weaknesses are gold and DXY — they add complexity without clear 5-minute edge. Consider making them optional display-only fields rather than signal inputs. Rename the agent to `MacroGateAgent`.
