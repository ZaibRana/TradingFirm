"""Part 3.3 — the regime monitors and their bar helpers, on synthetic
columnar series. Pure functions of a view: no socket, no Redis, no clock."""

import inspect
import logging

import pytest

from monitors import series
from monitors.series import UnusableSeries, align, complete_bars, ema, is_partial, slope_pct

AS_OF = "2026-09-10T21:00:00+00:00"   # 17:00 EDT: a bar dated 09-10 is complete


def make_series(dates, closes, lows=None, volumes=None):
    n = len(dates)
    return {
        "date": list(dates),
        "open": list(closes),
        "high": list(closes),
        "low": list(lows) if lows is not None else list(closes),
        "close": list(closes),
        "volume": list(volumes) if volumes is not None else [1_000_000] * n,
    }


def view_of(entries, as_of=AS_OF, stale=()):
    """A get_quotes_view-shaped dict from {T: series}."""
    return {
        "asOf": as_of,
        "source": "fresh",
        "reason": None,
        "tickers": {
            t: {**s, "asOf": s.get("asOf", as_of), "stale": t in stale} for t, s in entries.items()
        },
        "staleTickers": list(stale),
    }


# ── Alignment: happy path ────────────────────────────────────────

def test_align_all_dates_common():
    d = ["2026-09-04", "2026-09-08", "2026-09-09"]
    view = view_of({"RSP": make_series(d, [1.0, 2.0, 3.0]), "SPY": make_series(d, [10.0, 20.0, 30.0])})
    a = align(view, ["RSP", "SPY"])
    assert a["dates"] == d
    assert a["close"] == {"RSP": [1.0, 2.0, 3.0], "SPY": [10.0, 20.0, 30.0]}
    assert a["volume"]["SPY"] == [1_000_000] * 3
    assert (a["droppedDates"], a["missing"], a["unusable"]) == (0, [], [])
    assert align(view, ["SPY", "QQQ"])["missing"] == ["QQQ"]


def test_ema_matches_hand_computed():
    assert ema([1.0, 2.0, 3.0], 3) == [1.0, 1.5, 2.25]   # alpha 0.5
    assert ema([], 20) == []


def test_slope_pct_matches_hand_computed():
    assert slope_pct([1.0, 2.0, 3.0]) == pytest.approx(100.0)   # slope 1 × 2 / mean 2
    assert slope_pct([2.0, 2.0, 4.0, 4.0]) == pytest.approx(80.0)   # 4/5 × 3 / 3
    assert slope_pct([5.0] * 20) == 0.0
    with pytest.raises(ValueError):
        slope_pct([1.0])


# ── Alignment: failure branches ──────────────────────────────────

def test_align_pairs_on_date_not_position():
    """The live 3.2 shape: SPY ends on the prior session and lacks a date
    RSP has; ^VIX carries today's intraday bar. Pairing by index would put
    RSP's 09-07 against SPY's 09-08 and fail nowhere."""
    as_of = "2026-09-10T18:00:00+00:00"   # 14:00 EDT, session open
    spy = make_series(["2026-09-04", "2026-09-08", "2026-09-09"], [500.0, 508.0, 509.0])
    rsp = make_series(["2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09"], [180.0, 181.0, 182.0, 183.0])
    vix = make_series(["2026-09-04", "2026-09-08", "2026-09-09", "2026-09-10"], [15.0, 16.0, 17.0, 25.0])
    a = align(view_of({"SPY": spy, "RSP": rsp, "^VIX": vix}, as_of=as_of), ["RSP", "SPY", "^VIX"])
    assert a["dates"] == ["2026-09-04", "2026-09-08", "2026-09-09"]
    assert a["close"]["RSP"] == [180.0, 182.0, 183.0]   # 09-07 dropped, not shifted
    assert a["close"]["SPY"] == [500.0, 508.0, 509.0]
    assert a["close"]["^VIX"] == [15.0, 16.0, 17.0]     # today's partial bar never paired
    assert a["droppedDates"] == 1


@pytest.mark.parametrize(
    "dates",
    [["2026-09-04", "2026-09-04", "2026-09-08"], ["2026-09-08", "2026-09-04", "2026-09-09"]],
    ids=["duplicate", "unsorted"],
)
def test_series_unsorted_or_duplicate_dates_unusable(dates, caplog):
    bad = make_series(dates, [1.0, 2.0, 3.0])
    good = make_series(["2026-09-04", "2026-09-08", "2026-09-09"], [1.0, 2.0, 3.0])
    with caplog.at_level(logging.WARNING, logger="monitors.series"):
        a = align(view_of({"SPY": good, "RSP": bad}), ["RSP", "SPY"])
    assert a["unusable"] == ["RSP"]
    assert a["dates"] == [] and a["close"] == {}
    assert any("RSP" in rec.getMessage() for rec in caplog.records)
    with pytest.raises(UnusableSeries):
        complete_bars(bad, AS_OF, "RSP")


@pytest.mark.parametrize(
    "as_of, kept",
    [
        ("2026-09-10T20:14:59+00:00", ["2026-09-08", "2026-09-09"]),                # 16:14:59 EDT
        ("2026-09-10T20:15:00+00:00", ["2026-09-08", "2026-09-09", "2026-09-10"]),  # 16:15 EDT
    ],
    ids=["before_1615_dropped", "at_1615_kept"],
)
def test_complete_bars_partial_rule_at_1615_et(as_of, kept):
    s = make_series(["2026-09-08", "2026-09-09", "2026-09-10"], [1.0, 2.0, 3.0])
    out = complete_bars(s, as_of)
    assert out["date"] == kept
    assert len(out["close"]) == len(out["volume"]) == len(kept)
    prior = make_series(["2026-09-08", "2026-09-09"], [1.0, 2.0])   # last bar is yesterday's
    assert complete_bars(prior, "2026-09-10T14:00:00+00:00")["date"] == ["2026-09-08", "2026-09-09"]


@pytest.mark.parametrize(
    "bar_date, as_of, partial",
    [
        ("2026-09-10", "2026-09-10T19:00:00+00:00", True),    # 15:00 EDT; a UTC clock reads 19:00
        ("2026-09-10", "2026-09-10T20:15:00+00:00", False),   # 16:15 EDT
        ("2026-12-10", "2026-12-10T20:30:00+00:00", True),    # 15:30 EST; a fixed UTC-4 reads 16:30
        ("2026-12-10", "2026-12-10T21:15:00+00:00", False),   # 16:15 EST
        ("2026-12-10", "2026-12-11T02:00:00+00:00", False),   # 21:00 EST on 12-10; UTC date is 12-11
    ],
    ids=["edt_1500", "edt_1615", "est_1530", "est_1615", "est_evening_utc_next_day"],
)
def test_partial_bar_rule_uses_eastern_time_across_dst(bar_date, as_of, partial):
    assert is_partial(bar_date, as_of) is partial


def test_partial_rule_uses_download_time_not_read_time():
    """A body downloaded at 15:58 ET holds a partial bar whenever it is
    read: the rule takes the body's asOf and series.py has no clock."""
    s = make_series(["2026-09-09", "2026-09-10"], [1.0, 2.0])
    downloaded_1558 = view_of({"SPY": s}, as_of="2026-09-10T19:58:00+00:00")
    downloaded_1620 = view_of({"SPY": s}, as_of="2026-09-10T20:20:00+00:00")
    assert align(downloaded_1558, ["SPY"])["dates"] == ["2026-09-09"]
    assert align(downloaded_1620, ["SPY"])["dates"] == ["2026-09-09", "2026-09-10"]
    source = inspect.getsource(series)
    assert ".now(" not in source and "time.time" not in source
