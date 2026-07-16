# Critical System Review — Part 4b-i of 7
## 📊 Data Honesty Audit — Institutional Agent (System B)

---

### Field 1: `volume_delta` — Net Buying vs Selling Pressure

| Question | Answer |
| :--- | :--- |
| **Can we compute it?** | ✅ Yes — classify each tick as buy (at ask) or sell (at bid), sum the difference |
| **Is it correct?** | ⚠️ Approximately — the "tick rule" (trade at ask = buy, at bid = sell) has ~85% accuracy. Trades at the midpoint are ambiguous and we must guess. |
| **What user expects** | "Positive delta = more buyers than sellers = bullish" |
| **What it actually means** | More trades executed at the ask price than at the bid. This usually means aggressive buying (market orders hitting the ask). But it can also mean market makers absorbing sells at the ask to build inventory. |
| **Does it provide edge?** | ✅ Moderate-Good — volume delta is a legitimate order flow tool used by professional day traders. It's one of the few indicators that measures ACTIVITY, not just price. |
| **Data quality** | 🟡 MEDIUM — depends on tick data completeness. IBKR may consolidate fast ticks. |
| **Honesty rating** | 7/10 — Legitimately useful. The 85% classification accuracy is the main limitation. |

---

### Field 2: `cumulative_delta` — Session Running Total

| Question | Answer |
| :--- | :--- |
| **Can we compute it?** | ✅ Yes — running sum of volume_delta since session open |
| **Is it correct?** | ✅ Yes — simple accumulation |
| **What user expects** | "Rising cumulative delta = sustained buying pressure all day" |
| **What it actually means** | The NET buy-sell imbalance since 9:30 AM. A rising cumulative delta with rising price = healthy trend. Rising price with falling delta = divergence (potential reversal). |
| **Does it provide edge?** | ✅ Moderate — the divergence signal (price up, delta down) is genuinely predictive of short-term reversals. |
| **Caveat** | Cumulative delta is noisy in the first 30 minutes (open rotation) and last 30 minutes (MOC orders). These periods should be treated differently. |
| **Data quality** | 🟡 MEDIUM — inherits tick classification uncertainty from volume_delta |
| **Honesty rating** | 7/10 — Useful, especially for divergence detection. |

---

### Field 3: `dom_imbalance` — Depth of Market Ratio

| Question | Answer |
| :--- | :--- |
| **Can we compute it?** | ⚠️ Conditionally — requires paid L2 subscription ($10-25/mo per exchange) |
| **Is it correct?** | ⚠️ The math is correct (bid size / ask size ratio), but the INPUT data is unreliable |
| **What user expects** | "More bids than asks on the order book = buyers ready to support price" |
| **What it actually means** | At this exact microsecond, more DISPLAYED orders are on the bid side. But 70-80% of displayed orders are cancelled before execution. Market makers routinely show and pull orders to manipulate perception. |
| **Does it provide edge?** | ❌ Weak — academic research consistently shows that visible DOM is a poor predictor of short-term price direction for liquid US stocks. It has some value for illiquid stocks. |
| **Why we still include it** | It's part of System B's composite verdict. Even if individually weak, it adds a datapoint. Made `Optional` so system works without it. |
| **Data quality** | 🔴 LOW — shallow (5-10 levels), gameable, requires paid subscription |
| **Honesty rating** | 3/10 — The field exists and computes correctly, but it pretends to measure institutional intent when it actually measures displayed (often fake) orders. |

---

### Field 4: `poc_price` / `vah_price` / `val_price` — Volume Profile

| Question | Answer |
| :--- | :--- |
| **Can we compute it?** | ✅ Yes — aggregate tick volumes into price buckets, find highest-volume bucket (POC) and 70% value area |
| **Is it correct?** | ⚠️ Approximately — 2-5% error vs professional tools for liquid stocks, 10-20% for illiquid |
| **What user expects** | "POC = the price where most trading happened = strongest support/resistance" |
| **What it actually means** | The price level with the highest accumulated volume today. Institutions DO use Volume Profile for execution benchmarks. POC is a real concept with real market significance. |
| **Does it provide edge?** | ✅ Moderate — POC and value area edges are legitimate intraday support/resistance levels. Not perfect, but better than arbitrary round numbers. |
| **Caveat** | Our tick-based Volume Profile may differ from exchange-reported profiles. Use for directional guidance ("price is above/below POC"), not for exact entry levels. |
| **Data quality** | 🟡 MEDIUM — depends on tick completeness |
| **Honesty rating** | 6/10 — Real concept, approximate execution. Acceptable for 5-minute trading. |

---

### Field 5: `unusual_options_strikes` — Options Flow Detection

| Question | Answer |
| :--- | :--- |
| **Can we compute it?** | 🔴 NO — we have zero data source for this |
| **Is it correct?** | N/A — field will always be `[]` |
| **What user expects** | "Large unusual options bets detected = smart money is positioning" |
| **What it actually delivers** | An empty list. Every time. |
| **Does it provide edge?** | N/A — no data means no edge |
| **Data quality** | 🔴 NONE — no data source exists in our stack |
| **Honesty rating** | 1/10 — The field's existence in the dataclass is a lie. It promises a feature we cannot deliver. |

**Verdict:** Remove this field from `InstitutionalReport` for v1. Add it back ONLY when a paid data source (Unusual Whales $57/mo, FlowAlgo $99/mo) is integrated.

---

### Field 6: `large_block_prints` — Dark Pool Proxy

| Question | Answer |
| :--- | :--- |
| **Can we compute it?** | ✅ Yes — count trades > 10,000 shares at or near the bid-ask midpoint |
| **Is it correct?** | ⚠️ Heuristic — catches some dark pool prints but also catches large lit-exchange trades. False positive rate: 20-40%. |
| **What user expects** | "Dark pool block trade detected = institutional accumulation" |
| **What it actually means** | A large trade occurred near the midpoint. It MIGHT be a dark pool cross. It might also be a large institutional limit order filled on NYSE/NASDAQ. We genuinely cannot tell the difference from tick data alone. |
| **Does it provide edge?** | ⚠️ Weak — the signal is noisy. A single large print doesn't mean much. A CLUSTER of large prints at the same price level over 30 minutes is more meaningful, but we don't track clusters. |
| **Data quality** | 🟡 MEDIUM — the trade data is real, but our classification is uncertain |
| **Honesty rating** | 4/10 — Renamed from `dark_pool_blocks` (dishonest) to `large_block_prints` (more honest) in the audit. Still overstates our ability to identify dark pool activity. |

---

## Part 4b-i Summary: Institutional Agent Honesty Scorecard

| # | Field | Deliverable? | Edge? | Honesty |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `volume_delta` | ✅ Yes | ✅ Moderate-Good | 7/10 |
| 2 | `cumulative_delta` | ✅ Yes | ✅ Moderate | 7/10 |
| 3 | `dom_imbalance` | ⚠️ Paid + unreliable | ❌ Weak | 3/10 |
| 4 | `poc/vah/val` (Volume Profile) | ✅ Approximate | ✅ Moderate | 6/10 |
| 5 | `unusual_options_strikes` | 🔴 NO | N/A | 1/10 |
| 6 | `large_block_prints` | ⚠️ Heuristic | ⚠️ Weak | 4/10 |
| **Average** | | | | **4.7/10** |

> **Bottom line:** The Institutional Agent's honest edge comes from **volume delta** and **cumulative delta** — these are legitimate order flow tools. Everything else ranges from approximate (Volume Profile) to misleading (DOM) to undeliverable (options flow). The agent should be rebuilt around delta analysis as its primary signal, with Volume Profile as secondary context. DOM and options flow should be deprioritized or removed.
