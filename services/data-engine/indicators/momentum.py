"""
TradingFirm — Momentum indicators and relative strength.

Pure calculation functions. All functions are synchronous — async wrapping
is done at the caller level.

Conventions (recorded in docs/decisions.md — do not change silently):
  - rsi(): Wilder RSI seeded with a simple mean over the first `period`
    deltas, then Wilder-smoothed. Matches TA-Lib / TradingView. The first
    `period` rows are NaN (RSI is undefined until then).
  - macd(): EMA(fast) - EMA(slow), signal = EMA of the MACD line. No
    warm-up mask — this is a port of the frozen scanner/ reference and must
    keep producing the same numbers.
"""

import numpy as np
import pandas as pd

from indicators.moving_averages import ema


def _rs_to_rsi(avg_gain: float, avg_loss: float) -> float:
    """RSI from average gain / average loss. 100 * gain / (gain + loss) is
    algebraically 100 - 100 / (1 + RS) and handles avg_loss == 0 without a
    branch. A flat series (both zero) has no direction: return 50."""
    total = avg_gain + avg_loss
    if total == 0:
        return 50.0
    return 100.0 * avg_gain / total


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index (Wilder, SMA-seeded).

    Seed: average gain / loss = simple mean of the first `period` deltas.
    Then for each later bar:
        avg = (prev_avg * (period - 1) + current) / period

    Returns:
        Series of same length as input. First `period` rows NaN; all NaN if
        the input has `period` rows or fewer.
    """
    n = len(close)
    values = np.full(n, np.nan)
    if n <= period:
        return pd.Series(values, index=close.index)

    delta = close.diff()
    gains = delta.clip(lower=0).to_numpy()
    losses = (-delta).clip(lower=0).to_numpy()

    # Rows 1..period hold the first `period` deltas (row 0 is NaN from diff).
    avg_gain = float(np.mean(gains[1:period + 1]))
    avg_loss = float(np.mean(losses[1:period + 1]))
    values[period] = _rs_to_rsi(avg_gain, avg_loss)

    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        values[i] = _rs_to_rsi(avg_gain, avg_loss)

    return pd.Series(values, index=close.index)


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """
    MACD line, signal line, histogram.

    Same convention as the frozen scanner/ reference (scan.py, step3_filters.py):
    EMAs with adjust=False, no warm-up masking.

    Returns:
        DataFrame with columns `macd`, `signal`, `hist`, same index as input.
    """
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "hist": macd_line - signal_line,
    })


def relative_strength(
    stock_close: pd.Series,
    bench_close: pd.Series,
    period: int,
) -> float:
    """
    Relative strength versus a benchmark over the last `period` bars, in
    percentage points.

    rs = stock % return - benchmark % return

    +2.3 means the stock beat the benchmark by 2.3% over the window. The two
    series are inner-joined on index first so a missing bar on either side
    doesn't shift the window.

    Returns:
        float, or NaN if fewer than `period + 1` aligned rows exist or either
        starting price is 0.
    """
    aligned = pd.concat([stock_close, bench_close], axis=1, join="inner")
    if len(aligned) < period + 1:
        return float("nan")

    stock = aligned.iloc[:, 0]
    bench = aligned.iloc[:, 1]
    stock_start = stock.iloc[-1 - period]
    bench_start = bench.iloc[-1 - period]
    if stock_start == 0 or bench_start == 0:
        return float("nan")

    stock_ret = (stock.iloc[-1] / stock_start - 1) * 100
    bench_ret = (bench.iloc[-1] / bench_start - 1) * 100
    return float(stock_ret - bench_ret)


def check_52w_position(closes: pd.Series) -> float:
    """
    Calculate where current price sits in the 52-week range.

    Returns:
        Position as decimal (0.0 = at 52w low, 1.0 = at 52w high).
        Filter: reject if > 0.90 (top 10%) or < 0.10 (bottom 10%).
        NaN if the input is empty.
    """
    if len(closes) == 0:
        return float("nan")

    hi = closes.max()
    lo = closes.min()
    rng = hi - lo

    if rng <= 0:
        return 0.5

    return float((closes.iloc[-1] - lo) / rng)
