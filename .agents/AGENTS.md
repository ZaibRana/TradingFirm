# TradingFirm — Development Rules

## STOP BEFORE CODING — Mandatory Pre-Flight

### Rule 1: Grill-Me First
- Before ANY new feature or service, use `/grill-me` to interview the user
- Resolve ALL design decisions BEFORE writing code
- Ask about: data sources, API keys, libraries, deployment, constraints
- NEVER assume a library or data source — always ask

### Rule 2: Spec Before Code
- Write a 3-sentence spec: what it does, what it must NOT do, acceptance criteria
- Get user approval on the spec before touching any file
- If the task is vague, ask questions — don't guess

---

## TESTING — Small Before Big

### Rule 3: Smoke Test Every Module
- After building ANY module, test with 1 input → 1 output IMMEDIATELY
- Show the user the actual result before saying "it works"
- NEVER say "that's expected" when something fails — investigate first

### Rule 4: Scale Incrementally
- Test 1 item → then 5 → then full load
- NEVER go from 0 to 650 tickers without verifying 1 works
- Log sample data at every pipeline stage (first 3 items minimum)

### Rule 5: One Strike Rule
- If something fails, STOP immediately — do NOT keep retrying blindly
- Analyze root cause FIRST, test your fix with 1 item, only then retry
- Don't burn tokens/time/rate-limits guessing — understand the problem before trying again

---

## LIBRARIES & APIs — No Scrapers in Production

### Rule 6: No Scraper Libraries
- NEVER use web scrapers (yfinance, finvizfinance, beautifulsoup) as primary data sources
- Always ask the user which API/data provider they want
- Present options with pricing: free tier, mid-tier, production-grade

### Rule 7: Test Library Output First
- Before building a pipeline on any library, test its output with 3 lines of code
- Print the raw output, verify it's correct
- THEN build the pipeline around it

### Rule 8: Pin and Verify Versions
- Always pin library versions in requirements.txt
- After installing, run a quick import + basic call to verify it works
- Check for known bugs in that version before committing to it

---

## API RATE LIMITS — Never Get Blocked

### Rule 9: Research Rate Limits Before First Call
- Search for rate limits of any API BEFORE making the first request
- Document the limits in AGENTS.md
- Implement throttling BEFORE the first integration test

### Rule 10: Protect the IP
- Add delays between all external API calls (minimum 1-2 seconds)
- Use exponential backoff (max 3 retries)
- If rate-limited, STOP immediately — don't retry-storm
- Track request counts and enforce cooldowns
- NEVER run untested code against a live API at scale — test with 1 call first
- Use browser-like User-Agent headers on all HTTP sessions
- If an API returns 429 or blocks, STOP ALL requests — wait 15-30 min minimum
- NEVER risk getting the user's IP or Mac address blocked — treat this as the #1 priority
- If unsure whether a call is safe, DON'T make it — ask the user first

---

## CODE QUALITY

### Rule 11: Write Tests Alongside Code
- Every module gets at least 1 unit test before moving to the next module
- Test edge cases: empty input, single item, error responses
- Tests must run without external APIs (mock the providers)

### Rule 12: STRICT Garbage Control
- Close ALL connections on shutdown — DB pools, Redis, HTTP sessions, file handles
- `del` every large DataFrame immediately after use, followed by `gc.collect()`
- Never store raw data in app.state — only lightweight results (dicts, not DataFrames)
- Every background task MUST clean up its own variables — no memory leaks
- Every function that creates large objects MUST delete them before returning
- Monitor memory in long-running processes — if it grows, there's a leak, fix it

### Rule 13: Log Intelligently
- Log sample data at each pipeline stage (first 3 items)
- Log rejection reasons with counts
- Log errors with context (ticker name, step number, input data)
- Use WARNING for recoverable issues, ERROR for failures

---

## ARCHITECTURE DECISIONS

### Rule 14: Don't Over-Engineer Early
- Build the simplest working version first
- Add complexity only when the user asks for it
- Prefer battle-tested libraries over clever custom code

### Rule 15: Challenge Your Own Decisions
- Before implementing, ask yourself: "Is this the simplest way?"
- If porting old code, question whether the old approach still makes sense
- If a library has issues, consider alternatives instead of working around bugs
- Think TWICE before writing code or telling the user something — verify first
- Search thoroughly before suggesting or implementing ANYTHING — don't guess
- Use ONLY standard, reliable, well-documented APIs — never obscure or unmaintained ones
- Every piece of code must be production-quality from day one — no "fix it later"

---

## COMMUNICATION

### Rule 16: Be Honest About Failures
- If something fails, say it clearly — don't minimize or dismiss
- A 500 error is a BUG, never "expected behavior"
- 0 results is a problem, not "it will work later"

### Rule 17: Show Don't Tell
- Show actual output, not summaries
- If a test passes, show the response
- If it fails, show the error message and logs

### Rule 18: Admit Limitations
- If you can't do something, SAY SO — don't fake it or make up a story
- It is ALWAYS better to say "I don't know" or "I can't do this" than to hallucinate
- Never cover up a failure with confident-sounding excuses

---

## API Rate Limit Specifics

### MANDATORY for ANY API (Not Just These)
- **Before integrating ANY new API**: search for its rate limits, document them here, implement throttling FIRST
- **Never make the first real call at scale** — test with 1 request, verify response, then scale
- **Always add delays** between calls (minimum 1-2 seconds unless API docs say otherwise)
- **Always handle 429/rate-limit responses** — stop immediately, back off, never retry-storm
- **Always use persistent sessions** with proper headers — never raw requests in a loop
- **If rate limit docs don't exist**, assume the strictest limits and ask the user before proceeding

### yfinance (Yahoo Finance) — UNOFFICIAL SCRAPER
- **No official rate limit** — Yahoo uses dynamic anti-scraping detection
- **NEVER** make rapid sequential `yf.Ticker().info` calls — add `time.sleep(2)` minimum between calls
- **ALWAYS** use `yf.download()` for bulk OHLCV (batches tickers into one request)
- **ALWAYS** pass a persistent `requests.Session` with a real browser `User-Agent` to `yf.Ticker(symbol, session=session)`
- **MAX batch size** for `yf.download()`: 20 tickers per call (split larger lists)
- **NEVER** retry more than 3 times with exponential backoff (1s, 2s, 4s) — retry storms amplify blocks
- **ALWAYS** check for 429 errors and back off — never ignore them
- Since 2024, Yahoo aggressively blocks cloud/container IPs — Docker containers are at higher risk
- **ALWAYS** use `threads=False` and pass `session=` to `yf.download()`

### Finviz (finvizfinance) — WEB SCRAPER
- **Add 1–2 second delay** between screener calls (we make 9 exchange×cap combos)
- **Use random delays** (`random.uniform(1.0, 2.5)`) to avoid pattern detection
- **Cache results** — never re-scrape if data is < 1 hour old
- **One scan at a time** — never run concurrent scans
- **⚠️ Known Bug**: finvizfinance 1.3.0 doubles the first character of every ticker — must strip duplicated first char

### General Anti-Block Rules
- **Minimum 10-minute cooldown** between full scans (Finviz + yfinance combined)
- **Rate limit the /scan/run endpoint** — reject if scan ran < 10 minutes ago
- **Close all connections** in lifespan shutdown (DB pool, Redis, requests.Session)
- **Clean up large DataFrames** after processing — use `del df; gc.collect()` in scanner
- **Never store raw DataFrames** in app.state — only store lightweight results

### Resource Cleanup Rules
- All DB connection pools MUST use lifespan startup/shutdown
- All Redis connections MUST be closed on shutdown
- Background tasks MUST not leak memory — clear local variables
- Session objects MUST be reused (created once in __init__, not per-call)

### Concurrent Scan Prevention
- Only one scan can run at a time — check Redis `tf:cache:scan_status` before starting
- If a scan is already running, return 409 Conflict (not start a second one)

### Error Isolation
- If one ticker fails during enrichment, skip it — never crash the whole scan
- Log all errors with ticker name for debugging
- Never expose internal error details to API responses in production

---

## VERSION CONTROL — Save Progress in GitHub

### Rule 19: Commit Alongside Development
- After completing each task or meaningful chunk of work, commit to GitHub
- Use clear commit messages: `feat:`, `fix:`, `refactor:`, `docs:` prefixes
- Never let more than 1 task's worth of work go uncommitted
- If GitHub is not set up, ask the user for keys/repo before starting development
- Push regularly — unsaved work is lost work

### Rule 20: Version Audit Before First Use
- Before using ANY library, run `library.__version__` and compare with requirements.txt
- If versions differ (especially major version), search for breaking changes FIRST
- Check deprecated parameters, changed defaults, removed features
- Update requirements.txt to match installed version BEFORE writing code
- NEVER assume the API is the same across major versions — it never is

### Rule 21: Blame Yourself First
- When an API returns errors, check YOUR code before blaming the API
- Verify: correct parameters, correct version, correct auth method
- If rate-limited, investigate whether YOUR calling pattern causes it (wrong session, wrong headers, deprecated params)
- NEVER tell the user "the API is blocking you" until you've ruled out every code-side cause
- "Wait 15 minutes" is not debugging — it's avoidance

### Rule 22: Prove It's Not You
- Before telling the user an API is blocking/rate-limiting them, you MUST provide evidence:
  1. Show `library.__version__` vs requirements.txt — confirm they match
  2. Show the exact call parameters and confirm they match the installed version's API docs
  3. Run a minimal 3-line test that isolates the issue
- If you cannot provide ALL THREE, say "I don't know the cause yet" — never blame the API
- If you suggest "wait 15 min", "use VPN", or "switch network" without completing steps 1-3, you are WRONG
- External blame is the LAST resort, not the first guess

---

## LESSONS LEARNED — Past Mistakes (Never Repeat)

### Mistake 1: Used scraper libraries without asking user
- Blindly ported yfinance + finvizfinance into Docker without questioning the choice
- **Rule**: Always ask user which data provider before implementing

### Mistake 2: Never tested library output before building pipeline
- finvizfinance 1.3.0 corrupts every ticker (doubles first letter: AAL → AAAL)
- Built entire pipeline on corrupted data, ran 3 full scans with garbage tickers
- **Rule**: Test any library with 3 lines of code FIRST, verify output before building

### Mistake 3: Ran full 650-ticker scan without testing 1 ticker
- Went from 0 → 650 tickers, burned Yahoo rate limit, got IP blocked
- **Rule**: Test 1 → 5 → full. Never skip steps.

### Mistake 4: Downloaded hourly data for ALL 650 tickers
- Should download daily first, filter to ~20-40 winners, then hourly only for winners
- **Rule**: Filter before downloading. Minimize API calls.

### Mistake 5: Used threads=True in yf.download() inside Docker
- 50 parallel connections per batch instantly triggers Yahoo rate limits
- **Rule**: Always use `threads=False` and pass `session=` in Docker

### Mistake 6: Retry-stormed when rate limited
- Kept running scans even after getting 429 errors, burning the IP further
- **Rule**: If rate-limited, STOP immediately. Wait 15-30 min. Don't retry.

### Mistake 7: Said "0 results is expected" instead of investigating
- 0 stocks found was clearly a bug (corrupted tickers), not expected behavior
- **Rule**: 0 results is ALWAYS a problem. Investigate immediately.

### Mistake 8: Blamed Yahoo rate-limiting instead of checking own code
- yfinance was pinned at 0.2.55 in requirements.txt but 1.5.1 was installed
- Custom `requests.Session` with `session=` parameter fights yfinance 1.x internal session/cookie handling
- Every "test" call was failing because of OUR session interference, not Yahoo blocking the IP
- Wasted 6+ hours telling user to "wait 15-30 min", "use VPN", "switch to hotspot"
- **Rule**: When rate-limited, check YOUR code first (versions, params, sessions) before blaming the API

### Mistake 9: Never verified installed library version matches requirements.txt
- requirements.txt said 0.2.55, `pip` had 1.5.1 — a MAJOR version jump with breaking changes
- Used deprecated `session=` parameter that actively caused rate limiting
- Used `Ticker.info` instead of `Ticker.fast_info` (unstable in 1.x)
- **Rule**: ALWAYS run `library.__version__` and compare with requirements.txt before first use

---

## SCANNER ARCHITECTURE — Source of Truth

### Original Working Code
- **File**: `/Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py` (523 lines)
- **This is the reference implementation** — always compare against it before changing scanner logic

### Original Pipeline Flow
1. **Step 1**: Finviz screens ~7,000 stocks → ~650 candidates (price, volume, cap filters)
2. **Step 2**: yf.download() bulk daily (1y) + hourly (3mo) for all candidates
3. **Step 3**: Apply filters (ATRP 2.5-6%, RVOL >1.0/1.2, 4H price>50EMA, 1H EMA20>EMA50, 52w 10-90%)
4. **Step 3.5**: (advanced) 5M tradability check (day direction, green/red ratio, big body, volume dist)
5. **Step 4**: Enrichment (yf.Ticker.info) + market cap gate ($500M) + float gate (20M-1B)
6. **Step 5**: Sort by quality (RVOL × ATRP, best first)
7. **Step 6**: Save results

### Data Sources (Development/Testing)
- **Finviz** (via finvizfinance): Stock discovery/screening only. No candle data.
- **Yahoo Finance** (via yfinance): All OHLCV data, enrichment (sector, float, news)
- **For production**: Upgrade to Polygon.io or FMP. These scrapers are for dev/testing only.

### Known Library Bugs
- **finvizfinance 1.3.0**: Doubles first character of every ticker. Must strip in code.
- **yfinance 1.x**: Do NOT pass `session=` to `yf.download()` or `yf.Ticker()` — yfinance manages its own sessions/cookies. Passing a custom session fights internal rate-limit handling and CAUSES blocks.
- **yfinance 1.x**: Use `Ticker.fast_info` for market cap (reliable). `Ticker.info` is unstable — wrap in try/except.
- **yfinance 1.x**: Single-ticker `yf.download()` returns flat columns (not MultiIndex). Must handle both formats in `extract_ticker_df()`.
- **yfinance batch size**: Max 10 tickers per `yf.download()` call, 5s delay between batches.

---

## PIPELINE OPTIMIZATION — Filter Before Download

### Current Problem
Downloading hourly data for ALL 650 tickers wastes ~95% of API calls.

### Optimized Flow (Implement This)
1. Download **daily only** for all 650 tickers (one bulk yf.download call)
2. Apply daily-only filters: ATRP, RVOL, 52-week position, IPO age
3. ~20-40 tickers pass → download **hourly only for these**
4. Apply hourly filters: 4H price > 50 EMA, 1H EMA20 > EMA50
5. Winners → enrichment (yf.Ticker.info with 2s delays)
6. Sort by RVOL × ATRP

### Why This Matters
- 650 hourly downloads × 20 tickers/batch = 33 batches × 3s delay = ~100 seconds + data
- 30 hourly downloads × 20 tickers/batch = 2 batches × 3s delay = ~6 seconds + data
- **~95% fewer API calls = ~95% less risk of rate limiting**
