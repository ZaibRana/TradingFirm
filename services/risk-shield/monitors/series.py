"""
TradingFirm — date alignment and bar helpers for the regime monitors (Part 3.3).

One yfinance download returns a different date array per ticker (the 3.2
canary: the ETFs ended on the prior session while ^VIX and the futures
carried today's bar). Two tickers are therefore only ever paired through
align(), which inner-joins on the date string, never by array position
(spec 3.3 decision 1).

A bar is partial when it is dated today in New York and its body was
downloaded before 16:15 ET (VIX settles at 16:15, after the ETF close).
The clock is the body's own asOf; this module never reads the time.

Plain lists, no pandas: ~250 floats per ticker (G8).
"""

import logging
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
SESSION_SETTLED_ET = time(16, 15)
ALIGNED_FIELDS = ("close", "low", "volume")


class UnusableSeries(ValueError):
    """A ticker's dates are not strictly ascending (duplicate or unsorted)."""


def _as_datetime(as_of: Any) -> datetime:
    dt = as_of if isinstance(as_of, datetime) else datetime.fromisoformat(as_of)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def is_partial(bar_date: str, as_of: Any) -> bool:
    """True iff the bar is dated on asOf's Eastern date and asOf is before 16:15 ET."""
    et = _as_datetime(as_of).astimezone(ET)
    return bar_date == et.date().isoformat() and et.time() < SESSION_SETTLED_ET


def check_ascending(ticker: str, dates) -> None:
    """Fails closed: a duplicate date would silently double-weight a day."""
    if not all(isinstance(d, str) for d in dates) or any(a >= b for a, b in zip(dates, dates[1:])):
        raise UnusableSeries(f"{ticker}: dates not strictly ascending")


def complete_bars(series: dict, as_of: Any, ticker: str = "?") -> dict:
    """
    A copy of a columnar series without a trailing partial bar. Once dates
    are ascending only the last bar can be partial. Non-list keys (asOf,
    stale) are carried over unchanged.
    """
    dates = list(series.get("date") or [])
    check_ascending(ticker, dates)
    n = len(dates)
    if n and is_partial(dates[-1], as_of):
        n -= 1
    return {k: list(v[:n]) if isinstance(v, list) else v for k, v in series.items()}


def align(view: dict, tickers) -> dict:
    """
    Complete bars of `tickers`, inner-joined on date:
        {dates, close: {T: []}, low: {T: []}, volume: {T: []},
         droppedDates, missing, unusable}
    Each ticker's partial bar is dropped against the asOf of the body it
    came from. A missing or unusable ticker leaves `dates` empty.
    `droppedDates` counts dates some but not all tickers have.
    """
    tickers = list(tickers)
    out: dict = {"dates": [], "droppedDates": 0, "missing": [], "unusable": []}
    for field in ALIGNED_FIELDS:
        out[field] = {}

    complete: dict[str, dict] = {}
    for t in tickers:
        entry = (view.get("tickers") or {}).get(t)
        if entry is None:
            out["missing"].append(t)
            continue
        try:
            complete[t] = complete_bars(entry, entry["asOf"], t)
        except UnusableSeries as e:
            logger.warning(f"align: {e}")
            out["unusable"].append(t)
    if out["missing"] or out["unusable"] or not complete:
        return out

    date_sets = [set(s["date"]) for s in complete.values()]
    common = set.intersection(*date_sets)
    out["droppedDates"] = len(set.union(*date_sets)) - len(common)
    out["dates"] = sorted(common)
    for t, s in complete.items():
        index = {d: i for i, d in enumerate(s["date"])}
        for field in ALIGNED_FIELDS:
            column = s.get(field) or [None] * len(s["date"])
            out[field][t] = [column[index[d]] for d in out["dates"]]
    return out


def ema(values, span: int) -> list[float]:
    """Seeded with the first value, alpha = 2 / (span + 1): data-engine's
    `ema` (pandas ewm(adjust=False))."""
    if not values:
        return []
    alpha = 2.0 / (span + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def slope_pct(values) -> float:
    """Least-squares slope as the fitted % change across the window:
    slope × (n − 1) / mean × 100 (breadth, spec 3.3 decision 5)."""
    n = len(values)
    if n < 2:
        raise ValueError("slope needs at least 2 values")
    mean = sum(values) / n
    if mean == 0:
        raise ValueError("slope of a zero-mean window")
    xm = (n - 1) / 2
    num = sum((i - xm) * (v - mean) for i, v in enumerate(values))
    den = sum((i - xm) ** 2 for i in range(n))
    return num / den * (n - 1) / mean * 100
