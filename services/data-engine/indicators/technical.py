"""
TradingFirm — Technical Indicator Calculations

Pure calculation functions for technical analysis indicators.
Works with pandas Series/DataFrames from yfinance data.
All functions are synchronous — async wrapping is done at the caller level.
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """
    Exponential Moving Average.

    Args:
        series: Price series (typically Close prices)
        period: EMA lookback period

    Returns:
        EMA series of same length as input
    """
    return series.ewm(span=period, adjust=False).mean()


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
        ATRP percentage, or NaN if calculation fails.
    """
    atr_series = calc_atr(high, low, close, period)
    last_atr = atr_series.iloc[-1]
    last_close = close.iloc[-1]

    if np.isnan(last_atr) or last_close == 0:
        return float("nan")

    return (last_atr / last_close) * 100


def calc_rvol(
    volumes: pd.Series,
    last_volume: float,
    lookback: int = 20,
    scale_factor: float = 1.0,
) -> float:
    """
    Relative Volume (RVOL).

    RVOL = Today's Volume / Average Volume over lookback period.

    For live market, pass a scale_factor to project partial-day
    volume to full-day equivalent:
        scale_factor = 390 / minutes_since_open

    Args:
        volumes: Historical daily volume series
        last_volume: Today's raw volume
        lookback: Number of prior days to average
        scale_factor: Multiplier for partial-day volume projection

    Returns:
        RVOL ratio (e.g., 1.5 means 50% above average)
    """
    if len(volumes) < lookback + 1:
        return 0.0

    avg_vol = float(volumes.iloc[-(lookback + 1):-1].mean())
    if avg_vol <= 0:
        return 0.0

    projected_vol = last_volume * scale_factor
    return projected_vol / avg_vol


def aggregate_4h(hourly_df: pd.DataFrame) -> pd.DataFrame:
    """
    Resample 1-hour candles into 4-hour candles.

    Groups by calendar date and 4-hour blocks (0-3, 4-7, 8-11, etc.)
    and aggregates OHLCV accordingly.

    Args:
        hourly_df: DataFrame with Open, High, Low, Close, Volume columns
                   and a DatetimeIndex

    Returns:
        4H OHLCV DataFrame with reset integer index
    """
    df = hourly_df.copy()
    df.index = pd.to_datetime(df.index)
    df["_blk"] = df.index.hour // 4
    df["_day"] = df.index.date

    grouped = df.groupby(["_day", "_blk"]).agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna()

    return grouped.reset_index(drop=True)


def check_52w_position(closes: pd.Series) -> float:
    """
    Calculate where current price sits in the 52-week range.

    Returns:
        Position as decimal (0.0 = at 52w low, 1.0 = at 52w high).
        Filter: reject if > 0.90 (top 10%) or < 0.10 (bottom 10%).
    """
    hi = closes.max()
    lo = closes.min()
    rng = hi - lo

    if rng <= 0:
        return 0.5

    return float((closes.iloc[-1] - lo) / rng)
