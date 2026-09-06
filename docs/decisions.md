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
