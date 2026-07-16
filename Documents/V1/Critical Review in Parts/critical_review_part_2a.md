# Critical System Review — Part 2a of 7
## ❌ Data Quality Weaknesses — Claims vs Reality

---

### Weakness 1: "Institutional Agent" Uses Retail-Grade Data

**What we claim:** An "Institutional Agent" that evaluates order flow and microstructure.

**The hard truth:** We're reading IBKR retail tick data and calling it "institutional analysis." Real institutional flow detection requires:

| Data Type | What We Have (IBKR Retail) | What Institutions Use | Cost |
| :--- | :--- | :--- | :--- |
| Order flow | Tick-by-tick trades (consolidated tape) | Direct exchange feeds (ITCH, PITCH) | $2K-10K/mo |
| Dark pool prints | Heuristic guess (trades > 10k at midpoint) | FINRA ADF dark pool feed | $500-2K/mo |
| DOM / Level 2 | 5-10 levels of depth (if L2 subscribed) | Full book (all levels, all exchanges) | $5K+/mo |
| Options flow | **NONE** — we have no source | Real-time options tape (OPRA feed) | $1K-5K/mo |
| Block trades | Size-based guess | Broker-dealer reported blocks | Not available to retail |

**Impact:** The "Institutional Agent" is really a "Retail Microstructure Agent." The name sets expectations we can't meet. The analysis is legitimate but limited — it's reading the same data that any retail TradingView user can see.

**Severity: HIGH** — Misleading naming leads to false confidence in signals.

**Fix:** Rename to `MicrostructureAgent` or `OrderFlowAgent`. Document clearly in the README that this is retail-grade order flow analysis, not institutional-grade.

---

### Weakness 2: DOM Imbalance Is Shallow and Gameable

**What we claim:** DOM (Depth of Market) imbalance ratio as a signal input.

**The hard truth:**
- IBKR L2 shows only **5-10 price levels** of the order book per exchange
- Market makers routinely place and cancel large orders ("spoofing") to manipulate visible DOM
- High-frequency traders update their orders faster than our 5-second Guardian cycle can see
- DOM data is a **snapshot of intent**, not a commitment — 70-80% of visible orders are cancelled before execution

**Real-world example:** You see a huge bid wall at $150.00 (10,000 shares). You interpret this as bullish support. A market maker placed that wall to encourage retail buying, then pulls it 200ms later and sells into the buying pressure. Our system can't detect this because we sample every 5 seconds.

**Impact:** DOM imbalance is unreliable as a standalone signal. It has some value as confirming evidence when combined with volume delta, but treating it as an equal input to the Institutional verdict is overweighting noisy data.

**Severity: MEDIUM** — Not harmful (we made it Optional in the audit), but it contributes less alpha than implied.

**Fix:** Weight DOM imbalance lower in the institutional verdict. Use it as a tiebreaker, not a primary input. Add a warning comment in the code.

---

### Weakness 3: `unusual_options_strikes` Has NO Data Source

**What we claim:** The `InstitutionalReport` includes an `unusual_options_strikes: List[str]` field.

**The hard truth:** We have absolutely no data source for unusual options activity. Let's check what's actually available:

| Source | Provides Options Flow? | Cost | API? |
| :--- | :--- | :--- | :--- |
| IBKR TWS API | Can query option chains, but NO aggregated unusual activity detection | Included | Yes |
| Finnhub Free | ❌ No options flow | Free | Yes |
| Unusual Whales | ✅ Yes — unusual sweeps, block trades | $57/mo (Standard) | Yes |
| FlowAlgo | ✅ Yes — dark pool + options flow | $99/mo (Pro) | Limited |
| OptionStrat | Options pricing/strategy, NOT flow | $30/mo | No API |
| CBOE LiveVol | ✅ Professional options analytics | $100+/mo | Yes |
| Tradier API | Can pull option chains, but no unusual detection | Free tier | Yes |

**Impact:** This field will always be an empty list `[]` unless we either:
1. Pay for an options flow service ($57-100/mo)
2. Build our own unusual activity detector from raw IBKR option chain snapshots (complex, slow, unreliable)
3. Remove the field entirely

**Severity: HIGH** — We promise a feature we literally cannot deliver with our current data sources.

**Fix (options):**
- **Option A (Honest):** Remove `unusual_options_strikes` from `InstitutionalReport`. Add it back when/if the user subscribes to Unusual Whales or similar.
- **Option B (DIY):** Query IBKR option chains periodically and flag strikes with volume > 5x average. This is approximate but functional. Adds complexity and API calls.
- **Option C (Paid):** Integrate Unusual Whales API ($57/mo). They have a REST API that returns sweeps and block trades.

**Recommendation:** Option A for v1. Document as a "Future Enhancement" that requires a paid data source.

---

### Weakness 4: Volume Profile from Tick Data Is an Approximation

**What we claim:** Volume Profile with POC (Point of Control), VAH (Value Area High), VAL (Value Area Low).

**The hard truth:**
- Professional Volume Profile tools (Sierra Chart, Bookmap, TradingView Pro) use **exchange-reported volume at each price level** aggregated over the session
- We're building Volume Profile from individual ticks, which means:
  - We miss volume from trades that don't appear in our tick stream (our connection may drop ticks during high volume)
  - Our price bucketing (rounding to nearest cent) may differ from professional tools
  - IBKR tick-by-tick data is already consolidated — some micro-trades are aggregated before delivery

**How much error?** In practice, for liquid large-cap stocks (AAPL, MSFT, NVDA), the error is **2-5% on POC price and 5-10% on value area boundaries.** For less liquid stocks, error can be 10-20%.

**Impact:** The POC/VAH/VAL values will be "close enough" for directional guidance but not precise enough for tight scalp entries that depend on exact price levels.

**Severity: MEDIUM** — Acceptable for trend trades. Risky for scalp entries that need ±$0.10 precision.

**Fix:** Document the approximation. For v1, use Volume Profile as directional context ("price is above/below POC"), not as precise entry/exit levels.

---

## Part 2a Summary

| # | Weakness | Severity | Fixable? |
| :--- | :--- | :--- | :--- |
| 1 | "Institutional Agent" uses retail data | HIGH | Rename + document |
| 2 | DOM imbalance is shallow and gameable | MEDIUM | Lower weight in verdict |
| 3 | Options flow field has no data source | HIGH | Remove for v1 |
| 4 | Volume Profile from ticks is approximate | MEDIUM | Document limitation |

> **Bottom line:** The system's data is legitimate retail-grade data. The problem is that the *naming and positioning* imply institutional-grade intelligence that we can't deliver. **Fix the expectations, not the architecture.**
