"""
TradingFirm — Ticker key normalization.

One function, imported by every module that writes or reads a ticker-keyed
row or cache key (G1.5): the API endpoints in main.py, the scan pipeline,
and the context fetchers. It lives here rather than in main.py so library
modules can import it without importing the FastAPI entrypoint.
"""


def normalize_ticker(ticker: str) -> str:
    """Canonical ticker key form used everywhere ohlcv_bars / stocks /
    cache keys are written or read: upper-cased, whitespace-stripped."""
    return ticker.upper().strip()
