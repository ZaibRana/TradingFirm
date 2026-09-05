"""
TradingFirm — Data Provider Factory

Returns the appropriate DataProvider based on the DATA_PROVIDER env var.
Currently supports: 'yfinance' (dev), 'fixture' (offline tests only —
replays tests/fixtures/, no network). Future: 'fmp', 'polygon'.
"""

from providers.base import DataProvider


def get_provider(name: str = "yfinance") -> DataProvider:
    """
    Factory function to instantiate a data provider by name.

    Args:
        name: Provider identifier ('yfinance', 'fixture', 'fmp', etc.)

    Returns:
        DataProvider instance

    Raises:
        ValueError: If provider name is not recognized
    """
    if name == "yfinance":
        from providers.yfinance_provider import YFinanceProvider
        return YFinanceProvider()
    elif name == "fixture":
        from providers.fixture_provider import FixtureProvider
        return FixtureProvider()
    else:
        raise ValueError(
            f"Unknown data provider: '{name}'. "
            f"Supported: 'yfinance', 'fixture'. "
            f"Set DATA_PROVIDER env var to a valid provider."
        )
