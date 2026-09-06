"""
TradingFirm — Sector → SPDR sector ETF map (Part 1.7).

Static dict, no I/O. Keys are the 11 sector names yfinance's `Ticker.info`
returns (the same strings the scanner writes into `data_engine.stocks.sector`).
Values are the SPDR Select Sector ETFs used as the sector benchmark for
relative strength.
"""

SECTOR_ETFS: dict[str, str] = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
}

_LOOKUP = {name.casefold(): etf for name, etf in SECTOR_ETFS.items()}


def sector_etf(sector: str | None) -> str | None:
    """
    ETF ticker for a yfinance sector name, or None if the sector is unknown.

    Case and surrounding whitespace are ignored (" technology " → "XLK").
    None or empty input → None. "Other" (the scanner's placeholder) → None.
    """
    if not sector:
        return None
    return _LOOKUP.get(sector.strip().casefold())
