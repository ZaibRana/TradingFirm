"""
TradingFirm — the regime monitors (Part 3.3).

Each monitor is a pure function of the view built by quotes.get_quotes_view
and returns exactly {score, raw, detail, stale} (spec 3.3 decision 4):
  score  int 0–100, or None when it cannot score (never 0 for "no data")
  raw    JSON-safe dict, camelCase
  detail one human line
  stale  True iff any input ticker is in the view's staleTickers

Scoring rows follow Part 5 with its operators verbatim; the numbers Part 5
does not give are provisional (spec 3.3 decision 5). Only `vix` reads a
ticker directly (its partial bar is the freshest level); every other
monitor reads through series.align(), so tickers pair on date and partial
bars never count.
"""

import logging
from typing import Callable, NamedTuple, Optional

from monitors.series import (
    UnusableSeries,
    align,
    check_ascending,
    complete_bars,
    ema,
    is_partial,
    slope_pct,
)

logger = logging.getLogger(__name__)


class Monitor(NamedTuple):
    fn: Callable[[dict], dict]
    weight: int          # integer percent (spec 3.3 decision 6)
    tickers: tuple


def _result(score: Optional[int], raw: dict, detail: str, stale: bool) -> dict:
    return {"score": score, "raw": raw, "detail": detail, "stale": stale}


def _stale(view: dict, tickers) -> bool:
    stale = set(view.get("staleTickers") or [])
    return any(t in stale for t in tickers)


def _pct(before: float, after: float) -> float:
    """Percent change, multiplied before dividing so band edges such as
    +1.0 and −0.5 come out exact."""
    return (after - before) * 100 / before


def _unaligned(a: dict, stale: bool, name: str) -> Optional[dict]:
    """The null result for a missing or unusable input, else None."""
    if a["missing"]:
        return _result(None, {}, f"{name}: {', '.join(a['missing'])} unavailable", stale)
    if a["unusable"]:
        return _result(None, {}, f"{name}: {', '.join(a['unusable'])} dates not strictly ascending", stale)
    return None


# ── vix (weight 25) ──────────────────────────────────────────────

VIX_SPIKE_FACTOR = 1.20
VIX_SPIKE_PENALTY = 15


def vix_band(level: float) -> tuple[int, str]:
    if level < 15:
        return 95, "extremely calm"
    if level < 20:
        return 80, "normal conditions"
    if level < 25:
        return 60, "elevated uncertainty"
    if level < 30:
        return 40, "high fear"
    if level <= 40:
        return 20, "panic zone"
    return 5, "crisis territory"     # Part 5: "> 40"


def vix(view: dict) -> dict:
    stale = _stale(view, ("^VIX",))
    entry = (view.get("tickers") or {}).get("^VIX")
    if entry is None:
        return _result(None, {}, "vix: ^VIX unavailable", stale)
    try:
        check_ascending("^VIX", entry["date"])
        complete = complete_bars(entry, entry["asOf"], "^VIX")
    except UnusableSeries as e:
        logger.warning(f"vix: {e}")
        return _result(None, {}, f"vix: {e}", stale)
    dates = entry["date"]
    if not dates or entry["close"][-1] is None:
        return _result(None, {"bars": len(dates)}, f"vix: {len(dates)} bars, need ≥ 1", stale)

    level = entry["close"][-1]
    date = dates[-1]
    earlier = [i for i, d in enumerate(complete["date"]) if d < date]
    prev = complete["close"][earlier[-1]] if earlier else None

    score, label = vix_band(level)
    spike = prev is not None and level > prev * VIX_SPIKE_FACTOR
    if spike:
        score = max(score - VIX_SPIKE_PENALTY, 0)
    change_pct = (level / prev - 1) * 100 if prev else None
    detail = f"VIX at {level:.1f} — {label}"
    if spike:
        detail += f", up {change_pct:.1f}% in a day (−{VIX_SPIKE_PENALTY})"
    raw = {
        "level": level,
        "prevClose": prev,
        "changePct": change_pct,
        "date": date,
        "partial": is_partial(date, entry["asOf"]),
    }
    return _result(score, raw, detail, stale)


# ── spy_trend (weight 20) ────────────────────────────────────────

SPY_TREND_MIN_BARS = 200
LOWER_LOWS_WINDOW = 10


def _lower_lows(lows: list) -> bool:
    """min(low[-10:]) < min(low[-20:-10]); a window with no lows is not lower."""
    recent = [x for x in lows[-LOWER_LOWS_WINDOW:] if x is not None]
    prior = [x for x in lows[-2 * LOWER_LOWS_WINDOW:-LOWER_LOWS_WINDOW] if x is not None]
    return bool(recent and prior) and min(recent) < min(prior)


def spy_trend(view: dict) -> dict:
    stale = _stale(view, ("SPY",))
    a = align(view, ("SPY",))
    null = _unaligned(a, stale, "spy_trend")
    if null:
        return null
    closes = a["close"]["SPY"]
    if len(closes) < SPY_TREND_MIN_BARS:
        return _result(None, {"bars": len(closes)},
                       f"spy_trend: {len(closes)} complete bars, need ≥ {SPY_TREND_MIN_BARS}", stale)

    close = closes[-1]
    e20, e50, e200 = ema(closes, 20)[-1], ema(closes, 50)[-1], ema(closes, 200)[-1]
    a20, a50, a200 = close > e20, close > e50, close > e200     # equal counts as below
    lower_lows = _lower_lows(a["low"]["SPY"])

    if not a200 and lower_lows:
        score, detail = 5, "SPY below its 200 EMA and making lower lows"
    elif not (a20 or a50 or a200):
        score, detail = 15, "SPY below its 20, 50 and 200 EMAs"
    elif not a200:
        score, detail = 25, "SPY bouncing but still below its 200 EMA"
    elif a20 and a50:
        score, detail = 95, "SPY above its 20, 50 and 200 EMAs"
    elif a50:
        score, detail = 70, "SPY above its 50 and 200 EMAs, below its 20"
    else:
        score, detail = 45, "SPY above its 200 EMA only"
    raw = {"close": close, "ema20": e20, "ema50": e50, "ema200": e200,
           "lowerLows": lower_lows, "date": a["dates"][-1]}
    return _result(score, raw, detail, stale)


# ── volume (weight 10) ───────────────────────────────────────────

VOLUME_WINDOW = 20


def volume(view: dict) -> dict:
    tickers = ("SPY", "QQQ")          # never ^VIX: its volume is always 0
    stale = _stale(view, tickers)
    a = align(view, tickers)
    null = _unaligned(a, stale, "volume")
    if null:
        return null
    n = len(a["dates"])
    if n < VOLUME_WINDOW + 1:
        return _result(None, {"bars": n, "droppedDates": a["droppedDates"]},
                       f"volume: {n} aligned complete bars, need ≥ {VOLUME_WINDOW + 1}", stale)

    sums = [s + q if s is not None and q is not None else None
            for s, q in zip(a["volume"]["SPY"], a["volume"]["QQQ"])]
    date = a["dates"][-1]
    base = {"date": date, "droppedDates": a["droppedDates"]}
    if sums[-1] is None:
        return _result(None, base, f"volume: SPY/QQQ volume missing on {date}", stale)
    window = [x for x in sums[-(VOLUME_WINDOW + 1):-1] if x is not None]
    mean = sum(window) / len(window) if window else 0
    if mean <= 0:
        return _result(None, base, f"volume: no volume in the {VOLUME_WINDOW}-day window", stale)

    ratio = sums[-1] / mean
    closes = a["close"]["SPY"]
    red = closes[-1] < closes[-2]

    if ratio < 1.2:
        score, label = 85, "normal"
    elif ratio < 1.8:
        score, label = 60, "elevated"
    elif red:
        score, label = (10, "capitulation-level selling") if ratio > 2.5 else (30, "distribution")
    else:
        score, label = (80, "accumulation") if ratio > 2.0 else (60, "elevated")
    detail = f"SPY+QQQ volume {ratio:.2f}x its 20-day average on a {'red' if red else 'green'} day — {label}"
    return _result(score, {"ratio": ratio, "red": red, **base}, detail, stale)


# ── breadth (weight 20) ──────────────────────────────────────────
# Plan substitute for Part 5's constituent breadth: RSP/SPY ratio slope.
# A/D is unavailable (data-engine stores no advance/decline counts and this
# service reads no other schema), so adRatio is null, never 0.

BREADTH_WINDOW = 20
BREADTH_SLOPE_BAND = 1.0     # provisional (spec 3.3 decision 5)


def breadth(view: dict) -> dict:
    tickers = ("RSP", "SPY")
    stale = _stale(view, tickers)
    a = align(view, tickers)
    null = _unaligned(a, stale, "breadth")
    if null:
        return null
    n = len(a["dates"])
    if n < BREADTH_WINDOW:
        return _result(None, {"bars": n, "droppedDates": a["droppedDates"], "adRatio": None},
                       f"breadth: {n} aligned complete bars, need ≥ {BREADTH_WINDOW}", stale)

    rsp, spy = a["close"]["RSP"][-BREADTH_WINDOW:], a["close"]["SPY"][-BREADTH_WINDOW:]
    ratios = [r / s for r, s in zip(rsp, spy)]
    slope = slope_pct(ratios)
    if slope > BREADTH_SLOPE_BAND:
        score, label = 90, "equal weight leading, broad participation"
    elif slope >= -BREADTH_SLOPE_BAND:
        score, label = 60, "equal weight tracking cap weight"
    else:
        score, label = 25, "equal weight lagging, narrow leadership"
    raw = {"ratio": ratios[-1], "slopePct": slope, "window": BREADTH_WINDOW, "adRatio": None,
           "droppedDates": a["droppedDates"], "date": a["dates"][-1]}
    detail = f"RSP/SPY 20-day slope {slope:+.2f}% — {label} (A/D unavailable)"
    return _result(score, raw, detail, stale)


# ── sector_rotation (weight 15) ──────────────────────────────────

OFFENSIVE = ("XLK", "XLY", "XLF")
DEFENSIVE = ("XLU", "XLP", "XLV")
SECTOR_DAYS = 5


def sector_rotation(view: dict) -> dict:
    tickers = OFFENSIVE + DEFENSIVE
    stale = _stale(view, tickers)
    a = align(view, tickers)
    null = _unaligned(a, stale, "sector_rotation")
    if null:
        return null
    n = len(a["dates"])
    if n < SECTOR_DAYS + 1:
        return _result(None, {"bars": n, "droppedDates": a["droppedDates"]},
                       f"sector_rotation: {n} aligned complete bars, need ≥ {SECTOR_DAYS + 1}", stale)

    def mean_return(group) -> float:
        closes = a["close"]
        return sum(_pct(closes[t][-(SECTOR_DAYS + 1)], closes[t][-1]) for t in group) / len(group)

    offensive, defensive = mean_return(OFFENSIVE), mean_return(DEFENSIVE)
    spread = offensive - defensive
    if spread > 1:
        score, label = 90, "offensive sectors leading, risk-on"
    elif spread >= -1:
        score, label = 65, "offensive and defensive roughly equal"
    elif spread >= -2:
        score, label = 30, "defensive sectors leading, risk-off"
    else:
        score, label = 10, "panic rotation into defensives"
    raw = {"offensivePct": offensive, "defensivePct": defensive, "spreadPct": spread,
           "droppedDates": a["droppedDates"], "date": a["dates"][-1]}
    detail = f"Offensive minus defensive over 5 days {spread:+.2f} pts — {label}"
    return _result(score, raw, detail, stale)


# ── cross_asset (weight 10) ──────────────────────────────────────

CROSS_FLAT_BAND = 0.5    # provisional (spec 3.3 decision 5)
CROSS_SHARP_PCT = 1.0    # Part 5: "Bonds up sharply (>1% day)"


def cross_asset(view: dict) -> dict:
    tickers = ("TLT", "GLD", "UUP", "SPY")
    stale = _stale(view, tickers)
    a = align(view, tickers)
    null = _unaligned(a, stale, "cross_asset")
    if null:
        return null
    n = len(a["dates"])
    if n < 2:
        return _result(None, {"bars": n, "droppedDates": a["droppedDates"]},
                       f"cross_asset: {n} aligned complete bars, need ≥ 2", stale)

    pct = {t: _pct(a["close"][t][-2], a["close"][t][-1]) for t in tickers}
    havens = (pct["TLT"], pct["GLD"], pct["UUP"])

    def up(p): return p >= CROSS_FLAT_BAND
    def down(p): return p <= -CROSS_FLAT_BAND
    def flat(p): return -CROSS_FLAT_BAND < p < CROSS_FLAT_BAND

    if all(down(p) for p in havens) and down(pct["SPY"]):
        score, label = 15, "everything falling, bonds included — liquidity stress"
    elif all(up(p) for p in havens):
        score, label = 25, "bonds, gold and dollar all bid — flight to safety"
    elif pct["TLT"] > CROSS_SHARP_PCT and down(pct["SPY"]):
        score, label = 35, "bonds up sharply while stocks fall — risk-off"
    elif all(flat(p) for p in havens):
        score, label = 80, "bonds, gold and dollar calm"
    else:
        score, label = 65, "mixed cross-asset signals"
    raw = {"tltPct": pct["TLT"], "gldPct": pct["GLD"], "uupPct": pct["UUP"], "spyPct": pct["SPY"],
           "date": a["dates"][-1], "droppedDates": a["droppedDates"]}
    detail = (f"TLT {pct['TLT']:+.2f}%, GLD {pct['GLD']:+.2f}%, UUP {pct['UUP']:+.2f}%, "
              f"SPY {pct['SPY']:+.2f}% — {label}")
    return _result(score, raw, detail, stale)


# ── Registry (Part 5 order) ──────────────────────────────────────

MONITORS: dict[str, Monitor] = {
    "vix": Monitor(vix, 25, ("^VIX",)),
    "breadth": Monitor(breadth, 20, ("RSP", "SPY")),
    "spy_trend": Monitor(spy_trend, 20, ("SPY",)),
    "sector_rotation": Monitor(sector_rotation, 15, OFFENSIVE + DEFENSIVE),
    "volume": Monitor(volume, 10, ("SPY", "QQQ")),
    "cross_asset": Monitor(cross_asset, 10, ("TLT", "GLD", "UUP", "SPY")),
}
