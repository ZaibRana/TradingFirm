"""
TradingFirm — Volume and liquidity indicators.

Pure calculation functions. All functions are synchronous — async wrapping
is done at the caller level.
"""

import pandas as pd


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
        RVOL ratio (e.g., 1.5 means 50% above average). 0.0 if fewer than
        `lookback + 1` bars are available (kept for the scanner's filters).
    """
    if len(volumes) < lookback + 1:
        return 0.0

    avg_vol = float(volumes.iloc[-(lookback + 1):-1].mean())
    if avg_vol <= 0:
        return 0.0

    projected_vol = last_volume * scale_factor
    return projected_vol / avg_vol


def avg_dollar_volume(
    close: pd.Series,
    volume: pd.Series,
    lookback: int = 20,
) -> float:
    """
    Average dollar volume over the last `lookback` bars — a liquidity gauge.

    adv = mean(close * volume) over the window

    Returns:
        float, or NaN if fewer than `lookback` bars are available.
    """
    if len(close) < lookback or len(volume) < lookback:
        return float("nan")

    window = close.iloc[-lookback:] * volume.iloc[-lookback:]
    return float(window.mean())
