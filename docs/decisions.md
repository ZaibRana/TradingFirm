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
