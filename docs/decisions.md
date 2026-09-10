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

---

## 2026-09-09 — risk-shield skeleton: 005, a bounded startup, and a dev twin (Part 3.1)

**Decision:**

- **The risk migration is `005_risk.sql`, not the plan's `004_risk.sql`.** `004_filings.sql` shipped with Part 2.2 and `scripts/migrate.sh` keys `public.schema_migrations` by filename. `risk.macro_briefs` carries no `user_id` (D18 covers positions, verdicts and alerts; a macro brief is one shared market view) and repeats `risk.health_checks`' regime CHECK so the two tables speak one vocabulary. The file creates its own schema, so its rerun guarantee does not depend on `001`.
- **Startup is fail-open *and bounded*, which data-engine's is not.** `asyncpg.create_pool()` defaults to a 60 s connect timeout (`command_timeout=30` bounds queries, not connecting) and redis-py's `socket_connect_timeout` defaults to `None`. A Postgres that is restarting rather than refusing would therefore stall boot for a minute with `/health` unreachable — the opposite of what fail-open promises. Each dependency now gets `config.STARTUP_TIMEOUT` (5 s) inside one `asyncio.wait_for` covering the factory *and* its verification call, worst-case boot ~10 s, inside the healthcheck's start-period. **Data-engine keeps its unbounded startup**; fixing it is a separate `refactor:`, not smuggled into a Phase 3 feature part.
- **`config.STARTUP_TIMEOUT` is read at call time, never `from config import`.** A from-import copies the value, so the lifespan tests' monkeypatch would not land and each slow-path test would sit for the full 5 s. Later parts that bound a wait (3.4's scheduler) read it the same way.
- **`/health`'s `db_connected` / `redis_connected` are boot state, not a live probe** — the same contract data-engine has. A dependency that dies after boot reads `true` until a restart. Written down as a known limitation of both services rather than implied; a probing `/health` needs reconnect logic and is its own part.
- **`FRED_API_KEY` is a `SecretStr`** (G14 by construction, not by discipline): `repr`/`str`/`model_dump` mask it and only `.get_secret_value()` yields it, so `/health` can report `fredConfigured` without a path that could ever print the key.
- **Keys live under `tf:risk:`, never `tf:cache:`** (data-engine owns that prefix and both share Redis DB 0), through one builder `risk_key(kind, name)` over one normalizer `canonical()`.
- **A dev twin now** (`risk-shield-dev`, 8013, `tradingfirm_dev`, Redis DB 1, empty FRED key), the Part 0.7 shape, because the plan row names a test file and Phase 3 had nowhere to run pytest. Pytest pins match data-engine's exactly so the two twins cannot drift.

**Why:** the plan row says "mirroring data-engine's" and leaves every failure question open. Mirroring the startup verbatim would have copied a real defect into a service whose job is to notice when the market breaks.

**Supersedes:** the plan's Part 3.1 row on the migration number only (`004_risk.sql` → `005_risk.sql`).

---

## 2026-09-09 — `docs/progress.md` is the done-status source of truth; the plan's §12 checkboxes are not maintained

**Decision:** Part status lives in `docs/progress.md`, one row per part. The checkbox grid in `docs/plan-analyst-watcher.md` §12 is not kept up to date and is not authoritative — at the time of writing it shows Phase 0 and parts 2.3–2.5 unticked although all are done. It stays as written (plan files are read-only); nobody should tick it or read it.

**Why:** two trackers means neither is trusted. §12 asks for an edit to a read-only file, so it loses to the docs-discipline rule every time; `progress.md` carries the commit, the date and the caveats anyway.

**Supersedes:** the plan's §1 instruction "tick the box in §12".

---

## 2026-09-10 — Verify a prod Dockerfile stage under a separate tag

**Decision:** To check that a prod stage still builds, use `docker build --target prod -t tradingfirm-<service>:verify services/<service>` — never `docker compose build <service>` outside a G15 go. The image tag compose tracks only moves on the approved `docker compose up -d --build <service>`.

**Why:** in Part 3.1 a verification `docker compose build risk-shield` retagged `tradingfirm-risk-shield:latest`, so any later plain `up -d` would deploy it. Building the compose-tracked tag is half a deploy. (The 3.1 recreate itself was the operator's own `up`, not this — the rule is hygiene regardless.)

**Supersedes:** N/A.

---

## 2026-09-10 — Part 3.1 → 3.2 carry-forward

**Decision:** facts from 3.1 that 3.2 (core quotes + FRED) must build on, recorded here because none are in the spec or the plan row:

- **A fetcher must never return `None` into `cached_json`.** A cached JSON `null` reads back as `None`, which `cached_json` treats as a miss — so a quotes fetch that returns `None` (empty yfinance download) is re-fetched on *every* call, and the 5-minute cache silently stops protecting yfinance (G6). Raise a typed error, or return an empty `{}` / `[]` (empty is cached).
- **No cooldown helpers exist in risk-shield.** data-engine's `MemoryCooldowns` / `cooldown_remaining` / `start_cooldown` were not copied into `cache.py`; a yfinance or FRED refusal has nowhere to record a cooldown until 3.2 copies them (under `tf:risk:`) or specs its own.
- **No yfinance, pandas, numpy or provider layer.** risk-shield cannot import data-engine's `providers` (no shared package). Add them at data-engine's pins — `yfinance==1.5.1`, `pandas==2.3.0`, `numpy==2.3.0` — so `YFRateLimitError` and `read_json` behave identically, copy the `ProviderRateLimited` pattern, and add `respx==0.22.0` to `requirements-dev.txt`. Rebuild the twin with `docker compose --profile dev up -d --build risk-shield-dev` after the change.
- **The FRED live canary cannot run in the dev twin** — it hard-codes `FRED_API_KEY=""` by design. Run it as data-engine's 2.3 canary ran: a throwaway prod-image container with the source mounted, `docker compose run --rm --no-deps -v ./services/risk-shield:/app risk-shield python tests/<name>_live.py`. That container carries prod's `DATABASE_URL` / `REDIS_URL`, so a canary script must write neither.
- **`tests/` is a package** (`tests/__init__.py`): shared helpers import as `from tests.fake_redis import FakeRedis`, not `from fake_redis import`.

**Why:** 3.2 opens in a fresh chat that reads these docs, not the 3.1 conversation. The first two are G6 traps that pass every mocked test.

**Supersedes:** N/A.

---

## 2026-09-10 — Core quotes + FRED (Part 3.2)

**Decision:** approved spec `docs/specs/3.2.md`. The choices later parts build on:

- **FRED key in the query string** is the second approved exception to "secrets never in URLs" (FRED has no header form). It rests on the Alpha Vantage conditions, both held in `monitors/fred_client.py`: the `httpx` logger is pinned to WARNING, and every typed error is raised `from None` with a series id + status message. The body's `error_message` is inspected, never echoed.
- **Refusal raises, an answer is cached.**
  - A 429/423, a bad key, or a yfinance rate limit starts a source-wide cooldown and caches nothing.
  - A body with `reason` not null (`empty`, `partial`) caches for 120 s via the restored `ttl_for` hook, never the full 5 min / 6 h.
  - `cached_json` raises on a `None` from `fetch()`.
- **yfinance 1.5.1 swallows `YFRateLimitError`** into a per-download dict and only logs it. A refusal is therefore detected by a handler on the `yfinance` logger (both the download-summary and tz-fetch formats), behind an exact `1.5.1` version guard. A download where all 17 tickers are empty is treated as a silent block and starts the cooldown.
- **Quotes worst case.** Each ticker's `history()` first fetches its timezone (hard-coded 10 s) unless yfinance's on-disk cache has it, so a download is 34 requests cold and 17 warm. The per-request timeout is 5 s, giving 255 s cold and 85 s warm. There is no outer `wait_for`, because a thread can't be cancelled and the single-flight lock must be held until it returns. That lock is one per running event loop.
- **FRED bounds.** 8 s `httpx` timeout plus an `asyncio.wait_for` hard bound; `fred_limiter` at 60/min with a 1 s gap. `fred_snapshot` stops on `FredSourceWide` (cooldown, rate limited, not authorized, unconfigured) and continues past a per-series `FredError`, so one broken series can't blank the rest. A full outage costs ~72 s per walk.
- **Memory fallback:** without Redis, the in-memory clock can't tell a 429 from a bad key, so it remembers either FRED refusal for 900 s; the Redis path keeps 900 / 3600.
- **Live (9 FRED + 36 yfinance requests, all clean):**
  - FRED's missing marker is `"."`, and series lag by their release schedules (on 2026-09-10: `DGS10` 09-08, `DCOILWTICO` 09-01, `CPIAUCSL` 07-01).
  - yfinance returns MultiIndex `(Ticker, Price)` even for one ticker, in set order rather than request order.
  - Per-ticker dates differ: ETFs end on the prior session with 251 rows; `^VIX` and the futures include today's intraday bar (254 / 252 rows).
  - `^VIX` volume is 0.
  - All 17 tickers, cold, took 4.46 s.

**Why:** the plan row names two modules and two TTLs. Every item above is a place where a plausible default (cache an empty answer for 6 h, trust `download` to raise, stop a walk on any error, wrap a thread in `wait_for`) would quietly break G6 or blank the regime inputs.

**Supersedes:** the plan §2 call-budget line "~80" for the 5-minute regime check counts downloads. In requests it is ~1,360/day warm (17 per download), plus 17 per container start.

---

## 2026-09-10 — Part 3.2 → 3.3 / 3.4 carry-forward

**To 3.3:**
- **3.3's spec opens with this: per-ticker dates do not line up.** The live 3.2 canary (2026-09-10) showed one download returning different date arrays per ticker: the ETFs end on the prior session (251 rows), while `^VIX` (254) and the futures (252) carry today's intraday bar. Any monitor that pairs tickers by position (RSP/SPY ratio, sector vs SPY, cross-asset) computes on mismatched days without failing. Align on `date`, and treat a same-day `^VIX` / futures bar as intraday and partial.
- **`QuotesCoolingDown` / `FredCoolingDown` is stale, not an error.** Answer it with `stale: true` and the last known body. The fetchers keep no copy once a 120 s degraded body expires, so a last-known body must live somewhere. Two options; 3.3's spec decides:
  - **(a) per monitor:** each of the six monitors keeps its own last-known copy.
  - **(b) in the fetcher:** on every *full* answer (`reason: null`), the fetcher also writes a long-lived key, e.g. `tf:risk:cache:quotes:last` and `tf:risk:cache:fred:{SERIES}:last` at 24 h, and serves it with `stale: true` on `…CoolingDown`, a refusal or a degraded body. Stale is then decided in one place, not six.
- **`^VIX` volume is always 0**, so no volume monitor may read it.
- **Judge FRED freshness per series cadence** (daily, weekly, monthly), not against today.

**To 3.4:**
- A scheduler tick that finds the quotes lock held skips rather than queues. Cadence stays ≥ 255 s (quotes cold worst case) and ≥ ~72 s (FRED outage walk).
- `app.state.cooldowns = MemoryCooldowns()` is wired with the first caller.
- **yfinance's timezone cache lives in the container filesystem,** so every prod recreate costs 17 extra requests. Decide between a named volume for the cache dir and explicitly accepting the cost.
- **G15:** `docker compose up -d --build risk-shield` puts the 3.2 pins in the prod image. Nothing in prod calls the new modules until then.

**Supersedes:** N/A.

---

## 2026-09-10 — Commit sizes are checked before the first push; estimate overruns are reported

**Decision:**

- **Before a part's first push**, run `git show --numstat` on every unpushed commit. A commit over the 600-line split threshold (code + tests, spec 3.1 decision 12) is split locally before anything is pushed: `git reset --soft <part base>` and re-commit in smaller staged sets, because interactive rebase isn't available in the tool. If a split isn't sensible, stop and ask before pushing.
- **When code or tests come in more than ~50% over the spec's estimate**, the completion report says so as its own line item, with both numbers.
- **The append-only scope of this file:** an entry written during the current part may be edited in place until that part closes. Entries from earlier parts stay append-only.

**Why:** Part 3.2's commit 2 (`5a5b307`) carried 980 lines of code + tests, well over the threshold. That surfaced only in the report after the commit was on `origin/main`, where it stays. A split is free locally and impossible after a push. The part came in at 841 code / 1,049 tests against an estimate of 520 / 700, and the estimate is what the split plan is approved against.

**Supersedes:** the 2026-09-04 "Do not edit or delete past entries" line, for entries from the current part only.

---

## 2026-09-10 — Health score + regime (Part 3.3)

**Decision:** approved spec `docs/specs/3.3.md` (v2). What later parts build on:

- **Tickers pair on date, never by position.** `monitors/series.align()` inner-joins on the date string.
  - A bar is **partial** when it is dated today in New York and its body was downloaded before 16:15 ET. The rule reads the body's `asOf`, not the time of reading.
  - Only `vix` reads a partial bar (its intraday level). Every other monitor uses complete bars.
- **Last-known is option (b), in the fetcher.** A full quotes answer is also kept 24 h at `tf:risk:cache:quotes_last`. `get_quotes_view` serves it with `stale: true` on a cooldown, refusal, error or degraded answer. A partial download is patched per ticker. There is no Redis fallback: without Redis, a refusal gives `score: null`.
- **FRED is not a 3.3 input.** Its last-known key, `FredCoolingDown` → stale and cadence-based freshness move to the first FRED reader (3.6), in the same option (b) shape.
- **A/D is unavailable.** data-engine stores no advance/decline counts and risk-shield reads no other schema, so breadth is the RSP/SPY 20-day slope alone, with `adRatio: null`. The plan row's "when available" is not met.
- **Comparison operators:**
  - Part 5's operators are used verbatim, and its bare ranges are lower-inclusive.
  - A bare range that meets a `>` row closes at the top, so VIX 40.0 → 20 and a red volume ratio of 2.5 → 30.
  - Equal to an EMA counts as below.
- **Health score:**
  - integer weights and round-half-up integer arithmetic
  - a monitor with no score is left out and the rest renormalize, never counted as 0
  - covered weight < 70 → `score` and `regime` null
  - a monitor that raises is isolated
- **Provisional numbers.** These are not in Part 5, and Phase 5/6 may retune them without a spec correction:
  - partial-bar cut-off 16:15 ET
  - spy_trend 25 for a bounce under the 200 EMA; "lower lows" = min(low[-10:]) < min(low[-20:-10])
  - breadth slope band ±1.0 %
  - volume, green 1.8–2.0 → 60
  - cross-asset flat band 0.5 % and mixed → 65
  - coverage floor 70
- **Commit split.** Commit 1 came to 640 lines of code + tests (estimate ~520). It was split before any push into `6077699` / `8d03fa6`. Commit 2 was split from the start (`bcde3ee` / `2e6b51a`), because 3.1, 3.2 and 3.3 all ran over.

**Why:** the plan row names six monitors and four regime bands. Every bullet is a place where a plausible default would quietly mis-score a regime: pairing by index, a stale error, A/D read as 0, a missing monitor averaged in as 0, float rounding at 69.5.

**Supersedes:** N/A.

---

## 2026-09-10 — Regime scheduler + endpoints (Part 3.4)

**Decision:** approved spec `docs/specs/3.4.md` (v2). What later parts build on:

- **Night mode moves to a new Part 3.4b.**
  - 3.4 schedules only XNYS slots: every 5 min from open to close inclusive, plus a 16:20 ET settle check (`exchange_calendars` 4.13.2).
  - No 3.3 monitor reads futures, so a night check would repeat the last score.
  - How yfinance dates an evening `ES=F` bar is unverified. 3.4b opens with that live check.
- **The scheduler runs in prod only.**
  - `SCHEDULER_ENABLED` defaults to false, and the dev twin hard-codes false.
  - `--workers 1` is pinned in both Dockerfile stages, because uvicorn's default reads `$WEB_CONCURRENCY` and two workers would be two schedulers.
- **Redis pub/sub ignores the DB index.** Redis DB 1 does not isolate the twin's channel, so it publishes on `tf:risk:dev:health`. `test_twin_never_publishes_on_prod_channel` guards the compose override.
- **Throttle:**
  - A publish needs a regime change or a ≥ 10-point move since the last publish, at most one per 15 min.
  - CRITICAL bypasses the interval when entering it or on a ≥ 10 move inside it. Leaving CRITICAL is held like any other change.
  - A held change is delayed, never lost.
  - Delivery is at-least-once. Subscribers read `GET /market/health` on start, because pub/sub drops messages while they are down.
- **A check runs compute → trend base → publish → insert, each step isolated.** A Postgres failure never delays a publish. Null-score checks become rows and are never published.
- **Endpoints read Postgres only.**
  - No rows → 404 `no health checks yet`.
  - `settleScore` (the trend base) and the payload's `previousScore` (the last publish) are distinct on purpose.
  - `POST /market/check` is deferred.
- **Provisional numbers:**
  - trend ±5 against the latest scored settle before the check's session open
  - 60 s grace for a late slot, never caught up
  - settle at 16:20 ET, early closes included, so 3.3's 16:15 partial rule stands
- **Accepted:** 17 extra yfinance requests per prod recreate. There is no volume for the tz cache.
- **Test correction during commit 2:** the channel tests had read the twin's env, and now pin it. A test that proves a default must not depend on its container.
- **Commit split:** commit 3a came to 610 lines and was split before push (`5647ca2` / `b5324a7`).

**Why:** the plan row names a cadence, a channel and three endpoints. Each bullet is a place where a plausible default would quietly misbehave: a night check that repeats itself, Redis DB 1 assumed to isolate pub/sub, a symmetric CRITICAL bypass, one `previousScore` meaning two things, an unpinned worker count.

**Supersedes:** plan row 3.4's "every 30 min otherwise using futures (`ES=F NQ=F`) + VIX", which moves to Part 3.4b.

---

## 2026-09-10 — Part 3.4b (night mode) goes after 3.6

**Decision:** Phase 3 order is 3.5 → 3.6 → 3.4b. 3.4b's scope is unchanged (entry above).

- Until 3.4b lands, the 3.6 briefs see session checks only. The 07:30 ET brief reads the previous 16:20 settle row, with no overnight futures and no 08:00 pre-market check.
- The brief's "on regime change" trigger can only fire between 09:30 and 16:20 ET.

**Why:** 3.6 is the first consumer of night data. 3.4b's spec can't be written until the live `ES=F` / `NQ=F` evening-bar dating check has run. Sequencing 3.4b after 3.6 lets that check run against a working brief.

**Supersedes:** the plan §17 order 3.4 → 3.5 → 3.6, for 3.4b only.

---

## 2026-09-10 — Market news + econ calendar (Part 3.5)

**Decision:** approved spec `docs/specs/3.5.md` (v2, plus additions 8–9 and the correction after the live check). What later parts build on:

- **risk-shield polls, data-engine stores.** Finnhub `/news?category=general` every 15 min, around the clock → `POST /news/ingest` → `_MARKET` rows. Prod only (`NEWS_POLL_ENABLED`); the twin targets `data-engine-dev`.
- **No `minId`.** Live 2026-09-10: a 100-item page spanning ~41 h, ids in pickup order, not publish order. Every poll sends the whole page, and `ON CONFLICT (ticker, url)` absorbs the repeats. An overlap WARNING and `/health`'s page span show a shrinking page. If it ever spans under 15 min, revisit `minId` with a pickup-order check (spec, carried forward).
- **One limits table, two pinned copies:** url 2,048, title 1,000, summary 10,000, source 100, 200 items, no NUL. The converter truncates or drops before sending, so a 422 means the copies drifted: ERROR once, then WARNING per slot.
- **Finnhub 429s are account-level.** Before calling, the poller also reads data-engine's `tf:cache:finnhub` (read-only, fail-open).
- **Success** is a non-empty page, at least one item kept, and every chunk answering 200.
- **`newsPollStale`** means no success (or, before any, no start) for more than 60 min. It is on `/market/health` and every publish, and `null` when the poller is off or hasn't started.
- **The econ calendar is a file:** FOMC, CPI and jobs dates for Q3–Q4 2026 from the Fed and BLS pages. Renew by 2026-12-17; `/health` and a daily WARNING say when.
- **Deferred to Phase 6's first row (ops alerting):** every feed-stopping condition as `errors: [{source, since, message}]` in the health payload. 3.5 ships only `newsPollStale` + `newsLastError`.

**Why:** each bullet is a place where a plausible default would have quietly lost or blocked news: `minId` over pickup-order ids, a route stricter than its sender, per-service cooldowns on one account, a `null` read as "unknown".

**Supersedes:** N/A.

---

## 2026-09-10 — An addition after approval re-cuts its commit's band

**Decision:** an addition folded into an approved spec re-cuts the estimate band of the commit it lands in, in the same spec edit.

**Why:** Part 3.5's addition 8 landed after the bands were set, and 4c's band (208–288) was never re-cut. 4c measured 529 and was split into 4c-1 / 4c-2 at commit time.

**Supersedes:** N/A.

---

## 2026-09-10 — Separate estimate bands for code and tests (from Part 3.6)

**Decision:**
- A spec estimates code and tests separately on the fresh count: **code ×1.3–1.9, tests ×1.05–1.5.**
- Live scripts (`*_live.py`) count as code. Data (fixtures, JSON) stays outside both bands and the 600-line split threshold.
- Per-commit bands are cut the same way, and an addition after approval re-cuts both (entry above).

**Why:** actual ÷ fresh over the last three parts:
- code: 1.28× (3.3), 1.80× (3.4), 1.90× (3.5, live script included)
- tests: 1.05×, 1.50×, 1.27×

The shared ×1.3–1.8 put 3.5's code above its band (1,119 vs ~770–1,060) and its tests below (1,509 vs ~1,550–2,140).

**Supersedes:** the single ×1.3–1.8 band used in `docs/specs/3.5.md` decision 12.

---

## 2026-09-10 — G15 timing for prod rebuilds (from Part 3.6)

**Decision:**
- Prod `risk-shield` rebuilds happen outside XNYS hours: after the 16:20 ET settle check is recorded, or before 09:30 ET. Weekends and XNYS holidays are always fine.
- A during-hours rebuild is allowed only if the report names the slot(s) skipped and confirms the settle row was not one of them.
- `data-engine` rebuilds avoid the premarket scan window.
- **3.5 waived it**, because nothing reads the settle yet. Both rebuilds ran during the session (14:57–14:58 ET). The rebuild window held no slot boundary and no settle. The old image missed 18:45 and 18:55 UTC before the rebuild; the cause is unknown, since its logs went with the recreate.
- The rule is added to `CLAUDE.md`'s G15 rules in Part 3.6's docs commit.

**Why:** the scheduler has no catch-up. A missed settle breaks the next day's trend, which 3.6 and Phase 6 read.

**Supersedes:** N/A (it adds timing to G15's "only on explicit go").
