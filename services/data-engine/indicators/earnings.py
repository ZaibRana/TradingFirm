"""
TradingFirm — Earnings-day reaction history (Part 2.3, commit 2).

Plan §3 lists "earnings-day reaction history (last 8 reports: gap %,
close-to-close %)" in the swing indicator set, so the calculation lives
here with the other indicators: one pure function, no I/O. The wrapper
that queries Postgres is providers/context/earnings.py.

What a reaction is: the report happens, then one trading session absorbs
it. For an after-close report (`amc`) that is the NEXT session; for a
before-open or during-hours report (`bmo` / `dmh`) it is the session on
the report date itself.

    gapPct           = (session open  - previous close) / previous close
    closeToClosePct  = (session close - previous close) / previous close

Two things the data cannot always tell us, and one rule for both:

  * an unknown report hour (Alpha Vantage's reportTime is sometimes
    absent), and
  * two sources naming different dates for one report,

are resolved by VOLUME, never by picking the bigger move. A report is the
highest-volume session of its week; "whichever session moved more" would
select the bigger move by construction and quietly bias every statistic
built on this history. The rule is indicators.calc_rvol >= 2 on the
candidate session; if it cannot separate the candidates, the report is
dropped and counted rather than guessed.

dataQuality travels with the result so 2.4 can show what was thrown away:
    source         'yfinance' | 'alphavantage' | 'mixed' | None
    dropped        rows that should have produced a reaction and did not
    disagreements  cross-source date conflicts seen (resolved or not)

`reactions` is None (not []) when no confirmed report exists at all — the
ticker was never refreshed, or both sources were down. [] means reports
exist but no bars could explain them.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd

from indicators.volume import calc_rvol

logger = logging.getLogger(__name__)

EVENT_EARNINGS = "earnings"
META_KEY = "earnings"

HOUR_AMC = "amc"
HOUR_BMO = "bmo"
HOUR_DMH = "dmh"

RVOL_CONFIRM = 2.0          # a report session prints at least 2x average volume
RVOL_LOOKBACK = 20
SESSION_TOLERANCE_DAYS = 4  # holiday weekend between the report and its session
SAME_REPORT_DAYS = 20       # quarters are >= 60 days apart
ADJACENT_DAYS = 1           # <= 1 day apart across sources is one report

SOURCE_MIXED = "mixed"


# ── small helpers ────────────────────────────────────────────────────────


def _meta(event: dict) -> dict:
    meta = event.get("meta") or {}
    return meta if isinstance(meta, dict) else {}


def _earnings_meta(event: dict) -> dict:
    block = _meta(event).get(META_KEY) or {}
    return block if isinstance(block, dict) else {}


def _is_confirmed(event: dict) -> bool:
    """A report we can trust the date of: validated by this part, or a
    Part 2.1 calendar row that has since reported an actual EPS."""
    if _earnings_meta(event).get("validated") is True:
        return True
    calendar = _meta(event).get("calendar") or {}
    return isinstance(calendar, dict) and calendar.get("epsActual") is not None


def _event_date(event: dict) -> Optional[date]:
    at = event.get("event_at")
    if isinstance(at, datetime):
        return at.date()
    return at if isinstance(at, date) else None


def _source(event: dict) -> Optional[str]:
    return _earnings_meta(event).get("source")


def _number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _rvol_at(bars: list[dict], i: int) -> float:
    """RVOL of bar `i` against the 20 sessions before it. calc_rvol wants a
    series ending at the candidate, and returns 0.0 with too little history
    — which reads here as 'cannot confirm', the same as low volume."""
    if i < 1:
        return 0.0
    volumes = pd.Series([_number(b.get("volume")) or 0.0 for b in bars[: i + 1]], dtype="float64")
    return calc_rvol(volumes, float(_number(bars[i].get("volume")) or 0.0), RVOL_LOOKBACK)


# ── session selection ────────────────────────────────────────────────────


def _first_index(bar_dates: list[date], d: date, *, strictly_after: bool) -> Optional[int]:
    for i, bd in enumerate(bar_dates):
        if bd > d or (not strictly_after and bd == d):
            return i
    return None


def _session_for_hour(bar_dates: list[date], d: date, hour: Optional[str]) -> Optional[int]:
    return _first_index(bar_dates, d, strictly_after=(hour == HOUR_AMC))


def _within_tolerance(bar_dates: list[date], i: int, d: date, hour: Optional[str]) -> bool:
    """The chosen session must be the one that actually absorbed the report,
    not a bar on the far side of a hole in the store."""
    earliest = d + timedelta(days=1) if hour == HOUR_AMC else d
    return (bar_dates[i] - earliest).days <= SESSION_TOLERANCE_DAYS


def _confirmed_session(bar_dates: list[date], bars: list[dict], d: date) -> Optional[int]:
    """The volume rule: of the session on `d` and the one after it, the one
    printing >= 2x average volume — and only if exactly one does."""
    candidates = []
    for strictly_after in (False, True):
        i = _first_index(bar_dates, d, strictly_after=strictly_after)
        if i is not None and i not in candidates and (bar_dates[i] - d).days <= SESSION_TOLERANCE_DAYS:
            candidates.append(i)
    hits = [i for i in candidates if _rvol_at(bars, i) >= RVOL_CONFIRM]
    return hits[0] if len(hits) == 1 else None


# ── date reconciliation across and within sources ────────────────────────


def _reconcile(events: list[dict], bar_dates: list[date], bars: list[dict]) -> tuple[list[dict], int, int]:
    """
    Collapse duplicates and resolve cross-source date conflicts.

    Returns (kept, dropped, disagreements).
      same source, < 20 days apart   one report, keep the earlier (no count:
                                     nothing is lost)
      cross source, <= 1 day apart   one report, keep the yfinance row (the
                                     primary); not a disagreement
      cross source, 1 < gap < 20     a real conflict: keep whichever date has
                                     a volume-confirmed session, else drop
                                     both. Counted either way.
    """
    ordered = sorted(events, key=_event_date)
    kept: list[dict] = []
    dropped = disagreements = 0

    for event in ordered:
        if not kept:
            kept.append(event)
            continue
        previous = kept[-1]
        gap = (_event_date(event) - _event_date(previous)).days
        if gap >= SAME_REPORT_DAYS:
            kept.append(event)
            continue

        same_source = _source(event) == _source(previous)
        if same_source:
            logger.warning(
                f"earnings_reactions: {_event_date(previous)} and {_event_date(event)} "
                f"are {gap}d apart from the same source, keeping the earlier"
            )
            continue
        if gap <= ADJACENT_DAYS:
            # One report, two sources, off by a day. Keep the primary.
            if _source(previous) != "yfinance" and _source(event) == "yfinance":
                kept[-1] = event
            continue

        disagreements += 1
        winner_prev = _confirmed_session(bar_dates, bars, _event_date(previous))
        winner_this = _confirmed_session(bar_dates, bars, _event_date(event))
        logger.warning(
            f"earnings_reactions: sources disagree — {_source(previous)} says "
            f"{_event_date(previous)}, {_source(event)} says {_event_date(event)} "
            f"({gap}d apart)"
        )
        if (winner_prev is None) == (winner_this is None):
            kept.pop()
            dropped += 2
        elif winner_this is not None:
            kept[-1] = event

    return kept, dropped, disagreements


# ── the reaction itself ──────────────────────────────────────────────────


def _reaction(event: dict, bar_dates: list[date], bars: list[dict]) -> tuple[Optional[dict], bool]:
    """(row, counted_as_dropped). row is None when no reaction is possible."""
    d = _event_date(event)
    meta = _earnings_meta(event)
    calendar = _meta(event).get("calendar") or {}
    hour = meta.get("hour") or (calendar.get("hour") if isinstance(calendar, dict) else None)
    hour_assumed = False

    if d < bar_dates[0]:
        return None, True                       # older than the stored history

    if hour in (HOUR_AMC, HOUR_BMO, HOUR_DMH):
        i = _session_for_hour(bar_dates, d, hour)
        if i is None:
            logger.warning(f"earnings_reactions: {d} is newer than the last stored bar")
            return None, True
        if not _within_tolerance(bar_dates, i, d, hour):
            logger.warning(
                f"earnings_reactions: no session within {SESSION_TOLERANCE_DAYS}d of {d} "
                f"(nearest {bar_dates[i]}), bar store gap"
            )
            return None, True
    else:
        i = _confirmed_session(bar_dates, bars, d)
        if i is None:
            logger.warning(f"earnings_reactions: hour unknown for {d} and volume cannot separate the sessions")
            return None, True
        hour_assumed = True

    if i == 0:
        return None, True                       # no previous close to measure from

    previous, session = bars[i - 1], bars[i]
    prev_close = _number(previous.get("close"))
    open_r = _number(session.get("open"))
    close_r = _number(session.get("close"))
    if not prev_close or open_r is None or close_r is None:
        return None, True

    return {
        "reportDate": d.isoformat(),
        "session": bar_dates[i].isoformat(),
        "hour": hour,
        "hourAssumed": hour_assumed,
        "source": _source(event),
        "gapPct": round((open_r - prev_close) / prev_close * 100, 2),
        "closeToClosePct": round((close_r - prev_close) / prev_close * 100, 2),
        "epsEstimate": _number(meta.get("epsEstimate")),
        "epsReported": _number(meta.get("epsReported")),
        "surprisePct": _number(meta.get("surprisePct")),
    }, False


def earnings_reactions(
    events: list[dict],
    bars: list[dict],
    limit: int = 8,
    today: Optional[date] = None,
) -> dict:
    """
    Last `limit` earnings reactions, newest first, from stored events and
    stored daily bars. Pure: no I/O, no clock beyond `today`.

    Args:
        events: data_engine.events rows (db.get_events shape). Only
            ('earnings', date) rows are considered, and only confirmed ones.
        bars: daily bars ascending (db.get_bars shape).
        limit: how many reactions to return (>= 1).
        today: reference date; future reports are skipped, not counted.

    Returns:
        {"reactions": [...] | None, "dataQuality": {source, dropped, disagreements}}
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"limit must be a positive int, got {limit!r}")
    today = today or datetime.now(timezone.utc).date()

    earnings = [
        e for e in events
        if e.get("event_type") == EVENT_EARNINGS and _event_date(e) is not None
    ]
    confirmed = [e for e in earnings if _is_confirmed(e)]
    # Only a PAST row that could not be confirmed is a quality problem: an
    # upcoming report is unvalidated by construction (there are no bars to
    # validate it against yet), so it must not inflate `dropped`.
    unconfirmed = len([
        e for e in earnings if e not in confirmed and _event_date(e) <= today
    ])

    if not confirmed:
        return {
            "reactions": None,
            "dataQuality": {"source": None, "dropped": unconfirmed, "disagreements": 0},
        }

    def _quality(dropped: int, disagreements: int, sources) -> dict:
        known = {s for s in sources if s}
        source = known.pop() if len(known) == 1 else (SOURCE_MIXED if known else None)
        return {"source": source, "dropped": dropped, "disagreements": disagreements}

    past = [e for e in confirmed if _event_date(e) <= today]
    if not bars:
        return {
            "reactions": [],
            "dataQuality": _quality(unconfirmed + len(past), 0, (_source(e) for e in confirmed)),
        }

    bar_dates = [b["ts"].date() if isinstance(b.get("ts"), datetime) else b.get("ts") for b in bars]
    kept, dropped, disagreements = _reconcile(past, bar_dates, bars)
    dropped += unconfirmed

    reactions = []
    for event in kept:
        row, counted = _reaction(event, bar_dates, bars)
        if row is not None:
            reactions.append(row)
        elif counted:
            dropped += 1

    reactions.sort(key=lambda r: r["reportDate"], reverse=True)
    used = [r["source"] for r in reactions] or [_source(e) for e in confirmed]
    return {
        "reactions": reactions[:limit],
        "dataQuality": _quality(dropped, disagreements, used),
    }
