# Critical System Review — Part 3a of 7
## 🔒 Security Flaws

---

### Security Flaw 1: `secrets.toml` Is Plaintext on Disk

**What we store there:** Telegram bot token, IBKR connection params, Finnhub API key, FRED API key, SEC email.

**The risk:**
- `secrets.toml` is a plain text file — anyone with access to the MacBook can read it
- If the repo is accidentally pushed to GitHub with `secrets.toml` included, all API keys are exposed
- macOS Spotlight indexes file contents — searching "telegram" on the MacBook could surface the bot token

**How bad is it?**

| If Leaked | What an attacker can do | Severity |
| :--- | :--- | :--- |
| Telegram bot token | Send messages AS your bot to your chat, read message history, impersonate signals | HIGH |
| Finnhub API key | Use your quota (60 req/min) — annoying but not dangerous | LOW |
| FRED API key | Use your quota — harmless | LOW |
| IBKR host/port | Only useful if on same network — local socket | LOW |
| SEC email | Spam target — minor | LOW |

**Severity: MEDIUM** — The Telegram token is the real risk. Finnhub/FRED keys are low value.

**Fix:**
1. **`.gitignore` must include `secrets.toml`** — already planned ✅
2. **macOS Keychain:** For higher security, store the Telegram token in macOS Keychain and retrieve at runtime:
   ```python
   import subprocess
   def get_secret(key: str) -> str:
       result = subprocess.run(
           ["security", "find-generic-password", "-s", key, "-w"],
           capture_output=True, text=True
       )
       return result.stdout.strip()
   ```
3. **File permissions:** `chmod 600 .streamlit/secrets.toml` — only owner can read
4. **Pre-commit hook:** Add a git pre-commit hook that rejects commits containing `secrets.toml`

---

### Security Flaw 2: Streamlit Dashboard Has Zero Authentication

**What we expose:** Real-time positions, P&L, signal history, watchlist, account state.

**The risk:** By default, `streamlit run app.py` opens a web server on `http://localhost:8501`. But:

| Scenario | Who can see your dashboard? |
| :--- | :--- |
| At home, private WiFi | Only you | ✅ Safe |
| At coffee shop, public WiFi | Anyone on the same network who scans for open ports | 🔴 Exposed |
| Using a VPN to remote access | Anyone who intercepts the VPN traffic (if not encrypted) | 🟠 Risk |
| Port forwarding to access from phone | Anyone on the internet if port is public | 🔴 Exposed |

**What an attacker sees:** Your open positions, entry prices, stop-loss levels, account equity percentage, watchlist tickers, and real-time signals. This is enough to front-run your trades or know when your stops will trigger.

**Severity: MEDIUM** — If you only use it at home on private WiFi, the risk is minimal. If you ever access it remotely, it becomes HIGH.

**Fix:**
1. **Bind to localhost only** (default Streamlit behavior — verify with `streamlit run app.py --server.address localhost`)
2. **If remote access needed,** use SSH tunnel instead of port forwarding:
   ```bash
   # From your phone/remote machine:
   ssh -L 8501:localhost:8501 your_macbook_ip
   # Then open http://localhost:8501 on the remote device
   ```
3. **Streamlit authentication:** Add `streamlit-authenticator` package for username/password login:
   ```bash
   pip install streamlit-authenticator
   ```
4. **Never expose port 8501 to the public internet**

---

### Security Flaw 3: Telegram Bot Token = Full Access to Your Bot

**What we do:** Send signal alerts via Telegram bot.

**The risk:** The Telegram Bot API token grants complete control over the bot:
- **Read** all messages sent to the bot
- **Send** messages to any chat the bot is in
- **Impersonate** signal alerts (send fake BUY signals to your chat)
- **Delete** messages
- **Set webhooks** that forward all messages to an attacker's server

**Attack scenario:**
```
1. Attacker obtains your bot token (leaked secrets.toml, shoulder surfing, etc.)
2. Attacker sends: "🔴 EMERGENCY EXIT ALL POSITIONS — SYSTEM DETECTED CRASH"
3. You panic-sell everything
4. Attacker buys the dip and profits from your selling pressure
```

This sounds extreme, but if you're trading real money based on Telegram signals, bot security matters.

**Severity: MEDIUM-HIGH** — If anyone else has physical or remote access to your MacBook, this is a real vector.

**Fix:**
1. **Message signing:** Add a verification hash to every signal message:
   ```python
   import hashlib
   
   def sign_message(message: str, secret: str) -> str:
       """Append a verification hash to prevent fake signals."""
       hash_val = hashlib.sha256(f"{message}{secret}".encode()).hexdigest()[:8]
       return f"{message}\n\n🔑 Verify: {hash_val}"
   ```
   You mentally check the hash prefix matches what you expect. If a message arrives without the hash or with a wrong hash, you know it's fake.

2. **Separate bot for alerts vs commands:** Use one bot for sending signals (token stored in app), a different bot for receiving commands (if you add command features later)

3. **Rotate token periodically:** If you suspect compromise, revoke the token via @BotFather → `/revoke` and generate a new one

---

### Security Flaw 4: Position & Signal Data Is Unencrypted on Disk

**What we store:** `positions.json`, `daily_risk_state.json`, signal history files.

**The risk:** These files contain:
- Every trade you've made (ticker, entry price, size, stop-loss)
- Your current open positions and exact stop-loss levels
- Your P&L history and win/loss patterns
- Your watchlist (reveals your trading interests)

If someone copies your `data/` directory, they have a complete picture of your trading behavior.

**Severity: LOW** — For a single-user MacBook system, if someone has physical access to your machine, they have bigger problems than your trading data. macOS FileVault disk encryption covers this at the OS level.

**Fix:**
1. **Enable macOS FileVault** (full disk encryption) — this is the simplest and most effective protection
2. **Add to pre-flight checklist:** Verify FileVault is enabled
3. For v1, do NOT implement application-level encryption — it adds complexity without meaningful security beyond FileVault

---

### Security Flaw 5: IBKR Local Socket Has No Authentication

**What we do:** Connect to TWS/IB Gateway via TCP socket on `127.0.0.1:7497`.

**The risk:** The IBKR TWS API socket accepts connections from ANY local process. There is no username, password, or token. Any program running on your MacBook can:
- Connect to port 7497
- Send order commands
- Cancel existing orders
- Query your account data

**How realistic is this?** Low — an attacker would need to be running malicious software on your MacBook already. At that point, they have bigger access than just IBKR.

**Severity: LOW** — This is an IBKR design decision, not something we can change. TWS does show a confirmation dialog for API connections (if enabled), which provides a manual gate.

**Fix:**
1. **Enable "Read-Only API" in TWS during development** — prevents any order submission via API
2. **Enable "Precautions" in TWS** — shows a popup for each API order, requiring manual confirmation
3. **Disable API auto-connect** — TWS asks you to confirm each new API connection
4. **For live trading:** Only disable these guards when you're actively running the system and watching

---

## Part 3a Summary

| # | Flaw | Severity | Fix |
| :--- | :--- | :--- | :--- |
| 1 | secrets.toml is plaintext | MEDIUM | chmod 600, .gitignore, pre-commit hook |
| 2 | Dashboard has no auth | MEDIUM | Bind localhost, SSH tunnel for remote |
| 3 | Telegram token = full bot access | MEDIUM-HIGH | Message signing hash, rotate periodically |
| 4 | Data unencrypted on disk | LOW | macOS FileVault covers this |
| 5 | IBKR socket has no auth | LOW | IBKR design — enable TWS precautions |

> **Bottom line:** No critical security holes that would require architecture changes. The main actionable fix is **message signing for Telegram** to prevent fake signal injection. Everything else is standard operational security (file permissions, gitignore, disk encryption).
