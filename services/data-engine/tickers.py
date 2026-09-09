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


def validate_ticker(ticker: str) -> str:
    """normalize_ticker() plus the shape check the context fetchers share
    (Part 2.1 Finnhub, Part 2.2 EDGAR): 1–5 letters, nothing else. Raises
    ValueError before any HTTP or cache access, so a bad key never reaches
    a provider or a Redis key."""
    t = normalize_ticker(ticker)
    if not t.isalpha() or not 1 <= len(t) <= 5:
        raise ValueError(f"Invalid ticker: {ticker!r}")
    return t
