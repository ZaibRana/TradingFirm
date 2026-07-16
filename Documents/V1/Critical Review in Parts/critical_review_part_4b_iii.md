# Critical System Review — Part 4b-iii of 7
## 📊 Data Honesty Audit — SEC Agent

---

### Overall: What Does This Agent Actually Do?

The SEC Agent polls EDGAR's EFTS (Full-Text Search) API for new filings by our watchlist tickers and alerts us via Telegram when it finds insider trades (Form 4), large ownership disclosures (SC 13D/G), or material events (8-K).

**The core question:** Is this useful for a 5-minute intraday trading system?

---

### Field 1: `filing_type` — Type of SEC Filing

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ✅ Yes — EDGAR EFTS returns the form type in its JSON response |
| **Is it correct?** | ✅ Yes — SEC filing types are standardized |
| **What user expects** | "System detects Form 4 (insider trade) → I act on insider information" |
| **What it actually means** | We detected that a filing was PUBLISHED on EDGAR. This is not real-time insider activity — it's the legally required disclosure that happens AFTER the trade. |
| **Honesty rating** | 8/10 — We deliver the correct filing type. |

---

### Field 2: `ticker` — Which Stock the Filing Relates To

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ✅ Yes — EDGAR filings include the company's CIK, which maps to ticker |
| **Caveat** | CIK-to-ticker mapping isn't always 1:1. Some companies have multiple tickers (different share classes). We need to maintain a CIK→ticker lookup table. |
| **Honesty rating** | 7/10 — Works for major stocks, may need manual mapping for edge cases. |

---

### Field 3: `filer_name` — Who Filed (Insider or Institution)

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ✅ Yes — Form 4 includes the reporting person's name |
| **Does it provide edge?** | ⚠️ Context-dependent — knowing that Tim Cook sold AAPL shares is more meaningful than knowing a random VP sold. We don't rank insiders by importance. |
| **Honesty rating** | 7/10 — Data is available. Interpretation is left to user. |

---

### Field 4: `transaction_value` — Dollar Value of the Transaction

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ⚠️ Partially — Form 4 XML includes shares and price, but parsing the XML reliably is complex. Different filing formats, amendments, and multi-transaction filings make this error-prone. |
| **Honesty rating** | 5/10 — Available in theory, tricky to extract reliably in practice. |

---

### Field 5: `filing_url` — Link to the Filing on SEC.gov

| Question | Answer |
| :--- | :--- |
| **Can we deliver it?** | ✅ Yes — EFTS returns the filing URL directly |
| **Honesty rating** | 9/10 — Simple, always correct. |

---

### The Critical Timing Problem

**This is the most important honesty issue for the SEC Agent:**

| Step | When It Happens | Delay |
| :--- | :--- | :--- |
| Insider executes trade | T+0 | — |
| Insider files Form 4 with SEC | Within 2 business days (legally required) | T+1 to T+2 days |
| EDGAR processes and publishes filing | Minutes to hours after submission | T+1 to T+2 days + processing |
| Our system polls EDGAR EFTS | Every 10 minutes | +0 to +10 min after EDGAR publishes |
| **We detect the filing** | | **T+1 to T+2 days + up to 10 min** |

**What this means:** By the time we detect an insider sale, the trade happened 1-2 DAYS ago. The stock may have already moved. Professional services detect filings within seconds of EDGAR publishing — we detect within 10 minutes. But the real delay is the 1-2 day filing requirement.

**Comparison to professional services:**

| Service | Detection Speed After EDGAR Publishes | Cost |
| :--- | :--- | :--- |
| Our system | Up to 10 minutes | Free |
| Quiver Quantitative | ~30 seconds | $10-25/mo |
| InsiderMonkey | ~1-5 minutes | Free (web only, no API) |
| SEC RSS Feed | ~1-2 minutes | Free (but no filtering) |
| Professional terminals (Bloomberg) | ~5-15 seconds | $24K/year |

**Impact:** We're not first to know, and we're not even close. By the time our 10-minute poll detects a Form 4, algorithmic traders using direct EDGAR WebSocket feeds have already reacted.

---

### Is the SEC Agent Worth Building?

| Argument FOR | Argument AGAINST |
| :--- | :--- |
| Insider buying clusters are historically bullish signals | We detect 1-2 days after the trade — the signal is stale |
| SC 13D filings (activist investors) can move stocks 5-20% | These events are rare — maybe 1 per month across our 10 tickers |
| 8-K material events are important to know about | Most 8-K moves happen BEFORE our system detects the filing |
| It's free (EDGAR is public) | Development cost: ~2 weeks of work for a feature with minimal intraday edge |
| Telegram alerts are a nice-to-have for awareness | The alert arrives after the move has already started |

**Verdict:** The SEC Agent is a **monitoring tool**, not a **trading signal source**. It should NOT feed into the CEO's signal scoring. Its value is awareness — "Hey, the CEO of AAPL just sold $10M in shares yesterday. Be cautious."

---

### Recommended Redesign

```
Current Design:
  SEC Agent → SECFiling → CEO considers in verdicts → affects signals

Honest Design:
  SEC Agent → SECFiling → Telegram alert ONLY (informational)
                        → Dashboard display (historical view)
                        → Does NOT affect signal scoring
```

The agent scans EDGAR, alerts you, and displays on the dashboard. But it doesn't modify System A scores or System B verdicts. The user makes their own judgment about whether to adjust their watchlist based on the filing.

---

## Part 4b-iii Summary: SEC Agent Honesty Scorecard

| # | Field | Deliverable? | Edge? | Honesty |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `filing_type` | ✅ Yes | ⚠️ Awareness only | 8/10 |
| 2 | `ticker` | ✅ Yes (with CIK mapping) | ✅ Functional | 7/10 |
| 3 | `filer_name` | ✅ Yes | ⚠️ Needs insider ranking | 7/10 |
| 4 | `transaction_value` | ⚠️ Tricky XML parsing | ⚠️ If extractable | 5/10 |
| 5 | `filing_url` | ✅ Yes | ✅ Always correct | 9/10 |
| 6 | *Timing (overall)* | ⚠️ 10 min after EDGAR | ❌ Too late for intraday edge | 4/10 |
| 7 | *Signal integration* | ❌ Should not affect scores | ❌ Misleading if it does | 5/10 |
| **Average** | | | | **6.4/10** |

> **Bottom line:** The SEC Agent is an honest informational tool but a dishonest signal source. It should send Telegram alerts for awareness, NOT influence the CEO's trading decisions. The 1-2 day filing delay makes it irrelevant for intraday signal generation.

---

## Complete Data Honesty Summary (All Agents)

| Agent | Average Honesty | Best Feature | Worst Feature |
| :--- | :--- | :--- | :--- |
| Technical Agent (System A) | 6.7/10 | VWAP (8/10) | Order Block (5/10) |
| Institutional Agent (System B) | 4.7/10 | Volume Delta (7/10) | Options Flow (1/10) |
| News Agent | 7.1/10 | VIX (9/10) | Gold/DXY (5/10) |
| SEC Agent | 6.4/10 | Filing URL (9/10) | Timing delay (4/10) |
| **System Average** | **6.2/10** | | |

> The system is **6.2/10 honest.** The math is correct everywhere. The dishonesty is in naming, implied capabilities, and predictive claims that the data can't support.
