# Critical System Review — Part 2c-ii of 7
## ❌ Operational Risks — Platform, Infrastructure & Backtest

---

### Weakness 13: Streamlit Is NOT Designed for Real-Time Trading

**What we claim:** A live trading dashboard that displays real-time prices, signals, and position status.

**The hard truth:** Streamlit has a fundamental design model that fights against real-time applications:

| Streamlit Behavior | Impact on Trading Dashboard |
| :--- | :--- |
| **Full script rerun on every click** | User clicks a button → entire `app.py` re-executes from line 1. If startup takes 2 seconds, every interaction has a 2-second lag. |
| **No WebSocket push to browser** | Streamlit polls for updates, doesn't push. New signals don't appear instantly — the user must wait for the next poll cycle or interact with the page. |
| **`st.rerun()` is expensive** | To force-update the display, we call `st.rerun()` which re-executes everything. During market hours, calling this every 5 seconds creates visible flicker. |
| **Session state is per-tab** | If the user opens two browser tabs, they get two separate sessions with separate state. Confusing and potentially dangerous. |
| **No persistent WebSocket connection** | If the user's browser tab is backgrounded (phone screen off, laptop lid closed), Streamlit's session may time out and disconnect. Threads keep running but UI becomes stale. |

**Real-world scenario:**
```
10:30:00 — Signal fires for AAPL (Thread 2b writes to shared_state)
10:30:00 — User's phone screen is off (Streamlit session idle)
10:30:15 — User opens phone, sees the dashboard
10:30:16 — Streamlit detects session reactivation, reruns script
10:30:18 — Signal finally appears on screen (18 seconds late)
10:30:18 — But the Telegram notification arrived at 10:30:01 ✅
```

**Impact:** The Telegram bot is the REAL primary interface, not Streamlit. The dashboard is useful for monitoring and backtest review, but it's not reliable as a real-time trading terminal.

**Severity: MEDIUM** — Telegram covers the critical path (signal delivery). Streamlit is a monitoring/analysis tool, not the trading interface.

**Fix:**
1. **Accept Streamlit's role:** It's a monitoring dashboard and backtest lab, not a real-time trading terminal
2. **Primary interface is Telegram:** All time-critical signals go through Telegram (already designed this way)
3. **Future upgrade path:** If real-time UI becomes critical, migrate to FastAPI + WebSocket + React frontend. But for v1, Streamlit is adequate for its intended role.

---

### Weakness 14: Single MacBook = Single Point of Failure

**What we claim:** A trading system that monitors positions and fires emergency exits.

**The hard truth:** Everything runs on one MacBook. If the MacBook fails, everything stops simultaneously:

| Failure Scenario | Impact | Mitigation in Our Design? |
| :--- | :--- | :--- |
| **MacBook goes to sleep** | All threads pause. Price Guardian stops. Open positions unprotected. | ❌ No — user must disable sleep manually |
| **macOS update + restart** | System killed instantly. Open positions unprotected for minutes. | ⚠️ Partial — positions persist to disk, but Guardian is dead during restart |
| **WiFi drops for 10 minutes** | IBKR disconnects. No price data. Guardian can't check stops. | ⚠️ Partial — IBKR has server-side stop orders IF we submit them |
| **Power outage** | Everything dead. | ❌ No protection |
| **MacBook thermal throttling** | Unlikely (3-5% CPU), but possible during backtest | ✅ Handled — backtest is non-critical |
| **Python process crash** | All threads die. | ⚠️ Partial — positions persist, but need manual restart |

**The critical gap:** When our system is down, open positions have NO stop-loss protection unless we submit server-side stop orders to IBKR.

**Severity: HIGH** — This is a real risk for live trading with real money.

**Fix (Critical — must implement before live trading):**
1. **IBKR Server-Side Stops:** When entering a position, submit a bracket order (entry + stop-loss + take-profit) directly to IBKR's servers. Even if our MacBook explodes, IBKR will execute the stop.
   ```
   Our system fires BUY signal → User confirms →
   We submit to IBKR:
     1. BUY 100 AAPL @ MARKET
     2. SELL 100 AAPL @ STOP $149.00 (server-side, survives disconnect)
     3. SELL 100 AAPL @ LIMIT $152.50 (take-profit, server-side)
   ```
2. **Disable macOS Sleep:** Add to pre-flight checklist: `caffeinate -d &` or System Settings → disable sleep while on power
3. **Process Supervisor:** Use `launchd` (macOS) or a simple watchdog script that restarts the Python process if it crashes

---

### Weakness 15: Backtest Lab Doesn't Model Real Market Friction

**What we claim:** A backtest lab that replays 90 days of historical data through the CEO pipeline.

**The hard truth:** Our backtest replays bars through our signal logic and counts wins/losses. But it doesn't simulate:

| Real-World Factor | Our Backtest Models It? | Impact on Results |
| :--- | :--- | :--- |
| Slippage on entry | ❌ Assumes exact signal price fill | Backtest overstates profits by ~$0.05-0.20 per trade |
| Slippage on exit | ❌ Assumes exact stop/target fill | Stop-losses fill worse in fast moves |
| Spread cost | ❌ Assumes zero spread | Each round-trip costs $0.02-0.10 in spread |
| Partial fills | ❌ Assumes full fill instantly | In reality, large orders may not fill completely |
| Signal delay | ❌ Assumes instant entry at candle close | Real entry is 15-60 seconds later |
| Market impact | ❌ Assumes our order doesn't move price | For small retail size, this is actually fine |
| Look-ahead bias | ⚠️ Risk if code accidentally uses future bars | Must be carefully validated |

**The "backtest looks great, live trading disappoints" problem:** 
This is the #1 trap in algorithmic trading. A strategy that shows 65% win rate in backtest typically delivers 45-55% in live trading due to the factors above. This gap is called **backtest overfitting** or **execution gap**.

**Severity: HIGH** — If we trust backtest results at face value, we'll be overconfident going live.

**Fix:**
1. Add a **slippage penalty** to every backtest trade: `entry_price += ATR * 0.05` (5% of ATR slippage)
2. Add a **spread cost**: subtract $0.02 per share from every trade's P&L
3. Add a **delay penalty**: use the price 1 bar later (next 5m open) as the actual entry instead of the signal bar's close
4. Display backtest results with a prominent disclaimer: "Live results typically 10-20% worse than backtest"
5. Show TWO columns: "Ideal" (no friction) and "Realistic" (with friction modeled)

---

### Weakness 16: File-Based Storage Has a Scaling Ceiling

**What we claim:** JSON and JSONL files for all persistence.

**The hard truth:** File-based storage works for our current scope (10 tickers, < 50 trades/day) but hits real limits:

| Operation | File-Based Performance | SQLite Performance |
| :--- | :--- | :--- |
| Write 1 position update | ~1ms (`os.replace`) | ~0.5ms |
| Read today's signals | Scan entire JSONL file | Index lookup: < 1ms |
| Query "all AAPL trades last month" | Load file + filter in Python | SQL WHERE clause: < 1ms |
| Concurrent readers/writers | Lock contention | WAL mode handles it natively |
| Find worst P&L trade in 90 days | Load all monthly files + sort | SQL ORDER BY: < 1ms |
| Atomic writes | `tempfile` + `os.replace` | Built-in transaction support |

**When it breaks:** After 6+ months, the backtest lab wants to query historical signals by ticker + date range + signal type. With JSONL files, this means loading and parsing multiple monthly files in Python. With SQLite, it's a single SQL query.

**Severity: LOW for v1** — File-based is fine for the first 3-6 months. But if we plan to keep this system running for a year+, we should plan a migration path.

**Fix:**
- **v1:** Keep file-based. It's simpler, easier to debug, and adequate for our scale.
- **v2 (Month 4+):** Migrate to SQLite. It's a single file, zero-config, built into Python, and handles everything we need. NOT Postgres or MySQL — that's overkill for a single-user system.

---

## Part 2c-ii Summary

| # | Weakness | Severity | Fix Approach |
| :--- | :--- | :--- | :--- |
| 13 | Streamlit not built for real-time | MEDIUM | Accept its role — Telegram is the real interface |
| 14 | Single MacBook = single point of failure | HIGH | IBKR server-side bracket orders + disable sleep |
| 15 | Backtest doesn't model friction | HIGH | Add slippage, spread, delay penalties |
| 16 | File-based storage ceiling | LOW | Fine for v1, migrate to SQLite in v2 |

> **Bottom line:** The #1 operational risk is open positions with no protection when the MacBook is down. **IBKR server-side stop orders must be implemented before live trading.** This is non-negotiable.
