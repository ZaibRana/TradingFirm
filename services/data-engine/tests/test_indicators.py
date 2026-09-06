"""
Tests for the indicators package (Part 1.5).

Every expectation is hand-computed on a tiny frame with a small period so it
can be checked on paper. Fractions are left as fractions on purpose — they
show the derivation. No network, no fixtures, no I/O.

Conventions under test (docs/decisions.md, Part 1.5):
  - rsi(): SMA-seeded Wilder, first `period` rows NaN
  - macd(): reference convention, no warm-up mask
  - divisions never return inf (extension on ATR 0, gap on prev close 0)
  - empty input: series functions return empty, scalar functions return NaN,
    calc_rvol keeps 0.0
"""

import numpy as np
import pandas as pd
import pytest

import indicators
from indicators import (
    aggregate_4h,
    avg_dollar_volume,
    calc_atr,
    calc_atrp,
    calc_rvol,
    check_52w_position,
    ema,
    extension,
    gap,
    macd,
    relative_strength,
    rsi,
)

# ── Shared tiny frames ────────────────────────────────────────────────────

# 5 bars used by the ATR family. True ranges by hand:
#   bar0: h-l=2, no prev close             -> TR 2
#   bar1: h-l=3, |12-9|=3, |9-9|=0         -> TR 3
#   bar2: h-l=4, |11-11|=0, |7-11|=4       -> TR 4
#   bar3: h-l=3, |13-8|=5, |10-8|=2        -> TR 5
#   bar4: h-l=3, |12-12|=0, |9-12|=3       -> TR 3
HIGH = pd.Series([10.0, 12.0, 11.0, 13.0, 12.0])
LOW = pd.Series([8.0, 9.0, 7.0, 10.0, 9.0])
CLOSE = pd.Series([9.0, 11.0, 8.0, 12.0, 10.0])

EMPTY = pd.Series([], dtype=float)
NAN5 = pd.Series([np.nan] * 5)


def _all_nan(x) -> bool:
    arr = x.to_numpy() if hasattr(x, "to_numpy") else np.asarray(x, dtype=float)
    return bool(np.isnan(arr.astype(float)).all())


# ── Happy path: moved functions ───────────────────────────────────────────


def test_ema_matches_hand_computed():
    # span=3 -> alpha=0.5, adjust=False: e_t = 0.5*x_t + 0.5*e_{t-1}
    out = ema(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    np.testing.assert_allclose(out.to_numpy(), [1.0, 1.5, 2.25, 3.125, 4.0625])


def test_calc_atr_matches_hand_computed():
    # rolling(3).mean() over TR = [2, 3, 4, 5, 3]
    out = calc_atr(HIGH, LOW, CLOSE, period=3)
    np.testing.assert_allclose(out.to_numpy(), [np.nan, np.nan, 3.0, 4.0, 4.0])


def test_calc_atrp_matches_hand_computed():
    # last ATR 4 / last close 10 * 100
    assert calc_atrp(HIGH, LOW, CLOSE, period=3) == pytest.approx(40.0)


def test_calc_rvol_matches_hand_computed():
    # lookback=3 averages the 3 bars *before* the last one: (200+300+400)/3 = 300.
    # The series' own last value (999) is ignored; last_volume is passed separately.
    volumes = pd.Series([100.0, 200.0, 300.0, 400.0, 999.0])
    assert calc_rvol(volumes, last_volume=600.0, lookback=3) == pytest.approx(2.0)
    assert calc_rvol(volumes, last_volume=600.0, lookback=3, scale_factor=2.0) == pytest.approx(4.0)


def test_aggregate_4h_groups_into_4h_blocks():
    # hours 9,10,11 -> block 2; hours 12,13 -> block 3 (hour // 4)
    idx = pd.to_datetime([
        "2026-01-05 09:00", "2026-01-05 10:00", "2026-01-05 11:00",
        "2026-01-05 12:00", "2026-01-05 13:00",
    ])
    hourly = pd.DataFrame({
        "Open": [1.0, 2.0, 3.0, 7.0, 8.0],
        "High": [5.0, 6.0, 4.0, 9.0, 10.0],
        "Low": [0.5, 1.0, 2.0, 6.0, 5.0],
        "Close": [2.0, 3.0, 1.0, 8.0, 9.0],
        "Volume": [10, 20, 30, 40, 50],
    }, index=idx)
    out = aggregate_4h(hourly)
    assert list(out.index) == [0, 1]
    assert out.loc[0].tolist() == [1.0, 6.0, 0.5, 1.0, 60]
    assert out.loc[1].tolist() == [7.0, 10.0, 5.0, 9.0, 90]


def test_check_52w_position_matches_hand_computed():
    # hi 40, lo 10, range 30, last 22 -> (22-10)/30
    assert check_52w_position(pd.Series([10.0, 20.0, 30.0, 40.0, 22.0])) == pytest.approx(0.4)


# ── Happy path: new functions ─────────────────────────────────────────────


def test_rsi_matches_hand_computed():
    # period=3, closes 10,11,10,12,11,13,12 -> deltas +1,-1,+2,-1,+2,-1
    # seed (rows 1..3): avg_gain=(1+0+2)/3=1, avg_loss=(0+1+0)/3=1/3 -> 100*1/(4/3)=75
    # row4: g=(1*2+0)/3=2/3, l=(1/3*2+1)/3=5/9 -> 100*(6/9)/(11/9)=600/11
    # row5: g=(2/3*2+2)/3=10/9, l=(5/9*2+0)/3=10/27 -> 100*(30/27)/(40/27)=75
    # row6: g=(10/9*2+0)/3=20/27, l=(10/27*2+1)/3=47/81 -> 100*(60/81)/(107/81)=6000/107
    out = rsi(pd.Series([10.0, 11.0, 10.0, 12.0, 11.0, 13.0, 12.0]), period=3)
    np.testing.assert_allclose(
        out.to_numpy(),
        [np.nan, np.nan, np.nan, 75.0, 600 / 11, 75.0, 6000 / 107],
    )


def test_macd_matches_hand_computed():
    # close 1..5, fast=2 (alpha 2/3), slow=3 (alpha 1/2), signal=2 (alpha 2/3)
    # ema2:  1, 5/3, 23/9, 95/27, 365/81
    # ema3:  1, 3/2, 9/4, 25/8, 65/16
    # macd:  0, 1/6, 11/36, 85/216, 575/1296
    # sig:   0, 1/9, 13/54, 37/108, 797/1944
    # hist:  0, 1/18, 7/108, 11/216, 131/3888
    out = macd(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), fast=2, slow=3, signal=2)
    assert list(out.columns) == ["macd", "signal", "hist"]
    np.testing.assert_allclose(out["macd"].to_numpy(), [0, 1 / 6, 11 / 36, 85 / 216, 575 / 1296])
    np.testing.assert_allclose(out["signal"].to_numpy(), [0, 1 / 9, 13 / 54, 37 / 108, 797 / 1944])
    np.testing.assert_allclose(out["hist"].to_numpy(), [0, 1 / 18, 7 / 108, 11 / 216, 131 / 3888])


def test_extension_matches_hand_computed():
    out = extension(
        close=pd.Series([10.0, 12.0, 9.0]),
        ma=pd.Series([8.0, 10.0, 10.0]),
        atr=pd.Series([1.0, 2.0, 0.5]),
    )
    np.testing.assert_allclose(out.to_numpy(), [2.0, 1.0, -2.0])


def test_relative_strength_matches_hand_computed():
    # stock +21% over 2 bars, bench +10% -> +11 percentage points
    stock = pd.Series([100.0, 110.0, 121.0])
    bench = pd.Series([200.0, 210.0, 220.0])
    assert relative_strength(stock, bench, period=2) == pytest.approx(11.0)


def test_relative_strength_aligns_on_index():
    # bench is missing 01-02; the inner join must drop stock's 999 there so the
    # window is [100, 110, 121] vs [200, 210, 220] -> +11, same as above.
    stock = pd.Series(
        [100.0, 999.0, 110.0, 121.0],
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]),
    )
    bench = pd.Series(
        [200.0, 210.0, 220.0],
        index=pd.to_datetime(["2026-01-01", "2026-01-03", "2026-01-04"]),
    )
    assert relative_strength(stock, bench, period=2) == pytest.approx(11.0)


def test_avg_dollar_volume_matches_hand_computed():
    # lookback=3 -> last three: 2*10 + 3*20 + 4*30 = 200, /3. First row excluded.
    close = pd.Series([1.0, 2.0, 3.0, 4.0])
    volume = pd.Series([100.0, 10.0, 20.0, 30.0])
    assert avg_dollar_volume(close, volume, lookback=3) == pytest.approx(200 / 3)


def test_gap_matches_hand_computed():
    # (open - prev_close)/prev_close*100: NaN, (12-11)/11, (9-10)/10, (11-10)/10
    out = gap(
        open_=pd.Series([10.0, 12.0, 9.0, 11.0]),
        close=pd.Series([11.0, 10.0, 10.0, 12.0]),
    )
    np.testing.assert_allclose(out.to_numpy(), [np.nan, 100 / 11, -10.0, 10.0])


def test_package_exports_all_public_names():
    expected = [
        "Zone", "aggregate_4h", "avg_dollar_volume", "calc_atr", "calc_atrp",
        "calc_rvol", "check_52w_position", "ema", "extension", "fractal_swings",
        "gap", "macd", "merge_levels", "relative_strength", "rsi", "score_zones",
        "support_resistance", "volume_nodes",
    ]
    assert sorted(indicators.__all__) == expected
    for name in expected:
        assert callable(getattr(indicators, name)), name


# ── Failure branches: empty input ─────────────────────────────────────────


@pytest.mark.parametrize(
    "fn",
    [
        pytest.param(lambda e: ema(e, 3), id="ema"),
        pytest.param(lambda e: calc_atr(e, e, e, period=3), id="calc_atr"),
        pytest.param(lambda e: rsi(e, period=3), id="rsi"),
        pytest.param(lambda e: macd(e), id="macd"),
        pytest.param(lambda e: extension(e, e, e), id="extension"),
        pytest.param(lambda e: gap(e, e), id="gap"),
    ],
)
def test_series_functions_empty_input_return_empty(fn):
    out = fn(EMPTY)
    assert len(out) == 0


def test_calc_atrp_empty_returns_nan():
    assert np.isnan(calc_atrp(EMPTY, EMPTY, EMPTY, period=3))


def test_check_52w_position_empty_returns_nan():
    assert np.isnan(check_52w_position(EMPTY))


def test_calc_rvol_empty_returns_zero():
    assert calc_rvol(EMPTY, last_volume=100.0, lookback=3) == 0.0


def test_relative_strength_empty_returns_nan():
    assert np.isnan(relative_strength(EMPTY, EMPTY, period=2))


def test_avg_dollar_volume_empty_returns_nan():
    assert np.isnan(avg_dollar_volume(EMPTY, EMPTY, lookback=3))


def test_aggregate_4h_empty_returns_empty():
    hourly = pd.DataFrame(
        columns=["Open", "High", "Low", "Close", "Volume"],
        index=pd.DatetimeIndex([]),
    )
    assert len(aggregate_4h(hourly)) == 0


# ── Failure branches: shorter than period / lookback ──────────────────────


def test_calc_atr_shorter_than_period_all_nan():
    out = calc_atr(HIGH.iloc[:2], LOW.iloc[:2], CLOSE.iloc[:2], period=3)
    assert len(out) == 2 and _all_nan(out)


def test_calc_atrp_shorter_than_period_returns_nan():
    assert np.isnan(calc_atrp(HIGH.iloc[:2], LOW.iloc[:2], CLOSE.iloc[:2], period=3))


def test_rsi_shorter_than_period_all_nan():
    # exactly `period` rows is still too short: RSI needs period deltas = period+1 closes
    out = rsi(pd.Series([10.0, 11.0, 12.0]), period=3)
    assert len(out) == 3 and _all_nan(out)


def test_calc_rvol_shorter_than_lookback_returns_zero():
    # lookback=3 needs 4 bars (3 prior + the current one)
    assert calc_rvol(pd.Series([100.0, 200.0, 300.0]), last_volume=600.0, lookback=3) == 0.0


def test_avg_dollar_volume_shorter_than_lookback_returns_nan():
    assert np.isnan(avg_dollar_volume(pd.Series([1.0, 2.0]), pd.Series([10.0, 10.0]), lookback=3))


def test_relative_strength_fewer_than_period_plus_one_returns_nan():
    assert np.isnan(relative_strength(pd.Series([100.0, 110.0]), pd.Series([200.0, 210.0]), period=2))


# ── Failure branches: all-NaN columns ─────────────────────────────────────


@pytest.mark.parametrize(
    "fn",
    [
        pytest.param(lambda s: ema(s, 3), id="ema"),
        pytest.param(lambda s: calc_atr(s, s, s, period=3), id="calc_atr"),
        pytest.param(lambda s: calc_atrp(s, s, s, period=3), id="calc_atrp"),
        pytest.param(lambda s: rsi(s, period=3), id="rsi"),
        pytest.param(lambda s: macd(s, fast=2, slow=3, signal=2), id="macd"),
        pytest.param(lambda s: extension(s, s, s), id="extension"),
        pytest.param(lambda s: gap(s, s), id="gap"),
        pytest.param(lambda s: check_52w_position(s), id="check_52w_position"),
    ],
)
def test_all_nan_price_column_propagates_nan(fn):
    assert _all_nan(fn(NAN5))


def test_all_nan_volume_column_returns_nan():
    assert np.isnan(calc_rvol(NAN5, last_volume=100.0, lookback=3))
    assert np.isnan(avg_dollar_volume(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), NAN5, lookback=3))


# ── Failure branches: divide guards and alignment ─────────────────────────


def test_extension_zero_atr_returns_nan():
    out = extension(
        close=pd.Series([10.0, 12.0]),
        ma=pd.Series([8.0, 10.0]),
        atr=pd.Series([1.0, 0.0]),
    )
    assert out.iloc[0] == pytest.approx(2.0)
    assert np.isnan(out.iloc[1]) and not np.isinf(out.iloc[1])


def test_extension_nan_atr_returns_nan():
    out = extension(
        close=pd.Series([10.0, 12.0]),
        ma=pd.Series([8.0, 10.0]),
        atr=pd.Series([1.0, np.nan]),
    )
    assert out.iloc[0] == pytest.approx(2.0)
    assert np.isnan(out.iloc[1])


def test_relative_strength_no_index_overlap_returns_nan():
    stock = pd.Series([100.0, 110.0, 121.0], index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]))
    bench = pd.Series([200.0, 210.0, 220.0], index=pd.to_datetime(["2026-02-01", "2026-02-02", "2026-02-03"]))
    assert np.isnan(relative_strength(stock, bench, period=2))


def test_gap_zero_prev_close_returns_nan():
    # prev close of row 1 is 0 -> NaN, never inf; row 2 is a normal gap
    out = gap(
        open_=pd.Series([10.0, 12.0, 9.0]),
        close=pd.Series([0.0, 10.0, 10.0]),
    )
    assert np.isnan(out.iloc[1]) and not np.isinf(out.iloc[1])
    assert out.iloc[2] == pytest.approx(-10.0)
