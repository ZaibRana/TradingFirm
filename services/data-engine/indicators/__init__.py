"""
TradingFirm — Indicator package.

Import from the package, not the submodules: the module split
(moving_averages / volatility / momentum / volume) is an implementation
detail. Every public function is re-exported here.
"""

from indicators.momentum import check_52w_position, macd, relative_strength, rsi
from indicators.moving_averages import aggregate_4h, ema
from indicators.volatility import calc_atr, calc_atrp, extension, gap
from indicators.volume import avg_dollar_volume, calc_rvol

__all__ = [
    "aggregate_4h",
    "avg_dollar_volume",
    "calc_atr",
    "calc_atrp",
    "calc_rvol",
    "check_52w_position",
    "ema",
    "extension",
    "gap",
    "macd",
    "relative_strength",
    "rsi",
]
