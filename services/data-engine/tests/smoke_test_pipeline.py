"""
Smoke test: Run the restructured pipeline with real tickers.
Step 1: Test 1 ticker (verify download works)
Step 2: Test 10 tickers (verify filters work, see pass/reject breakdown)
NO Finviz — we use a hardcoded list to avoid 9 screener calls.
"""
import sys
import time
import numpy as np
import pandas as pd
import yfinance as yf
import requests

# ── Setup: browser-like session ──
session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
})

# Add the service code to path
sys.path.insert(0, ".")
from indicators.technical import calc_atr, calc_atrp, calc_rvol, ema, aggregate_4h, check_52w_position
from scanners.market_scanner import MarketScanner
from scanners.market_status import get_market_status

# ── Step 1: Canary — download daily for 1 ticker ──
print("=" * 60)
print("  SMOKE TEST: 1 ticker (canary)")
print("=" * 60)

canary = yf.download("AAPL", period="1y", interval="1d", progress=False, session=session)
if canary is None or canary.empty:
    print("❌ CANARY FAILED: Could not download AAPL daily data.")
    print("   Yahoo may be blocking. STOP — do not proceed.")
    sys.exit(1)

last_close = float(canary["Close"].iloc[-1])
print(f"✅ AAPL daily download OK: {len(canary)} bars, last close ${last_close:.2f}")
print()

time.sleep(3)  # Be polite to Yahoo

# ── Step 2: 10 real tickers — daily download + filter ──
TEST_TICKERS = ["AAPL", "MSFT", "NVDA", "AMD", "PYPL", "SQ", "PLTR", "ROKU", "SNAP", "RIVN"]
print("=" * 60)
print(f"  SMOKE TEST: {len(TEST_TICKERS)} tickers — daily filters")
print("=" * 60)

print(f"\nDownloading daily data (1y) for {len(TEST_TICKERS)} tickers...")
daily_bulk = yf.download(
    " ".join(TEST_TICKERS), period="1y", interval="1d",
    group_by="ticker", threads=False, progress=False, session=session,
)

if daily_bulk is None or daily_bulk.empty:
    print("❌ Daily download FAILED. Yahoo may be blocking.")
    sys.exit(1)

print(f"✅ Daily bulk download: shape {daily_bulk.shape}")
print()

# ── Apply daily-only filters ──
status, et = get_market_status()
print(f"Market status: {status} ({et.strftime('%a %I:%M %p ET')})")
print()

# Create scanner instance with a dummy provider (we're calling methods directly)
from unittest.mock import MagicMock
scanner = MarketScanner(MagicMock())

daily_winners = {}
daily_rejections = {}

print(f"{'Ticker':<8} {'Result':<10} {'Detail'}")
print("-" * 60)

for ticker in TEST_TICKERS:
    try:
        if ticker in daily_bulk.columns.get_level_values(0):
            df = daily_bulk[ticker].dropna(subset=["Close"])
        else:
            print(f"{ticker:<8} {'SKIP':<10} No data in bulk download")
            continue

        if df is None or len(df) == 0:
            print(f"{ticker:<8} {'SKIP':<10} Empty data")
            continue

        passed, result = scanner._apply_daily_filters(ticker, df, status)

        if passed:
            daily_winners[ticker] = result
            print(f"{ticker:<8} {'✅ PASS':<10} ATRP={result['atrp']:.1f}%  RVOL={result['rvol']:.2f}x  52w={result['pos52w']}%  ${result['price']:.2f}")
        else:
            key = result.split("(")[0].split("<")[0].strip()
            daily_rejections[key] = daily_rejections.get(key, 0) + 1
            print(f"{ticker:<8} {'❌ REJECT':<10} {result}")
    except Exception as e:
        print(f"{ticker:<8} {'ERROR':<10} {e}")

print()
print(f"Daily filter results: {len(daily_winners)} passed / {len(TEST_TICKERS)} tested")
if daily_rejections:
    print("Rejection breakdown:")
    for reason, count in sorted(daily_rejections.items(), key=lambda x: -x[1]):
        print(f"  {count}× {reason}")

# ── Step 3: hourly download + filter ONLY for daily winners ──
if daily_winners:
    print()
    print("=" * 60)
    print(f"  HOURLY FILTERS: downloading for {len(daily_winners)} daily winners ONLY")
    print(f"  (NOT all {len(TEST_TICKERS)} tickers — this is the optimization)")
    print("=" * 60)

    time.sleep(5)  # Rate limit safety

    winner_tickers = list(daily_winners.keys())
    print(f"\nDownloading hourly data (3mo) for: {winner_tickers}")
    hourly_bulk = yf.download(
        " ".join(winner_tickers), period="3mo", interval="1h",
        group_by="ticker", threads=False, progress=False, session=session,
    )

    if hourly_bulk is None or hourly_bulk.empty:
        print("⚠️  Hourly download returned empty — skipping hourly filters")
    else:
        print(f"✅ Hourly bulk download: shape {hourly_bulk.shape}")
        print()

        hourly_passed = {}
        hourly_rejections = {}

        print(f"{'Ticker':<8} {'Result':<10} {'Detail'}")
        print("-" * 60)

        for ticker, daily_data in daily_winners.items():
            try:
                if len(winner_tickers) == 1:
                    # Single ticker: no multi-level columns
                    hdf = hourly_bulk.dropna(subset=["Close"])
                elif ticker in hourly_bulk.columns.get_level_values(0):
                    hdf = hourly_bulk[ticker].dropna(subset=["Close"])
                else:
                    print(f"{ticker:<8} {'SKIP':<10} No hourly data")
                    continue

                if hdf is None or len(hdf) == 0:
                    print(f"{ticker:<8} {'SKIP':<10} Empty hourly data")
                    continue

                passed, result = scanner._apply_hourly_filters(ticker, hdf, daily_data)

                if passed:
                    hourly_passed[ticker] = result
                    print(f"{ticker:<8} {'✅ PASS':<10} Passed 4H EMA50 + 1H EMA20>EMA50")
                else:
                    key = result.split("(")[0].split("<")[0].strip()
                    hourly_rejections[key] = hourly_rejections.get(key, 0) + 1
                    print(f"{ticker:<8} {'❌ REJECT':<10} {result}")
            except Exception as e:
                print(f"{ticker:<8} {'ERROR':<10} {e}")

        print()
        print(f"Hourly filter results: {len(hourly_passed)} passed / {len(daily_winners)} daily winners")
        if hourly_rejections:
            print("Rejection breakdown:")
            for reason, count in sorted(hourly_rejections.items(), key=lambda x: -x[1]):
                print(f"  {count}× {reason}")

        if hourly_passed:
            print()
            print("=" * 60)
            print(f"  FINAL WINNERS: {len(hourly_passed)} stocks")
            print("=" * 60)
            for ticker, data in hourly_passed.items():
                print(f"  {ticker}: ${data['price']:.2f}  ATRP={data['atrp']:.1f}%  RVOL={data['rvol']:.2f}x  52w={data['pos52w']}%")
        else:
            print("\n  No stocks passed all filters (normal for a small test set)")
else:
    print("\n  No daily winners — nothing to test with hourly filters")

print()
print("Smoke test complete. No IP issues.")
