"""
TradingFirm — MANUAL live recorder for earnings-date fixtures (Part 2.3).

⚠️  LIVE API SCRIPT. Never runs in CI, never imported by a test. Run it
deliberately, one ticker first (G6 canary), and read the output before
recording more.

    python tests/record_earnings_live.py --source yfinance AAPL
    python tests/record_earnings_live.py --source yfinance MSFT SPY
    python tests/record_earnings_live.py --source alphavantage AAPL

yfinance: one Ticker.get_earnings_dates() call per ticker through the real
YFinanceProvider (the delay and the rate-limit mapping come with it), 2 s
apart. Writes tests/fixtures/earnings_dates/<T>.json — `to_json(orient=
"table")` for a lossless round-trip, or the literal `null` when yfinance
has no earnings for the ticker (an ETF), which is a recorded fact and not
the same as a missing file.

alphavantage: ONE call. The free tier allows 25 per day; do not loop.
Writes tests/fixtures/alphavantage/<T>_earnings.json.

Prints shape only — never a key, never a URL (docs/specs/2.3.md 13).
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yfinance  # noqa: E402
import yfinance.exceptions  # noqa: E402

from config import settings  # noqa: E402
from providers.context.alphavantage_client import AlphaVantageClient  # noqa: E402
from providers.yfinance_provider import YFinanceProvider  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DELAY = 2.0


async def record_yfinance(tickers: list[str]) -> None:
    out = FIXTURES / "earnings_dates"
    out.mkdir(parents=True, exist_ok=True)

    print(f"yfinance {yfinance.__version__}")
    print(f"YFRateLimitError present: {hasattr(yfinance.exceptions, 'YFRateLimitError')}")

    provider = YFinanceProvider()
    for i, ticker in enumerate(tickers):
        if i:
            await asyncio.sleep(DELAY)
        print(f"\n── {ticker} ──")
        df = await provider.get_earnings_dates(ticker)
        path = out / f"{ticker}.json"
        if df is None:
            path.write_text("null")
            print("  result: None (no earnings feed for this ticker) → recorded as null")
            continue
        print(f"  shape: {df.shape}")
        print(f"  columns: {list(df.columns)}")
        print(f"  index name/tz: {df.index.name} / {df.index.tz}")
        print(f"  first 3 index entries: {[str(x) for x in df.index[:3]]}")
        print(f"  dtypes: {dict(df.dtypes.astype(str))}")
        df.to_json(path, orient="table")
        print(f"  wrote {path.relative_to(Path.cwd())} ({path.stat().st_size} bytes)")


async def record_alphavantage(tickers: list[str]) -> None:
    if len(tickers) != 1:
        raise SystemExit("alphavantage: record ONE ticker per run (25 calls/day free cap)")
    out = FIXTURES / "alphavantage"
    out.mkdir(parents=True, exist_ok=True)

    ticker = tickers[0]
    client = AlphaVantageClient(settings.alphavantage_api_key)
    print(f"key configured: {client.configured}")  # shape only, never the value
    try:
        body = await client.get("EARNINGS", ticker)
    finally:
        await client.aclose()

    print(f"\n── {ticker} ──")
    print(f"  top-level keys: {list(body)}")
    quarterly = body.get("quarterlyEarnings") or []
    annual = body.get("annualEarnings") or []
    print(f"  quarterlyEarnings: {len(quarterly)} items, annualEarnings: {len(annual)}")
    if quarterly:
        print(f"  first quarterly item keys: {list(quarterly[0])}")
        print(f"  first quarterly item: {quarterly[0]}")
        print(f"  reportTime present: {'reportTime' in quarterly[0]}")

    # Trim to the newest 12 quarters: fixtures stay small, three years is
    # more than the 8 reactions the reader ever needs.
    if len(quarterly) > 12:
        body["quarterlyEarnings"] = quarterly[:12]
    body.pop("annualEarnings", None)
    path = out / f"{ticker}_earnings.json"
    path.write_text(json.dumps(body, indent=1))
    print(f"  wrote {path.relative_to(Path.cwd())} ({path.stat().st_size} bytes)")


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != "--source":
        raise SystemExit(__doc__)
    source, tickers = args[1], [t.upper() for t in args[2:]]
    if source == "yfinance":
        asyncio.run(record_yfinance(tickers))
    elif source == "alphavantage":
        asyncio.run(record_alphavantage(tickers))
    else:
        raise SystemExit(f"unknown source: {source}")


if __name__ == "__main__":
    main()
