"""
TradingFirm — Volatility, extension and gap indicators.

Pure calculation functions. All functions are synchronous — async wrapping
is done at the caller level. Divisions never return inf: a zero or NaN
divisor yields NaN at that row.
"""

import numpy as np
import pandas as pd


def calc_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Average True Range (ATR).

    True Range = max of:
      - High - Low (current bar range)
      - |High - Previous Close|
      - |Low - Previous Close|

    ATR = Simple Moving Average of True Range over `period` bars.
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_atrp(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> float:
    """
    ATR as a Percentage of price (ATRP).

    ATRP = (ATR / Last Close) * 100

    Used to normalize volatility across different price levels.
    Filter: 2.5% – 6.0% is the sweet spot for day trading.

    Returns:
        ATRP percentage, or NaN if the input is empty or the ATR is not
        yet defined.
    """
    if len(close) == 0:
        return float("nan")

    atr_series = calc_atr(high, low, close, period)
    last_atr = atr_series.iloc[-1]
    last_close = close.iloc[-1]

    if np.isnan(last_atr) or last_close == 0:
        return float("nan")

    return (last_atr / last_close) * 100


def extension(close: pd.Series, ma: pd.Series, atr: pd.Series) -> pd.Series:
    """
    Extension of price from a moving average, in ATR units.

    extension = (close - ma) / atr

    "Already too high" in one number: +2 means the close sits two ATRs above
    the average. Caller passes the moving average it cares about (EMA 20 or
    EMA 50) and the matching ATR series.

    Returns:
        Series of same length as input. NaN wherever ATR is 0 or NaN.
    """
    safe_atr = atr.where(atr != 0)
    return (close - ma) / safe_atr


def gap(open_: pd.Series, close: pd.Series) -> pd.Series:
    """
    Opening gap versus the previous close, in percent.

    gap = (open - prev_close) / prev_close * 100

    The full series is the gap history; `.iloc[-1]` is today's gap.

    Returns:
        Series of same length as input. First row is NaN (no previous close);
        NaN wherever the previous close is 0.
    """
    prev_close = close.shift(1)
    prev_close = prev_close.where(prev_close != 0)
    return (open_ - prev_close) / prev_close * 100
