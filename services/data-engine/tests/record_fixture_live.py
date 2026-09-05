"""
TradingFirm — Fixture Recording Script (LIVE, MANUAL ONLY)

Records real OHLCV + stock-info data from yfinance into
tests/fixtures/{daily,hourly,info}/<TICKER>.json for FixtureProvider
to replay offline in tests.

This is a manual verification script, not a pytest suite — it makes
real network calls and must never run automatically or in CI. The
filename intentionally does NOT match pytest's `test_*.py` / `*_test.py`
discovery patterns.

Per AGENTS.md G6/G2: run with ONE ticker first, verify the output,
then run again with the full set.

Usage:
    python3 tests/record_fixture_live.py AAPL             # canary run
    python3 tests/record_fixture_live.py AAPL MSFT SPY    # full set
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.yfinance_provider import YFinanceProvider  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
INFO_DELAY = 2.0  # seconds between yf.Ticker calls — AGENTS.md yfinance rules


async def record(tickers: list[str]) -> None:
    provider = YFinanceProvider()

    for sub in ("daily", "hourly", "info"):
        (FIXTURES_DIR / sub).mkdir(parents=True, exist_ok=True)

    print(f"Downloading daily (1y) for {tickers}...")
    daily_bulk = await provider.download_daily(tickers, period="1y")
    for ticker in tickers:
        df = provider.extract_ticker_df(daily_bulk, ticker)
        if df is None:
            print(f"  SKIP {ticker}: no daily data returned")
            continue
        path = FIXTURES_DIR / "daily" / f"{ticker}.json"
        df.to_json(path, orient="table")
        print(f"  OK   {ticker}: {len(df)} daily rows -> {path}")

    print(f"Downloading hourly (3mo) for {tickers}...")
    hourly_bulk = await provider.download_hourly(tickers, period="3mo")
    for ticker in tickers:
        df = provider.extract_ticker_df(hourly_bulk, ticker)
        if df is None:
            print(f"  SKIP {ticker}: no hourly data returned")
            continue
        path = FIXTURES_DIR / "hourly" / f"{ticker}.json"
        df.to_json(path, orient="table")
        print(f"  OK   {ticker}: {len(df)} hourly rows -> {path}")

    print(f"Fetching stock info for {tickers} ({INFO_DELAY}s between calls)...")
    for i, ticker in enumerate(tickers):
        if i > 0:
            await asyncio.sleep(INFO_DELAY)
        try:
            info = await provider.get_stock_info(ticker)
        except Exception as e:
            print(f"  SKIP {ticker}: get_stock_info failed: {e}")
            continue
        path = FIXTURES_DIR / "info" / f"{ticker}.json"
        path.write_text(json.dumps(info, indent=2))
        print(f"  OK   {ticker}: {path}")


if __name__ == "__main__":
    tickers = sys.argv[1:]
    if not tickers:
        print(__doc__)
        sys.exit(1)
    asyncio.run(record(tickers))
