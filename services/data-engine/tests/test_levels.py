"""
Tests for indicators/levels.py (Part 1.6).

Every expectation is hand-computed on a tiny synthetic series with known
pivots. No network, no fixtures, no I/O.

Conventions under test (docs/decisions.md, Part 1.6):
  - strict fractals; NaN high/low disqualifies the bar and its window
  - volume nodes bin by close over [min low, max high]; NaN close/volume skipped
  - running-mean merge, inclusive of merge_pct
  - rubric: +30 both swing methods, +25 volume node (not a method),
    +20 tested twice, +15 recent
  - split on last valid close; zone price == close is resistance
"""

import numpy as np
import pandas as pd
import pytest

from indicators import (
    Zone,
    fractal_swings,
    merge_levels,
    score_zones,
    support_resistance,
    volume_nodes,
)

# ── Shared synthetic series (17 bars, known pivots) ───────────────────────
#
# Swing highs (strict, wing 2):  idx 5 -> 110, idx 12 -> 108
# Swing lows  (strict, wing 2):  idx 2 -> 100, idx 8 -> 95, idx 14 -> 99.6
# Volume: all 1 except bar 13 (close 107) = 100, so with n_bins=15 over
# [95, 110] (width 1) the single top node is bin [107, 108) -> 107.5.
#
# Merge (0.5%, running mean):
#   95                        -> alone
#   99.6, 100  (0.4016%)      -> zone [99.6, 100], price 99.8
#   107.5, 108 (0.465%)       -> zone [107.5, 108], price 107.75
#   110                       -> alone (1.85% from 108)
#
# Scores with recent_bars=5 (recent = idx >= 12):
#   95      swing_low idx 8                 -> 0
#   99.8    2 swing lows, idx 14 recent     -> 20 + 15 = 35
#   107.75  swing_high idx 12 + volume      -> 25 + 15 = 40 (one method)
#   110     swing_high idx 5                -> 0
# Last close 105: support = [99.8, 95], resistance = [107.75, 110].

HIGH = pd.Series([103, 102, 101.5, 103, 105, 110, 106, 103, 102, 103, 104, 106, 108, 107, 103, 104, 106], dtype=float)
LOW = pd.Series([101, 100.5, 100, 100.8, 102, 106, 101, 99, 95, 98, 100, 102, 104, 100.5, 99.6, 101, 103], dtype=float)
CLOSE = pd.Series([102, 101, 100.5, 102, 104, 108, 102, 100, 96, 101, 103, 104, 106, 107, 100, 103, 105], dtype=float)
VOLUME = pd.Series([1] * 13 + [100] + [1] * 3, dtype=float)

EMPTY = pd.Series([], dtype=float)


def _prices(zones):
    return [z.price for z in zones]


def _run(close=CLOSE, **kw):
    kw.setdefault("n_bins", 15)
    kw.setdefault("top_nodes", 1)
    kw.setdefault("recent_bars", 5)
    return support_resistance(HIGH, LOW, close, VOLUME, **kw)


# ── Happy path: fractal_swings ────────────────────────────────────────────


def test_fractal_swings_finds_known_pivots():
    assert fractal_swings(HIGH, LOW) == ([5, 12], [2, 8, 14])


def test_fractal_swings_strict_ties_not_pivots():
    # A double top / double bottom at the exact same price is not a fractal.
    high = pd.Series([1, 2, 3, 3, 2, 1, 0], dtype=float)
    low = pd.Series([3, 2, 1, 1, 2, 3, 4], dtype=float)
    assert fractal_swings(high, low) == ([], [])


def test_fractal_swings_last_two_bars_never_pivots():
    # The extreme sits on the last bar, which has no right-hand window.
    high = pd.Series([1, 2, 3, 4, 5], dtype=float)
    low = pd.Series([5, 4, 3, 2, 1], dtype=float)
    assert fractal_swings(high, low) == ([], [])


# ── Happy path: volume_nodes ──────────────────────────────────────────────


def test_volume_nodes_picks_top_bins():
    # 4 bins of width 1 over [100, 104]; closes land one per bin.
    high = pd.Series([101, 102, 103, 104], dtype=float)
    low = pd.Series([100, 101, 102, 103], dtype=float)
    close = pd.Series([100.5, 101.5, 102.5, 103.5], dtype=float)
    volume = pd.Series([10, 40, 20, 30], dtype=float)
    out = volume_nodes(high, low, close, volume, n_bins=4, top_nodes=2)
    assert out == [101.5, 103.5]  # volume 40, then 30


# ── Happy path: merge_levels ──────────────────────────────────────────────


def test_merge_levels_groups_within_half_percent():
    # 100.4 vs 100 = 0.4% -> merged (mean 100.2).
    # 100.9 vs running mean 100.2 = 0.6986% -> new group, even though it is
    # 0.498% from 100.4 (a chain merge would have joined it).
    levels = [
        (100.0, "swing_low", 2),
        (105.0, "swing_high", 9),
        (100.9, "volume", None),
        (100.4, "swing_low", 5),
    ]
    groups = merge_levels(levels, merge_pct=0.5)
    assert [[lv[0] for lv in g] for g in groups] == [[100.0, 100.4], [100.9], [105.0]]


def test_merge_levels_does_not_merge_beyond_half_percent():
    groups = merge_levels([(100.0, "swing_low", 1), (100.6, "swing_low", 4)])
    assert len(groups) == 2


# ── Happy path: score_zones rubric ────────────────────────────────────────


@pytest.mark.parametrize(
    "group, expected",
    [
        pytest.param(
            [(100.0, "swing_low", 3)],
            Zone(100.0, 100.0, 100.0, 0, ("swing_low",), 1, False, False),
            id="swing_only_single_not_recent_0",
        ),
        pytest.param(
            [(100.0, "volume", None)],
            Zone(100.0, 100.0, 100.0, 25, (), 0, False, True),
            id="volume_only_25",
        ),
        pytest.param(
            [(100.0, "swing_low", 3), (100.3, "volume", None)],
            Zone(100.0, 100.3, 100.15, 25, ("swing_low",), 1, False, True),
            id="swing_plus_volume_is_one_method_25",
        ),
        pytest.param(
            [(100.0, "swing_high", 3), (100.3, "swing_low", 6)],
            Zone(100.0, 100.3, 100.15, 50, ("swing_high", "swing_low"), 2, False, False),
            id="two_swing_methods_tested_twice_50",
        ),
        pytest.param(
            [(100.0, "swing_high", 28), (100.3, "swing_low", 6), (100.2, "volume", None)],
            Zone(100.0, 100.3, 300.5 / 3, 90, ("swing_high", "swing_low"), 2, True, True),
            id="all_four_90",
        ),
    ],
)
def test_score_zones_rubric(group, expected):
    # n_bars=30, recent_bars=5 -> recent means index >= 25
    (zone,) = score_zones([group], n_bars=30, recent_bars=5)
    assert zone.price == pytest.approx(expected.price)
    assert zone == Zone(
        expected.low, expected.high, zone.price, expected.score,
        expected.methods, expected.tests, expected.recent, expected.volume_node,
    )


# ── Happy path: support_resistance ────────────────────────────────────────


def test_support_resistance_splits_by_last_close_and_ranks_by_score():
    out = _run()
    assert _prices(out["support"]) == pytest.approx([99.8, 95.0])
    assert [z.score for z in out["support"]] == [35, 0]
    assert _prices(out["resistance"]) == pytest.approx([107.75, 110.0])
    assert [z.score for z in out["resistance"]] == [40, 0]

    top = out["resistance"][0]
    assert top == Zone(107.5, 108.0, 107.75, 40, ("swing_high",), 1, True, True)

    # Zone price == last close -> resistance, not support. Moving the last
    # close onto the 107.75 zone changes only that bar's bin (still bin
    # [107,108), still the top node); swings use high/low only.
    close = CLOSE.copy()
    close.iloc[-1] = 107.75
    out = _run(close=close)
    assert _prices(out["support"]) == pytest.approx([99.8, 95.0])
    assert _prices(out["resistance"]) == pytest.approx([107.75, 110.0])


def test_support_resistance_caps_at_three_per_side():
    # No fractals (5 bars, middle bar is not an extreme). 10 bins of width 1
    # over [100, 110]; four closes with volume land in bins 0-3, the last
    # close (110) carries no volume. Four volume-only zones, all score 25,
    # so ranking falls to distance from the close: 103.5, 102.5, 101.5.
    px = pd.Series([100, 101, 102, 103, 110], dtype=float)
    volume = pd.Series([4, 3, 2, 1, 0], dtype=float)
    out = support_resistance(px, px, px, volume, n_bins=10, top_nodes=4)
    assert _prices(out["support"]) == pytest.approx([103.5, 102.5, 101.5])
    assert out["resistance"] == []


# ── Failure branches ──────────────────────────────────────────────────────


def test_support_resistance_empty_returns_empty_lists():
    assert support_resistance(EMPTY, EMPTY, EMPTY, EMPTY) == {"support": [], "resistance": []}


def test_fractal_swings_fewer_than_five_bars_returns_empty():
    high = pd.Series([1, 5, 1, 0], dtype=float)
    low = pd.Series([1, 0, 1, 5], dtype=float)
    assert fractal_swings(high, low) == ([], [])


def test_support_resistance_all_nan_returns_empty_lists():
    nan5 = pd.Series([np.nan] * 5)
    assert support_resistance(nan5, nan5, nan5, nan5) == {"support": [], "resistance": []}


def test_volume_nodes_zero_volume_returns_empty():
    zero = pd.Series([0, 0, 0, 0, 0], dtype=float)
    assert volume_nodes(HIGH.iloc[:5], LOW.iloc[:5], CLOSE.iloc[:5], zero) == []


def test_volume_nodes_flat_price_single_node():
    flat = pd.Series([50, 50, 50], dtype=float)
    assert volume_nodes(flat, flat, flat, pd.Series([1, 2, 3], dtype=float)) == [50.0]


def test_support_resistance_mismatched_lengths_raises():
    with pytest.raises(ValueError):
        support_resistance(HIGH, LOW.iloc[:-1], CLOSE, VOLUME)


def test_support_resistance_returns_fewer_than_three_when_scarce():
    # One zone in total (flat series -> one volume node at 50). Its price
    # equals the last close, so it lands on the resistance side.
    flat = pd.Series([50, 50, 50], dtype=float)
    out = support_resistance(flat, flat, flat, pd.Series([1, 2, 3], dtype=float))
    assert out["support"] == []
    assert out["resistance"] == [Zone(50.0, 50.0, 50.0, 25, (), 0, False, True)]


def test_support_resistance_is_deterministic():
    assert _run() == _run()


def test_fractal_swings_nan_bar_disqualifies_neighbours():
    # Without NaN: swing highs at 2 and 6, swing low at 4.
    high = pd.Series([1, 2, 5, 2, 1, 3, 9, 3, 1], dtype=float)
    low = pd.Series([0, 1, 4, 1, 0, 2, 8, 2, 0], dtype=float)
    assert fractal_swings(high, low) == ([2, 6], [4])

    # NaN high on bar 3 disqualifies bars 1-5: the swing high at 2 and the
    # swing low at 4 vanish; bar 6's window (4-8) is clean and survives.
    high.iloc[3] = np.nan
    assert fractal_swings(high, low) == ([6], [])


def test_volume_nodes_skips_nan_volume():
    high = pd.Series([101, 102, 103, 104], dtype=float)
    low = pd.Series([100, 101, 102, 103], dtype=float)
    close = pd.Series([100.5, 101.5, 102.5, 103.5], dtype=float)
    volume = pd.Series([10, np.nan, 20, 30], dtype=float)
    # Bin 1 (the 40 in the happy-path test) is now NaN and dropped.
    assert volume_nodes(high, low, close, volume, n_bins=4, top_nodes=2) == [103.5, 102.5]
