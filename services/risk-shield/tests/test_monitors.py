"""Part 3.3 — the regime monitors and their bar helpers, on synthetic
columnar series. Pure functions of a view: no socket, no Redis, no clock."""

import copy
import inspect
import json
import logging
from datetime import date, timedelta

import pytest

from monitors import regime, series
from monitors.regime import MONITORS
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


# ── Monitor helpers ──────────────────────────────────────────────

# Bars each monitor needs before it can score (spec 3.3 decision 4).
MIN_BARS = {"vix": 1, "breadth": 20, "spy_trend": 200, "sector_rotation": 6, "volume": 21, "cross_asset": 2}


def bdays(n, end=date(2026, 9, 9)):
    """n weekday ISO dates ending at `end` (a Wednesday, the prior session)."""
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def full_view(n=230, stale=(), drop=(), bars=None):
    """All 17 core tickers, gently rising, 1M volume each; ^VIX flat at 18.
    `bars` truncates the named tickers to that many bars."""
    from monitors.quotes import CORE_TICKERS
    entries = {}
    for k, t in enumerate(CORE_TICKERS):
        if t in drop:
            continue
        size = (bars or {}).get(t, n)
        closes = [18.0] * size if t == "^VIX" else [100.0 + k + 0.1 * i for i in range(size)]
        entries[t] = make_series(bdays(size), closes)
    return view_of(entries, stale=stale)


def vix_view(closes, as_of=AS_OF, dates=None):
    return view_of({"^VIX": make_series(dates or bdays(len(closes)), closes)}, as_of=as_of)


def spy_view(closes):
    return view_of({"SPY": make_series(bdays(len(closes)), closes)})


def volume_view(last_ratio, red=False, window_nulls=0, last_null=False, all_null=False):
    """21 aligned bars: the window sums to 1,000,000 a day (SPY 600k + QQQ
    400k); the last day's whole sum sits on SPY so the ratio is exact."""
    d = bdays(21)
    spy_vol = [600_000] * 20 + [None if last_null else round(last_ratio * 1_000_000)]
    qqq_vol = [400_000] * 20 + [0]
    for i in range(20 if all_null else window_nulls):
        spy_vol[i] = None
    spy_close = [100.0] * 20 + [99.0 if red else 101.0]
    return view_of({
        "SPY": make_series(d, spy_close, volumes=spy_vol),
        "QQQ": make_series(d, [300.0] * 21, volumes=qqq_vol),
    })


# ── Monitors: alignment carried from commit 1 ────────────────────

def test_align_no_common_dates_scores_null():
    spy = make_series(bdays(21), [100.0] * 21)
    qqq = make_series(bdays(21, end=date(2025, 6, 4)), [300.0] * 21)
    result = regime.volume(view_of({"SPY": spy, "QQQ": qqq}))
    assert (result["score"], result["stale"]) == (None, False)
    assert result["raw"]["droppedDates"] == 42
    assert "need ≥ 21" in result["detail"]


def test_vix_uses_intraday_bar_and_flags_partial():
    view = vix_view([20.0, 20.0, 23.0], as_of="2026-09-10T18:00:00+00:00",   # 14:00 EDT
                    dates=["2026-09-08", "2026-09-09", "2026-09-10"])
    result = regime.vix(view)
    assert result["score"] == 60
    assert result["raw"] == {"level": 23.0, "prevClose": 20.0, "changePct": pytest.approx(15.0),
                             "date": "2026-09-10", "partial": True}


def test_no_monitor_reads_vix_volume():
    quiet, loud = full_view(), full_view()
    quiet["tickers"]["^VIX"]["volume"] = [0] * 230
    loud["tickers"]["^VIX"]["volume"] = [9_000_000_000] * 230
    for name, m in MONITORS.items():
        assert m.fn(quiet) == m.fn(loud), name
        assert "^VIX" not in m.tickers or name == "vix"


# ── Monitors: scoring tables ─────────────────────────────────────

@pytest.mark.parametrize(
    "level, score",
    [(10, 95), (14.99, 95), (15, 80), (19.99, 80), (20, 60), (25, 40), (30, 20),
     (39.99, 20), (40, 20), (40.01, 5), (45, 5)],
)
def test_vix_score_table(level, score):
    assert regime.vix(vix_view([float(level), float(level)]))["score"] == score


@pytest.mark.parametrize(
    "prev, latest, score",
    [(20.0, 24.0, 60), (20.0, 24.01, 45), (37.0, 45.0, 0)],
    ids=["exactly_x1.20_no_penalty", "above_x1.20_minus_15", "vix45_spike_floor_0"],
)
def test_vix_spike_penalty(prev, latest, score):
    assert regime.vix(vix_view([prev, latest]))["score"] == score


def test_vix_single_bar_no_spike_check():
    result = regime.vix(vix_view([45.0]))
    assert result["score"] == 5
    assert (result["raw"]["prevClose"], result["raw"]["changePct"]) == (None, None)


def _flat_then(*segments, base=100.0, base_bars=200):
    closes = [base] * base_bars
    for value, count in segments:
        closes += value if isinstance(value, list) else [value] * count
    return closes


@pytest.mark.parametrize(
    "closes, score",
    [
        (_flat_then(([99.0 - i for i in range(20)], 0)), 5),      # below the 200, lows falling
        (_flat_then((90.0, 20)), 15),                               # below all three, lows flat
        (_flat_then((80.0, 60), (90.0, 10), base=120.0, base_bars=150), 25),  # bounce under the 200
        ([100.0 + 0.5 * i for i in range(220)], 95),                # above all three
        (_flat_then((120.0, 15), (113.0, 5)), 70),                  # dip under the 20 only
        (_flat_then((120.0, 15), (105.0, 5)), 45),                  # under the 50, over the 200
    ],
    ids=["lower_lows_5", "below_all_15", "bounce_below_200_25", "above_all_95",
         "below_20_only_70", "above_200_only_45"],
)
def test_spy_trend_score_table(closes, score):
    assert regime.spy_trend(spy_view(closes))["score"] == score


def test_spy_trend_close_equal_to_ema_counts_as_below(monkeypatch):
    monkeypatch.setattr(regime, "ema", lambda values, span: [values[-1]] * len(values))
    result = regime.spy_trend(spy_view([100.0] * 220))
    assert result["score"] == 15      # equal to all three EMAs, flat lows: "below all three"


@pytest.mark.parametrize(
    "ratio, red, score",
    [(1.19, False, 85), (1.2, False, 60), (1.79, True, 60), (1.8, True, 30), (2.5, True, 30),
     (2.51, True, 10), (1.8, False, 60), (2.0, False, 60), (2.01, False, 80)],
)
def test_volume_score_table(ratio, red, score):
    result = regime.volume(volume_view(ratio, red=red))
    assert result["score"] == score
    assert result["raw"]["ratio"] == ratio and result["raw"]["red"] is red


def test_volume_null_volume_rows():
    some = regime.volume(volume_view(1.2, window_nulls=5))    # excluded: mean still 1,000,000
    assert (some["score"], some["raw"]["ratio"]) == (60, 1.2)
    last = regime.volume(volume_view(1.2, last_null=True))
    assert last["score"] is None and "missing on" in last["detail"]
    empty = regime.volume(volume_view(1.2, all_null=True))
    assert empty["score"] is None and "no volume in the 20-day window" in empty["detail"]


def last_move_view(latest, n=2):
    """n aligned bars per ticker, every close 100 until the last bar, which
    is `latest[T]` — so the 1-bar and 5-bar % moves are latest − 100."""
    d = bdays(n)
    return view_of({t: make_series(d, [100.0] * (n - 1) + [v]) for t, v in latest.items()})


@pytest.mark.parametrize(
    "slope, score",
    [(1.01, 90), (1.0, 60), (-1.0, 60), (-1.01, 25)],
)
def test_breadth_slope_score_table(monkeypatch, slope, score):
    monkeypatch.setattr(regime, "slope_pct", lambda values: slope)
    view = view_of({t: make_series(bdays(20), [100.0] * 20) for t in ("RSP", "SPY")})
    result = regime.breadth(view)
    assert (result["score"], result["raw"]["slopePct"]) == (score, slope)


@pytest.mark.parametrize(
    "rsp_closes, score",
    [
        ([100.0 + 0.5 * i for i in range(20)], 90),   # fitted ratio change ≈ +9.5%
        ([100.0] * 20, 60),                            # flat ratio
        ([100.0 - 0.5 * i for i in range(20)], 25),   # ≈ −9.5%: a sign flip in slope_pct fails here
    ],
    ids=["rising_90", "flat_60", "falling_25"],
)
def test_breadth_real_closes_reach_every_band(rsp_closes, score):
    """Unpatched: proves slope_pct is wired into breadth, in the right sign."""
    d = bdays(20)
    view = view_of({"RSP": make_series(d, rsp_closes), "SPY": make_series(d, [100.0] * 20)})
    assert regime.breadth(view)["score"] == score


def test_breadth_ad_unavailable_is_null_not_zero():
    d = bdays(20)
    rsp = make_series(d, [100.0 + 0.5 * i for i in range(20)])   # equal weight pulling ahead
    spy = make_series(d, [100.0] * 20)
    result = regime.breadth(view_of({"RSP": rsp, "SPY": spy}))
    assert result["score"] == 90
    assert "adRatio" in result["raw"] and result["raw"]["adRatio"] is None
    assert "A/D unavailable" in result["detail"]


@pytest.mark.parametrize(
    "offensive_close, score",
    [(101.01, 90), (101.0, 65), (99.0, 65), (98.99, 30), (98.0, 30), (97.99, 10)],
    ids=["+1.01", "+1", "-1", "-1.01", "-2", "-2.01"],
)
def test_sector_rotation_score_table(offensive_close, score):
    latest = {t: offensive_close for t in regime.OFFENSIVE} | {t: 100.0 for t in regime.DEFENSIVE}
    result = regime.sector_rotation(last_move_view(latest, n=6))
    assert result["score"] == score
    assert result["raw"]["spreadPct"] == pytest.approx(offensive_close - 100.0)


@pytest.mark.parametrize(
    "tlt, gld, uup, spy, score",
    [
        (99.5, 99.5, 99.5, 99.5, 15),       # all four ≤ −0.5, edge inclusive
        (100.5, 100.5, 100.5, 100.0, 25),   # havens ≥ +0.5, edge inclusive
        (101.5, 100.5, 100.5, 99.0, 25),    # first match: 25 before 35
        (101.01, 100.0, 100.0, 99.5, 35),   # TLT > +1.0, SPY ≤ −0.5
        (101.0, 100.0, 100.0, 99.5, 65),    # TLT exactly +1.0 is not "sharply"
        (100.49, 99.51, 100.0, 97.0, 80),   # havens strictly inside ±0.5
        (100.5, 100.0, 100.0, 100.0, 65),   # +0.5 is up, not flat
        (99.0, 101.0, 100.0, 100.0, 65),    # mixed
    ],
    ids=["all_down_15", "havens_up_25", "havens_up_spy_down_25", "tlt_sharp_spy_down_35",
         "tlt_exactly_1_65", "havens_flat_80", "band_edge_up_65", "mixed_65"],
)
def test_cross_asset_score_table(tlt, gld, uup, spy, score):
    view = last_move_view({"TLT": tlt, "GLD": gld, "UUP": uup, "SPY": spy})
    assert regime.cross_asset(view)["score"] == score


def test_monitors_registry_names_weights_tickers():
    from monitors.quotes import CORE_TICKERS
    assert list(MONITORS) == ["vix", "breadth", "spy_trend", "sector_rotation", "volume", "cross_asset"]
    assert {n: m.weight for n, m in MONITORS.items()} == {
        "vix": 25, "breadth": 20, "spy_trend": 20, "sector_rotation": 15, "volume": 10, "cross_asset": 10,
    }
    assert sum(m.weight for m in MONITORS.values()) == 100
    assert MONITORS["sector_rotation"].tickers == ("XLK", "XLY", "XLF", "XLU", "XLP", "XLV")
    used = {t for m in MONITORS.values() for t in m.tickers}
    assert used <= set(CORE_TICKERS)
    assert not used & {"ES=F", "NQ=F", "CL=F", "GC=F"}   # night mode is 3.4


# ── Monitors: contract ───────────────────────────────────────────

def _insufficient_view(name):
    need = MIN_BARS[name] - 1
    return full_view(bars={t: need for t in MONITORS[name].tickers})


@pytest.mark.parametrize("name", list(MONITORS))
def test_monitor_insufficient_history_scores_null(name):
    result = MONITORS[name].fn(_insufficient_view(name))
    assert (result["score"], result["stale"]) == (None, False)
    assert f"need ≥ {MIN_BARS[name]}" in result["detail"]


@pytest.mark.parametrize("name", list(MONITORS))
def test_monitor_missing_ticker_scores_null_stale(name):
    tickers = MONITORS[name].tickers
    result = MONITORS[name].fn(full_view(drop=tickers, stale=tickers))
    assert (result["score"], result["stale"]) == (None, True)
    assert "unavailable" in result["detail"]


@pytest.mark.parametrize("state", ["ok", "null", "stale"])
@pytest.mark.parametrize("name", list(MONITORS))
def test_every_monitor_returns_the_contract(name, state):
    tickers = MONITORS[name].tickers
    view = {"ok": full_view(), "null": full_view(drop=tickers), "stale": full_view(stale=tickers)}[state]
    result = MONITORS[name].fn(view)
    assert set(result) == {"score", "raw", "detail", "stale"}
    score = result["score"]
    if state == "null":
        assert score is None
    else:
        assert type(score) is int and 0 <= score <= 100
    assert result["stale"] is (state == "stale")
    assert isinstance(result["detail"], str) and result["detail"]
    assert isinstance(result["raw"], dict)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("name", list(MONITORS))
def test_monitors_are_pure_repeat_call_identical(name):
    view = full_view()
    before = copy.deepcopy(view)
    assert MONITORS[name].fn(view) == MONITORS[name].fn(view)
    assert view == before
