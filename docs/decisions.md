# Decisions Log

Append-only. One entry per decision: date, decision, why, what it supersedes (if anything).
Do not edit or delete past entries — if a decision changes, add a new entry that says so.

---

## 2026-09-04 — Docs discipline adopted

**Decision:** Every feature works from `docs/plan-analyst-watcher.md`. Plan files are read-only once approved; changes go here instead. `docs/overview.md` tracks as-built architecture, `docs/progress.md` tracks part-by-part status.

**Why:** Plans were being hand-edited mid-implementation, making it unclear what was approved vs. changed after the fact. Keeping decisions in a separate append-only log preserves the history.

**Supersedes:** N/A — first entry.

---

## 2026-09-04 — `plan-analyst-watcher.md` supersedes `plan-x.md` for product direction

**Decision:** `docs/plan-analyst-watcher.md` is the active plan. `docs/plan-x.md` is kept as reference; its §5 (disposable Docker environments) is parked to Phase 9 of the new plan.

**Why:** Product direction shifted from the System E / Docker-first rebuild toward Analyst + Watcher + Journal (swing trading first). The old plan's findings (§1) are still accurate and are not being redone.

**Supersedes:** `docs/plan-x.md` (kept, not deleted, per docs-discipline rule).

---

## 2026-09-05 — Migrations must be idempotent; recording init-applied files is deferred

**Decision:** Every file in `infra/supabase/migrations/` must be safe to run twice (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, no plain `INSERT` seeds). Enforced by convention (rule in `CLAUDE.md`). The root-cause fix — an init script that records init-applied filenames in `public.schema_migrations` — is deferred until the first migration that cannot be made idempotent (e.g. seed data).

**Why:** A fresh `pgdata` volume applies migrations via Postgres init without recording them; `scripts/migrate.sh` then re-applies all of them on its first run. Phase 1's only migration is one table + index, so a convention covers it without new infra.

**Supersedes:** N/A — refines Part 0.2, does not change it.

---

## 2026-09-05 — Spec tables required for stateful parts

**Decision:** G1.5 added to `.agents/AGENTS.md`: parts that write state or call a dependency include a writes table and a failure-branch table in the spec, each branch naming its test function, and keyed values go through one named normalization function. Numbered G1.5, not G13, on purpose: it extends G1's spec format and is not a freestanding rule.

**Why:** The Part 1.2 review found a Redis-down path that failed open with no test, and a cooldown key dodgeable by ticker case. Both were visible at spec time once writes and failure branches were listed.

**Enforcement of the normalization rule:** the normalizer should be the only place `.upper()` appears in an endpoint file, so `grep -n "\.upper()" main.py` catches an inlined copy. Not automated yet.

**Supersedes:** N/A — extends G1.

---

## 2026-09-06 — Indicator package layout and calculation conventions (Part 1.5)

**Decision:**

- **Module assignment.** `indicators/technical.py` is split into the four modules the plan names and nothing else (no `price.py`): `moving_averages.py` (`ema`, `aggregate_4h` — resampling feeds only the 4H EMA), `volatility.py` (`calc_atr`, `calc_atrp`, `extension`, `gap`), `momentum.py` (`rsi`, `macd`, `relative_strength`, `check_52w_position`), `volume.py` (`calc_rvol`, `avg_dollar_volume`). The package `indicators/__init__.py` re-exports every public name and is the only import surface — callers write `from indicators import ...`, never the submodule path. `relative_strength` returns a percentage-point difference (stock % return − benchmark % return), matching plan §3 and the 6.5 wake trigger ("underperforming SPY by > 1.5%").
- **Empty-input guard on two moved functions.** `calc_atrp` and `check_52w_position` previously raised `IndexError` on an empty series (`.iloc[-1]`); they now return `NaN`. Rationale: Part 1.7's `GET /indicators/{ticker}` computes from stored bars, which can legitimately be empty, and a pure indicator must not raise on that. The scanner never hit the raise (it rejects `len < 120` first), so its numbers are unchanged. Explicit carve-out: `calc_rvol` keeps returning `0.0` on empty/short input — the scanner's RVOL floor comparison depends on it.
- **RSI and MACD conventions.** `rsi()` is Wilder RSI **seeded with a simple mean over the first `period` deltas**, then Wilder-smoothed (`(prev × (period−1) + current) / period`); the first `period` rows are `NaN`. This matches TA-Lib and TradingView, so users cross-checking against a broker chart see the same number — the alternative (`ewm(alpha=1/period, adjust=False)` from the first delta) converges but disagrees for weeks after a listing. `macd()` is `EMA(fast) − EMA(slow)` with `signal = EMA(macd)`, **deliberately without a warm-up mask**: it is a port of the frozen `scanner/` reference (`scan.py:91`, `step3_filters.py:25`) and must keep producing identical numbers. Do not "fix" MACD by masking its first rows — that silently breaks parity with the reference.

**Why:** The plan row for 1.5 names the four modules but does not assign functions to them, does not specify RSI seeding, and says nothing about empty input. All three are conventions the next parts (1.6, 1.7, 6.5) build on and would otherwise be re-litigated.

**Supersedes:** N/A — fills gaps in Part 1.5's plan row; no D-number changes.

---

## 2026-09-06 — Support/resistance zone conventions (Part 1.6)

**Decision:** `indicators/levels.py` implements the plan row's pipeline with these conventions, none of which the row specifies:

- **Fractals are strict.** Bar i is a swing high only if its high is strictly greater than the highs of the `wing=2` bars on each side (lows likewise). A bar tied with a neighbour is not a fractal; the first and last two bars never are. **NaN rule:** a bar with NaN high or low is never a fractal and disqualifies every bar whose window contains it. This is checked explicitly with `np.isnan`, not left to the fact that strict `>` against NaN is False.
- **Volume nodes bin by close.** The range `[min low, max high]` is split into `n_bins=50` equal bins; each bar's volume is added to the bin holding its close; the `top_nodes=5` bins by volume (ties → lower bin) become levels at the bin midpoint. Bars with NaN close or NaN volume are skipped. Zero total volume → no nodes (fails open on volume only; swings still count). A zero-width range → one node at that price.
- **Three methods:** `swing_high`, `swing_low`, `volume`. A flip level holding a swing high and a swing low counts as "2+ methods" without a volume node.
- **Merge anchor is the running mean.** Levels are visited in ascending price; a level joins the current group when it is within `merge_pct=0.5` percent (inclusive) of the group's running mean, else it starts a new group. Zone low/high are the min/max member prices, zone price the member mean. Running mean, not chain merging, so a run of levels each 0.49% apart cannot drift into one wide zone.
- **Scoring.** `+30` two or more methods, `+25` contains a volume node, `+20` "tested" = two or more swing members (volume nodes are not tests), `+15` "recent" = newest swing member within the last `recent_bars=20` bars of the full series (NaN tail included; a volume-only zone is never recent). Max 90. **Volume deliberately double-counts**: a zone with one swing and one volume node scores 30 + 25 = 55, so a lone volume node with one nearby swing outranks two clean swings (50). That is what the plan row implies and it is pinned by `test_score_zones_rubric[swing_plus_volume_double_counts_55]`; if it proves wrong in use, change it here with a superseding entry.
- **Split on last valid close.** Zone price below the close → support, otherwise → resistance, including a zone whose low < close < high (classified by its price, not its range) and a zone price exactly equal to the close. Each side is sorted by score descending, then by distance to the close ascending, capped at `top_n=3`.
- **Input validation.** Mismatched Series lengths raise `ValueError` (fails closed). No shared validation helper exists in the 1.5 modules — they use inline `len()` checks — so this is an inline check too. Empty input, or no non-NaN close, returns two empty lists without raising (fails open).
- **Shape.** A level is a plain tuple `(price, method, bar_index)`; zones are the frozen dataclass `Zone(low, high, price, score, methods, tests, recent)`. Six names exported from `indicators`: `Zone`, `fractal_swings`, `volume_nodes`, `merge_levels`, `score_zones`, `support_resistance` (`__all__` now 18). Part 1.7 serializes to camelCase; this module does not.

**Why:** Every bullet is a choice the next parts (1.7 endpoint, 4.3 plan math: stop = nearest support zone low − 1 ATR, targets = next resistance zones) build on and would otherwise be re-litigated. The plan-x §3a design had the same zones in `signal-engine`; D1/D17 moved them into `data-engine`'s indicator package and dropped pivots.

**Supersedes:** N/A — fills gaps in Part 1.6's plan row; no D-number changes.

---

## 2026-09-06 — Volume is not a zone "method" (Part 1.6 follow-up)

**Decision:** In `indicators/levels.py`, `methods` is the set of swing methods only: `{swing_high, swing_low}`. A volume node is reported on the zone as `Zone.volume_node: bool` and still scores `+25`, but it no longer counts toward the `+30` "two or more methods" bonus. Rubric cases pinned by `test_score_zones_rubric`: swing only 0, volume only 25, swing + volume 25, both swing methods (two tests) 50, both swing methods + volume + tested twice + recent 90. The same zone without the recency bonus is 75. Max stays 90.

**Why:** Under the previous rule a lone volume node with one nearby swing scored 55 and outranked two clean swings (50). The reviewer flagged that as a design smell at spec time; the user chose to fix it now rather than carry it into 1.7 and 4.3.

**Supersedes:** the "Three methods" and "Scoring" bullets of the 2026-09-06 "Support/resistance zone conventions (Part 1.6)" entry. The `Zone` dataclass gains a trailing `volume_node` field; `methods` never contains `"volume"`.

---

## 2026-09-06 — Part 0.7 added: `tf-data-engine-dev` service, pytest discovery lock, rule G13

**Decision:**

- **Part 0.7 exists.** The plan is read-only, so this entry adds it under Phase 0: a compose service `data-engine-dev` (container `tf-data-engine-dev`, profile `dev`, host port 8011) built from a new `dev` stage of the data-engine Dockerfile, so tests run with one `docker exec` while prod `tf-data-engine` keeps running. Recorded in `docs/progress.md` as row 0.7; there is no §17 checkbox for it.
- **Dockerfile has three stages** — `base` (system + runtime deps), `dev` (`FROM base` + `requirements-dev.txt`, deliberately no `COPY . .`: the image is inert without the source mount, which also supplies `tests/` since `.dockerignore` excludes it from every image), `prod` (`FROM base` + `COPY . .`, last stage so an untargeted build still yields prod). The `data-engine` service names `target: prod` explicitly.
- **The dev service is isolated by construction, not by convention.** Hard-coded in compose, not read from `.env`: `DATA_PROVIDER=fixture` (nothing on 8011 can reach Finviz or yfinance; the only Finviz caller is `YFinanceProvider.get_candidates`), database `tradingfirm_dev` (not `POSTGRES_DB`), Redis `redis://redis:6379/1` (not DB 0). It has **no `depends_on`** — tests mock Postgres and Redis, and the API tolerates both being absent — and joins the default compose network like every other service. Until `scripts/dev-db.sh` has been run the dev API starts with `db_connected=false` and every DB path fails closed (503 / persistence skipped), which is itself the guarantee that it cannot touch prod's tables.
- **`scripts/dev-db.sh`** creates `tradingfirm_dev` if absent (via `docker exec` into `tf-postgres`, same pattern as `migrate.sh`, name validated against `^[a-z_][a-z0-9_]*$`) and applies migrations to it through a new `MIGRATE_DB` override in `scripts/migrate.sh` (default unchanged: the container's `POSTGRES_DB`). Idempotent. Not run automatically by the container: the image has no `psql`, and service code should not create databases.
- **pytest discovery is locked** by `services/data-engine/pytest.ini`: `testpaths = tests`, `python_files = test_*.py`. A bare `pytest` can no longer collect `tests/full_scan_test.py` (which matched the default `*_test.py` pattern and fired a live scan on import). Naming the test files stays as habit; it is no longer the only guard.
- **Rule G13 — Verify Edits Landed** added to `.agents/AGENTS.md` (numbered G13, not G6: G6 is Protect External APIs): absolute paths for every edit, prove scripted edits applied before the next step, end every completion report with `git diff --stat <base>..HEAD`.

**Why:** Parts 1.5 and 1.6 each displaced the prod container to run tests (the `docker-compose.dev.yml` overlay redefines the same service). The first draft of this part's G1.5 writes table said "no state written" and missed that a dev API carrying prod's `DATABASE_URL`/`REDIS_URL` could upsert fixture bars into prod's tables and write prod's cooldown keys — exactly the gap G1.5 exists to catch; the separate database and Redis index close it. G13 exists because 1.6 and its follow-up each had a scripted edit pass silently not apply (a missing `python` alias, then a persisted `cd`), caught only by re-checking.

**Supersedes:** N/A — adds Part 0.7 and rule G13; no D-number changes. `docker-compose.dev.yml` is untouched and remains the full-stack hot-reload path.

---

## 2026-09-06 — Indicators endpoint conventions (Part 1.7)

**Decision:**

- **Field → function.** `ema20/50/200` = `ema` last value; `atr14` = `calc_atr`; `rsi14` = `rsi`; `macd/macdSignal/macdHist` = `macd` last row; `ext20/ext50` = `extension` against EMA 20/50 and ATR 14; `gapPct` = `gap` last value, `gaps20` = its last 20 values; `rvol` = `calc_rvol(volume, last volume, 20, scale 1.0)`; `avgDollarVolume20` = `avg_dollar_volume`; `rsSpy5/20`, `rsSector5/20` = `relative_strength` with period 5 / 20 (stock % return − benchmark % return over the window, in percentage points, aligned on bar timestamp). All over the full stored daily history.
- **52-week window.** `pos52w` and `zones` are computed on the last 252 stored bars only; everything else uses the full history (EMA 200 and MACD need the warm-up). New arithmetic beyond the 1.5/1.6 functions is exactly this window plus the gap-history slice, both hand-tested in `test_indicators.py`.
- **Null and 0.0.** Every NaN serializes as JSON `null`; no minimum bar count. `rvol` is `0.0` on fewer than 21 bars (1.5 convention, do not "fix"); its scale factor is 1.0 because stored bars carry no minutes-since-open.
- **Benchmarks come from the bar store, never the provider.** SPY and the sector ETF (`data_engine.stocks.sector` → `indicators/sectors.py`, 11 yfinance names → SPDR ETFs, case/whitespace-insensitive, unknown → none) are read with `get_bars`; a missing one nulls its RS fields and reports `bars: 0` under `benchmarks`.
- **Cache.** Key `tf:cache:indicators:{normalized ticker}`, TTL 900 s, body = the camelCase response without `cached`; `cached` is set on retrieval. Unparseable or schema-mismatched bodies are a miss (recompute, overwrite). Refresh deletes the key after a successful upsert.
- **Failure policy.** Any DB read raising (bars, benchmark bars, stocks row) is 503; no daily bars is 404; Redis absent or failing at GET or SET is a computed 200 with `cached: false`.

**Why:** the plan row names the endpoint and the cache TTL only; every bullet is a choice 4.3 plan math and the analyst prompt will read.

**Supersedes:** N/A.

---

## 2026-09-09 — `normalize_ticker` moves to `tickers.py` (before Part 2.1)

**Decision:** the one ticker normalizer lives in `services/data-engine/tickers.py`; `main.py` (four callers: `/stocks/{t}`, `/stock/{t}/refresh`, `/stock/{t}/bars`, `/indicators/{t}`) and `scanners/market_scanner.py` (the fifth caller, where a ticker enters `daily_winners`; its deferred `from main import` is gone) import it from there. Body unchanged: upper-case, strip. Part 2.1's context fetchers import it too. The G1.5 grep becomes: `.upper()` appears in `tickers.py` only.

**Why:** library modules (fetchers, cache helpers) cannot import the FastAPI entrypoint without a cycle, and G1.5 wants every key to pass through the same function rather than a copy.

**Supersedes:** the "normalizer is the only place `.upper()` appears in an endpoint file" enforcement note in the 2026-09-05 spec-tables entry; the rule is the same, the file moved.

---

## 2026-09-09 — Finnhub context fetchers (Part 2.1)

**Decision:**

- **Free-tier scope verified live (plan §18):** `/stock/profile2`, `/company-news`, `/stock/recommendation`, `/calendar/earnings`, `/stock/earnings` all answer 200 with the free key. But `/calendar/earnings` returned only the *upcoming* report for a two-year `from`; past report dates are not available from it on this tier. `/stock/earnings` gives the last four surprises keyed by fiscal period end, not report date. Part 2.3 must take report dates from elsewhere (yfinance `earnings_dates`, plan fallback) or from the daily-bar gap around the period end.
- **Events shape.** `('earnings', report date)` with `meta.calendar` from the calendar; `('earnings_surprise', period end)` with `meta.surprise` from surprises. Two rows, not one: the dates differ. `meta` is merged on conflict (`existing || new`), each writer owns a nested key, so neither order clobbers the other. Same-kind reruns replace their own key (a calendar rerun updates `epsActual` once reported).
- **`news_items.ticker` is NOT NULL.** A nullable column inside a UNIQUE constraint does not dedup in Postgres (NULLs are distinct). General-market news is stored under the sentinel `_MARKET` (`db.MARKET_TICKER`). Verified live: two inserts of the same `_MARKET` URL leave one row.
- **Writes are not transactional.** `upsert_news` / `upsert_events` use one `executemany` without an explicit transaction: partial rows are possible on a mid-batch raise, same deferred defect as `upsert_bars`; a rerun dedups. In-batch duplicates are collapsed first so `DO UPDATE` never touches a row twice.
- **Fetchers return raw Finnhub bodies**; conversion to rows is separate and pure. Cache goes through `cache.py` (`finnhub_key`, generic `get_cached_json` / `set_cached_json`). Tickers pass through `tickers.normalize_ticker` and must be 1–5 letters; anything else raises `ValueError` before HTTP.
- **Limiter** is an in-process sliding window (60 per 60 s) plus a 1.2 s minimum gap, with injectable clock and sleep so tests use a fake clock. A 429 raises and nothing retries.

**Why:** the plan row names the endpoints and tables but not the free-tier limits, the date mismatch between the two earnings endpoints, or the dedup trap; all three would otherwise surface as silent bugs in 2.3 and 2.4.

**Supersedes:** N/A — the plan's `news_items` column list said `ticker NULL for market news`; the sentinel replaces the NULL for the reason above.

---

## 2026-09-09 — G1 reworded: spec file before approval, approval is a word in chat

**Decision:** G1 in `.agents/AGENTS.md` now says: the spec goes to `docs/specs/<part>.md` (3 sentences, G1.5 tables, the decisions the plan row leaves open) and is the only file written before approval; no other edit until "approved" appears in chat for that part; a complete plan row is not a substitute; "do part X, stop when done" means post the spec and stop; the spec file is committed with the feat commit, corrected to what was approved.

**Why:** Part 2.2 was built without a posted spec on the strength of "Do step 2.2 only", and its completion report listed five choices the plan row never covered. Review then changed four of them and the table shape. A spec file also survives a cleared chat.

**Supersedes:** the G1 wording of 2026-09-04 (first entry); G1.5 unchanged.

---

## 2026-09-09 — SEC EDGAR filings fetcher (Part 2.2)

**Decision:** the approved spec is `docs/specs/2.2.md`; the choices the plan row left open, as approved after review:

- **Two modules** (`edgar_client.py`, `edgar.py`) like 2.1, over the shared `ratelimit.RateLimiter`, `cache.cached_json()` and `tickers.validate_ticker()` (refactor commit before this part).
- **Limiter:** one mechanism, a rolling window of 10 per second, no gap, one module-level instance `ratelimit.edgar_limiter` shared by every client in the process; one uvicorn worker assumed.
- **Verified live (plan §18):** two calls for AAPL with the declared User-Agent answered 200. `company_tickers.json` has 10,407 entries; the submissions `filings.recent` block is 16 parallel arrays, up to 1,000 rows, newest first. `acceptanceDateTime` is genuine UTC (a Form 4 accepted 18:30 ET shows `22:30:44.000Z`).
- **Dates:** `filed_on DATE NOT NULL` is the official `filingDate` — what `days` filters on and what 2.3/2.4 join daily bars on. `accepted_at TIMESTAMPTZ` is `acceptanceDateTime`, NULL when EDGAR gives none, never fabricated.
- **Blocked means stop.** SEC answers 403 for an undeclared client and for "Request Rate Threshold Exceeded"; 403 and 429 both raise `EdgarRateLimited`, nothing retries.
- **Cache shape.** Whole ticker → CIK map under one key, 24 h; an empty map raises and is never cached. Per ticker, the parsed rows of `recent` with `filingDate` inside `today − 90` (the `days` maximum; bounded by date, not count), every form, plus the block's oldest `filingDate`, for 15 min. `forms` / `days` (1..90) filter in-process. A cached body of the wrong shape is a miss (`valid` predicate on `cached_json`).
- **Truncation** is real only when the block's oldest row, before form filtering, is newer than `today − days`: `recent_filings` returns `(rows, truncated)`, warns then and never otherwise; 2.4 surfaces the flag in `dataQuality`.
- **Unequal `recent` columns fail closed** (`EdgarError`, nothing cached): a short middle array would pair every later accession with the wrong form. Absent columns read as all-None; missing values inside an aligned row are dropped and logged.
- **Nothing at the SEC fails open:** not in the map, or CIK known but submissions 404 → `([], False)` at warning, so 2.4 keeps the section. The client still raises `EdgarNotFound`; the fetcher catches it.
- **Table.** `data_engine.filings` PK `(ticker, accession)` (the same accession appears under GOOG and GOOGL); `ON CONFLICT DO NOTHING` because a filed accession never changes — a future derivation change is a one-off backfill with its own entry. Amendments fold into the base form for matching (`8-K/A` matches `'8-K'`); the row keeps the literal form.

**Why:** the plan row names the endpoints, the User-Agent rule and the 10 req/s cap only; every bullet is a choice 2.3, 2.4 and Phase 6 (an 8-K is a wake trigger) build on.

**Supersedes:** N/A.

---

## 2026-09-09 — Deferred from Part 2.2

- **Class shares unreachable through both context fetchers.** `validate_ticker` is letters-only, so `BRK-B` (SEC) and `BRK.B` (Finnhub) both raise before HTTP, although `BRK-B` is a key in the CIK map. Fix, as its own future `refactor:`: one canonical form in `validate_ticker`, hyphen/dot mapped per provider at the call site. Not inside 2.2.
- **2.1 Finnhub fetchers coerce a wrong-shaped cached body** to `[]` / `{}` instead of refetching; the `valid` predicate on `cached_json` is the same fix. Not touched in 2.2.
- **Finnhub limiter is per-client** (`FinnhubClient.__init__` builds one when none is injected), unlike the module-level EDGAR limiter. Not touched in 2.2.
- **One uvicorn worker assumed** for the module-level EDGAR limiter; the Dockerfile CMD has no `--workers`. If that changes, the limiter must move out of process (Redis).

---

## 2026-09-09 — Earnings report dates: yfinance primary, Alpha Vantage fallback (Part 2.3, commit 1)

**Decision:**

- **Two sources, one direction.** yfinance `Ticker.get_earnings_dates(limit=12)` through `DataProvider.get_earnings_dates()` is primary; Alpha Vantage `EARNINGS` is the fallback, called only when the primary yields no usable *past* date. The fallback is never called on a rate limit (the source refused, which says nothing about its data) and never when the bar store is empty (validation is then impossible, so the call is wasted). `providers/base.ProviderRateLimited` is what carries that difference; `yfinance.exceptions.YFRateLimitError` maps onto it (confirmed present on the pinned 1.5.1).
- **Live findings (plan §18, four calls).** yfinance 1.5.1 columns are `EPS Estimate`, `Reported EPS`, `Surprise(%)`; the index is tz-aware `America/New_York`, so the `amc`/`bmo`/`dmh` hour is derived, not guessed. `limit=12` actually returned **25 rows** for AAPL and MSFT, spanning six years. SPY (an ETF) returns `None` — recorded as the literal `null` in its fixture, which is a different fact from a missing file. Alpha Vantage `quarterlyEarnings` items carry `fiscalDateEnding`, `reportedDate`, `reportedEPS`, `estimatedEPS`, `surprise`, `surprisePercentage`, `reportTime` — every value a **string**, with `"None"` as the null sentinel, and `reportTime` is present (`post-market` / `pre-market`).
- **Validation reads the store, not the refresh frame.** A past report date must be a stored daily bar date or within one calendar day of one. Refresh downloads two years while the feed reaches six back, so validating against the frame would drop older reports the store can still explain. One `db.get_bars(pool, ticker, "1d")` with no `since` at the top of the sync.
- **"Out of range" is not "dropped".** Rows older than the stored bar history are counted separately and reported only in the log. Against a two-year store the live AAPL feed puts 20 of 25 rows there; folding them into `dropped` would make the dossier's `dataQuality` alarming and meaningless. `dropped` counts only dates inside the stored window that are not a trading day or adjacent to one.
- **One response shape.** `POST /stock/{ticker}/refresh` gains `earningsDates: {source, stored, dropped, reason}`, never null, `reason` ∈ `null` / `rate_limited` / `down` / `no_bars` / `error`, so 2.4 handles one object. A failure in the step never fails a refresh whose bars were stored.
- **`meta.earnings` is the one nested key** this part writes (`source`, `validated`, `hour`, `epsEstimate`, `epsReported`, `surprisePct`), so 2.1's `meta.calendar` survives the `existing || new` merge. All three writers (Finnhub calendar, yfinance, Alpha Vantage) build `event_at` as midnight UTC of the Eastern calendar date and therefore collide on the PK by design.
- **Alpha Vantage key in the query string** is an approved, narrow exception to G14, held by two conditions in the client module: the `httpx` logger pinned to WARNING, and typed errors raised `from None` with messages built from `function` + `symbol` only. Free tier 5/min (in-process limiter) and 25/day, the daily cap arriving as HTTP 200 with an `Information` body rather than a 429.

**Why:** the plan row for 2.3 assumes stored earnings dates exist; they do not, because the Finnhub free calendar returns only the upcoming report (2026-09-09 entry above). Everything here is a choice that entry left to this part, plus three facts only a live call could settle (the real column names, the 25-row `limit`, and the string-typed Alpha Vantage payload).

**Supersedes:** N/A. Extends the 2026-09-09 Finnhub entry, which named Part 2.3 as the place the past-report-date gap gets closed.

---

## 2026-09-09 — Earnings reactions: volume decides, never the bigger move (Part 2.3, commit 2)

**Decision:**

- **One rule for both ambiguities.** An unknown report hour and a cross-source date conflict are both resolved by `calc_rvol >= 2` on the candidate session. "Whichever session moved more" was rejected: it selects the bigger move by construction and would bias every statistic built on this history. When volume cannot separate the candidates the report is dropped and counted, never guessed.
- **Date reconciliation has three cases.** Same source under 20 days apart: one report, keep the earlier (quarters are ≥ 60 days apart), nothing counted. Cross-source ≤ 1 day apart: one report off by a day, collapse to the yfinance row, not a disagreement. Cross-source 1–20 days apart: a real conflict, counted in `dataQuality.disagreements`, resolved by volume or both dropped.
- **`reactions: null` ≠ `[]`.** Null means no confirmed report exists at all (never refreshed, or both sources down); `[]` means reports exist but no bars explain them. 2.4 shows "no data" for one and "no reaction" for the other, and never 500s on either.
- **Only past rows count against quality.** A future report is unvalidated by construction, so it is skipped silently; counting it would show a permanent `dropped: 1` on every healthy ticker. The hour also falls back to `meta.calendar.hour` so Part 2.1 rows, which have no `meta.earnings`, still produce reactions.
- **`db.get_events` is generic** (`event_type` / `since` / `until`, all optional, `meta` decoded with a malformed row kept as `{}`) because 2.4's dossier and 6.4's "earnings within 24 h" read the same table.

**Why:** the plan row says "join stored earnings dates with stored daily bars" and leaves every tie-break open; each one above is a place where a plausible shortcut would have quietly biased the journal that Phase 4 scores verdicts against.

**Supersedes:** N/A.

---

## 2026-09-09 — `dropped` means one thing on both sides (Part 2.3, follow-up)

**Decision:** a report older than the first stored bar is logged, skipped and **not counted** at read time, matching `out_of_range` on the write side. `dropped` means "the source gave us something we could not use"; a short bar history is our limit, not a source-quality problem. No fourth `dataQuality` key — the shape stays `{source, dropped, disagreements}` and `out_of_range` reaches no response.

**Why:** commit 2 shipped the two sides disagreeing — uncounted when storing, counted when reading — so the same report could inflate `dataQuality.dropped` for 2.4 purely because bars were trimmed.

**Supersedes:** the read-side half of the 2026-09-09 commit-1 entry's out-of-range rule, which described the write side only.


---

## 2026-09-09 — Dossier: sections degrade, the database does not (Part 2.4)

**Decision:**

- **Every section is an object with a `status`** (`ok` / `truncated` / `error` / `unconfigured`) and, when it fails, still carries its own payload key empty — a consumer never branches on a missing key. `unconfigured` (empty key or User-Agent) is a different fact from `error` and is the dev twin's normal state.
- **Upstream failures degrade, database failures do not.** `db.DB_ERRORS` is re-raised through the section boundary and answered as 503 for the whole document: half a dossier that silently drops what Postgres holds is worse than an error. There is no 502 on this path.
- **`DB_ERRORS` is `(PostgresError, InterfaceError, ConnectionError)`, not `OSError`.** `asyncio.TimeoutError` *is* the builtin `TimeoutError`, an `OSError` subclass, so an `OSError`-based tuple reported every timed-out section as a dead database. The three HTTP clients now map `OSError` to their typed errors, so no upstream socket error reaches the tuple either.
- **Stale is a weekday rule and has four outcomes.** More than one weekday behind the reference session (today if it is a weekday past 16:00 ET, else the previous weekday) triggers one refresh. Refreshed and current → `ok`; refreshed with nothing newer (holiday week, lagging provider) → `stale` + `refreshed: true`; refresh refused, failed or on cooldown → `stale` + `refreshed: false`. Holidays are deferred to Phase 3's calendar, hence the field name `staleWeekdays`.
- **Each section owns its calls and its write.** `sync_context` is not on the dossier path: it writes only after all three fetches succeed, so one failure would drop the news rows too. News fetches and upserts news; events fetches calendar + surprises, upserts, and reads the section back from `data_engine.events`, which is what folds 2.3's `meta.earnings` rows and 2.1's projections into one list. A failed fetch is forgiven only when the store **can answer**, defined precisely as: the `get_events` read completed *and* returned at least one row. A read that completed and returned nothing re-raises the fetch error, so the section reports the source (`error`, or `unconfigured` for an empty key) instead of an empty `ok` that claims we looked. A read that *raises* is a 503, never a section. News has no read-back and needs none: a failed news fetch is always the section's status.
- **Call budget, stated and asserted.** 5 Finnhub + 2 EDGAR per cold dossier, 2 warm, 0 cached, 11 with a stale-bar refresh. Against the 60/min limiter that is 12 cold dossiers per minute; the binding constraint is Alpha Vantage's 25/day, reached only through refreshes. `test_budget_counts_upstream_calls` asserts those numbers from the respx call log.
- **Client `OSError` is mapped at the client.** `finnhub_client`, `edgar_client` and `alphavantage_client` catch `(httpx.HTTPError, OSError)` so a bare socket error is already a typed upstream error by the time the dossier sees it, and cannot be mistaken for a database failure. Their 2.1/2.2/2.3 test files are unchanged and green.
- **Cooldowns are source-wide, in `cache.py`.** Finnhub 429 → 60 s, EDGAR 403/429 → 15 min, Alpha Vantage cap → 1 h, checked before any HTTP. Alpha Vantage is not a section, so its cooldown acts inside `sync_earnings_dates`: the fallback call is skipped and the refresh reports `earningsDates.reason: "cooldown"` — a *skipped* call, never a refused one, which stays `down`.
- **`horizon` selects a row of `HORIZON_PROFILES`** (news days, filing days and forms, reaction limit, events window, bar interval) and rides in the cache key. Phase 6's intraday mode is a second row, not a branch.

**Why:** the plan row names the sections, the caps and the TTLs and leaves every failure question open; Phase 4 reads this document to build a verdict, so "which source was missing and why" has to survive into the JSON rather than being flattened into an empty list.

**Supersedes:** N/A.

---

## 2026-09-09 — The dossier budget is counted once per source, and is not part of the document (Part 2.4, after 2.5)

**Decision:**

- **One `calls_made` delta per source per dossier**, marked before the bars step and collected after the fan-out. Per-section deltas were wrong by construction: sections run concurrently and share one client, so each section's `after` read included the calls the others made in between. The 2.5 live check on prod reported **14 upstream calls for 10 actually made** (`finnhub: 12` for 5 calls). `test_budget_counts_upstream_calls` now makes every mocked route slow enough to overlap, and fails on the old code.
- **The refresh helper reports its own spend.** `refresh_ticker_bars` returns `(response body, {source: calls})`, so yfinance and Alpha Vantage appear in `bySource` — nothing else knows what the refresh cost. `POST /stock/{ticker}/refresh` keeps exactly Part 1.2's response shape; the counts are the second half of the tuple, not a new field.
- **Known limit, accepted:** `calls_made` lives on the client object and the app holds one client per process, so two dossiers assembled at the same instant cross-count each other. Fine while one caller uses the endpoint; the fix, if the scanner ever fans out over `/dossier`, is a `contextvars` counter inside the clients (deferred, `docs/progress.md`).
- **`budget` is not part of the cached document.** It describes the retrieval, like `cached`, so it is excluded from the stored body and set on the way out: a hit reports `upstreamCalls: 0`, `bySource: {}`, `elapsedMs` = the read time. Before the fix a cached dossier replayed the build's budget and claimed 14 upstream calls on a request that made none.

**Why:** the budget exists so a caller can reason about the Finnhub 60/min limiter and the Alpha Vantage 25/day cap before pointing the scanner at this endpoint. A number that is 40% high, and that a cache hit repeats as though the calls happened again, is worse than no number.

**Supersedes:** the counting half of the 2026-09-09 "Dossier: sections degrade" entry ("`test_budget_counts_upstream_calls` asserts those numbers from the respx call log" — it did, but only because the mocked calls never overlapped).
