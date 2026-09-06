"""
TradingFirm — Moving averages and bar resampling.

Pure calculation functions. Works with pandas Series/DataFrames from stored
bars. All functions are synchronous — async wrapping is done at the caller.
"""

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
