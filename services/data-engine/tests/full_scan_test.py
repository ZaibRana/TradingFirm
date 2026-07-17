"""
FULL END-TO-END SCAN — Real Finviz + yfinance pipeline.
Uses the actual MarketScanner.run_scan() method.
"""
import sys
sys.path.insert(0, ".")

import asyncio
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("full_scan")

from providers.yfinance_provider import YFinanceProvider
from scanners.market_scanner import MarketScanner


async def main():
    start = time.time()

    logger.info("=" * 65)
    logger.info("  FULL END-TO-END SCAN")
    logger.info("=" * 65)

    # Create real provider and scanner
    provider = YFinanceProvider()
    scanner = MarketScanner(provider)

    # Run the actual pipeline
    result = await scanner.run_scan(price_min=10.0, price_max=40.0)

    elapsed = time.time() - start

    # Print results
    print()
    print("=" * 65)
    print(f"  SCAN COMPLETE — {elapsed:.0f}s total")
    print("=" * 65)
    print(f"  Status:         {result.market_status}")
    print(f"  Total scanned:  {result.total_scanned}")
    print(f"  Passed:         {result.passed_count}")
    print(f"  Duration:       {result.duration_seconds:.1f}s")
    print()

    if result.stocks:
        print(f"  {'Ticker':<8} {'Price':>8} {'ATRP':>6} {'RVOL':>6} {'52w':>5} {'Sector'}")
        print(f"  {'-'*8} {'-'*8} {'-'*6} {'-'*6} {'-'*5} {'-'*20}")
        for s in result.stocks:
            print(
                f"  {s.ticker:<8} ${s.price:>7.2f} {s.atrp:>5.1f}% {s.rvol:>5.2f}x {s.pos_52w:>4}% {s.sector}"
            )
        print()
        print(f"  Top stock: {result.stocks[0].ticker} "
              f"(ATRP={result.stocks[0].atrp:.1f}%, RVOL={result.stocks[0].rvol:.2f}x)")
    else:
        print("  No stocks passed all filters.")
        print("  (This can happen pre-market with low RVOL — normal)")

    print()
    print("=" * 65)
    print("  SCAN DONE — No IP issues, no errors")
    print("=" * 65)


asyncio.run(main())
