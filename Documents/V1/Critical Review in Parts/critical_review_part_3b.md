# Critical System Review — Part 3b of 7
## 🚫 Blocking Risks — What Can Get Us Banned

---

### Blocking Risk 1: IBKR Can Restrict Your Account

IBKR is the most critical dependency. If your IBKR account gets restricted, the entire system is dead. Here are the real ways this can happen:

#### 1a. Pattern Day Trader (PDT) Rule
**What it is:** If your account has < $25,000, you're limited to 3 day trades in a rolling 5-business-day period. A "day trade" is buying and selling (or selling and buying) the same security on the same day.

**Our risk:**

| Scenario | Day Trades Used | PDT Violation? |
| :--- | :--- | :--- |
| 3 Trend signals, all exit same day | 3 | 🟡 At the limit |
| 3 Trend + 2 Scalp exits same day | 5 | 🔴 Yes — account frozen for 90 days |
| 5 Scalp signals in one day (fast exits) | 5 | 🔴 Yes |

**Impact:** Account frozen for 90 days. No trading allowed. System is useless.

**Severity: CRITICAL if account < $25K**

**Fix:**
1. Add a `DayTradeCounter` to `DailyRiskState`:
   ```python
   day_trades_used: int = 0           # Increment on each same-day round trip
   day_trades_remaining: int = 3      # PDT limit (3 per 5 days for <$25K)
   is_pdt_restricted: bool = False    # True if account < $25K
   ```
2. CEO must check `day_trades_remaining > 0` BEFORE firing any signal
3. If remaining = 0, block ALL signals and send Telegram: "⚠️ PDT limit reached — no new entries today"
4. If account ≥ $25K, disable PDT tracking

#### 1b. IBKR API Pacing Violations
**What it is:** IBKR silently throttles or disconnects clients that exceed message rate limits.

| Violation | Limit | Consequence |
| :--- | :--- | :--- |
| Too many messages/sec | 50 msg/sec | Connection throttled, then dropped |
| Too many historical requests | 60 per 10 min | Error code 162, request rejected |
| Identical historical request | Within 15 seconds | Request silently ignored |
| Too many active subscriptions | ~100 simultaneously | New subscriptions rejected |

**Our risk:** Low with 10 tickers. Our usage is ~10-15 messages/sec during candle processing, well under 50. Historical requests are spaced 10 seconds apart per our config.

**Severity: LOW** — Already mitigated in the audit.

#### 1c. IBKR Account Closure for "Abusive" API Usage
**What it is:** IBKR's terms of service reserve the right to close accounts for "disruptive" API behavior. In practice, this means:
- Flooding the API with thousands of order modifications per second
- Submitting and immediately cancelling orders (spoofing-like behavior)
- Running multiple API connections from different machines to the same account

**Our risk:** Very low. We don't submit orders programmatically in v1 (manual entry), and we use a single connection.

**Severity: VERY LOW**

---

### Blocking Risk 2: SEC EDGAR IP Block

**What it is:** SEC blocks IPs that make requests without a proper `User-Agent` header or exceed 10 requests/second.

**How the block works:**
- First offense: requests silently return empty/error responses
- Continued abuse: IP added to a block list (reportedly lasts 10 minutes to 24 hours)
- Severe abuse: permanent block requiring email to SEC webmaster to unblock

**Our risk assessment:**

| Factor | Our Usage | Safe? |
| :--- | :--- | :--- |
| User-Agent header | ✅ Configured in SystemConfig (audit C-4) | ✅ |
| Request rate | 1 request per 10 minutes | ✅ Way under 10/sec |
| Request volume | ~6 requests/hour during market hours | ✅ |
| Scraping behavior | We only hit the EFTS API, not HTML pages | ✅ |

**Severity: LOW** — Already mitigated. The User-Agent fix in the audit was the critical one.

**Remaining risk:** If your ISP assigns you a shared IP and another user on that IP is also hammering EDGAR, you could be collateral damage. Very unlikely but possible on shared hosting or VPNs.

---

### Blocking Risk 3: Finnhub 429 Lockout

**What it is:** Finnhub free tier allows 60 API calls per minute. Exceeding this returns HTTP 429 for the remainder of that minute.

**Our usage pattern:**

| Call | Frequency | Per Minute |
| :--- | :--- | :--- |
| `/quote` × 10 tickers | Every 5 min | ~2/min average |
| `/calendar/economic` | Every 15 min | ~0.07/min |
| `/news` | Every 10 min | ~0.1/min |
| **Total** | | **~2.2/min** |

**Safe?** Yes — 2.2/min is well under 60/min. Even if we triple our usage, we're at 6.6/min.

**But watch out for:** The backtest lab. If backtest fetches historical quotes from Finnhub (instead of cached IBKR data), it could burst through the limit quickly. 

**Severity: LOW** — Normal operation is safe. Add a rate limiter wrapper for safety.

**Fix:** Already handled in our error boundary. Finnhub 429 → fall back to cached data → retry after 15 min.

---

### Blocking Risk 4: Telegram Flood-Wait Ban

**What it is:** Telegram bans bots that send too many messages too quickly with a "flood-wait" error. The ban duration escalates:

| Offense | Ban Duration |
| :--- | :--- |
| First: > 1 msg/sec to same chat | 5-30 seconds |
| Repeated: sustained bursting | 1-5 minutes |
| Severe: ignored flood-wait, kept sending | 1-24 hours |
| Extreme: bot flagged as spam | Permanent bot ban |

**Our risk scenario:**
```
10:30:00 — 5 signals fire simultaneously (rare but possible during volatile open)
10:30:00 — System tries to send 5 Telegram messages instantly
10:30:00 — Messages 1-2 succeed
10:30:01 — Message 3 gets flood-wait: 10 seconds
10:30:01 — If we ignore flood-wait and keep sending → ban escalates
```

**Severity: MEDIUM** — Already mitigated (audit C-6: 1 msg/sec pacing + priority queue). But the implementation must respect the `retry_after` field in the 429 response.

**Critical rule:** When Telegram returns `retry_after: N`, the bot MUST wait exactly N seconds before sending anything. Not just the failed message — ALL messages. Sending during the wait period escalates the ban.

**Fix:** Already designed in TDD. Verify during Phase 1 implementation that the Telegram consumer:
1. Reads `retry_after` from the error response
2. Pauses the ENTIRE queue drain for that duration
3. Does NOT retry the failed message before the wait expires

---

### Blocking Risk 5: ISP / Network Level Concerns

**What it is:** Some ISPs or corporate networks flag sustained outbound connections to financial services.

| Concern | Risk Level | Explanation |
| :--- | :--- | :--- |
| **ISP throttling API calls** | VERY LOW | Our total bandwidth is < 1 KB/sec. ISPs throttle streaming video, not tiny JSON requests |
| **Corporate firewall blocking IBKR** | MEDIUM | If trading from a work network, IBKR ports (4001/4002/7496/7497) may be blocked |
| **VPN interference with IBKR** | MEDIUM | IBKR detects VPN usage and may flag it if your apparent location differs from account registration |
| **macOS firewall blocking inbound** | LOW | Streamlit runs locally, IBKR is outbound — macOS firewall typically allows this |

**Severity: LOW** — On a home network, none of these are real concerns. On corporate or public networks, IBKR ports may be blocked.

**Fix:**
1. Trade from home network only (recommended)
2. If using VPN, ensure exit node is in the same country as your IBKR account registration
3. Add IBKR connection test to pre-flight checklist (already there)

---

## Part 3b Summary

| # | Blocking Risk | Severity | Mitigated? |
| :--- | :--- | :--- | :--- |
| 1a | IBKR PDT rule (< $25K account) | 🔴 CRITICAL | ❌ NOT in TDD — must add DayTradeCounter |
| 1b | IBKR API pacing violations | LOW | ✅ Yes (audit C-5) |
| 1c | IBKR account closure | VERY LOW | ✅ Yes (single connection, no order spam) |
| 2 | SEC EDGAR IP block | LOW | ✅ Yes (audit C-4) |
| 3 | Finnhub 429 lockout | LOW | ✅ Yes (error boundary) |
| 4 | Telegram flood-wait ban | MEDIUM | ✅ Yes (audit C-6, must verify retry_after handling) |
| 5 | ISP / network blocking | LOW | ✅ Yes (home network recommendation) |

> **🔴 NEW CRITICAL FINDING: The PDT (Pattern Day Trader) rule is NOT in our TDD.** If the user has an account under $25,000, the system can trigger a 90-day account freeze by firing too many same-day round trips. A `DayTradeCounter` must be added to `DailyRiskState` and the CEO must check it before every signal.
