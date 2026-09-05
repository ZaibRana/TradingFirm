"""
TradingFirm — Provider Factory Tests

Verifies get_provider() maps names to the right DataProvider class
without instantiating anything that touches the network: the fixture
branch is pure disk, and the yfinance branch is only checked for the
error path (unknown name), never constructed.
"""

import pytest

from providers import get_provider
from providers.fixture_provider import FixtureProvider


def test_get_provider_fixture_returns_fixture_provider():
    provider = get_provider("fixture")

    assert isinstance(provider, FixtureProvider)
    assert provider.provider_name == "fixture"


def test_get_provider_unknown_name_raises():
    with pytest.raises(ValueError, match="Unknown data provider: 'nope'"):
        get_provider("nope")
