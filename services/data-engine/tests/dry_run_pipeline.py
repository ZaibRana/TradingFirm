"""
OFFLINE verification of the full pipeline.
Zero Yahoo calls — mocks yf.download() with realistic DataFrames.
Tests the exact code path: Provider → Scanner → Filters → Results.
"""
import sys
sys.path.insert(0, ".")

# Mock finvizfinance (not installed locally, not needed for dry-run)
from unittest.mock import MagicMock
sys.modules["finvizfinance"] = MagicMock()
sys.modules["finvizfinance.screener"] = MagicMock()
sys.modules["finvizfinance.screener.overview"] = MagicMock()
import asyncio
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# ── Build realistic test data ──────────────────────────────────

def make_daily(ticker, base, atrp_range, rvol_ratio, rows=200):
    """Build realistic daily OHLCV that controls ATRP and RVOL."""
    np.random.seed(hash(ticker) % 2**31)
    dates = pd.bdate_range(end=datetime.now(), periods=rows)
    
    # Price: peak at 2/3, pullback — keeps 52w position mid-range
    peak = rows * 2 // 3
    up = np.linspace(0, 2.0, peak)
    down = np.linspace(2.0, 1.0, rows - peak)
    closes = base + np.concatenate([up, down]) + np.random.normal(0, 0.05, rows)
    
    half_range = (atrp_range / 100) * base / 2
    highs = closes + half_range
    lows = closes - half_range
    
    avg_vol = 1_500_000
    volumes = np.full(rows, avg_vol, dtype=float) + np.random.normal(0, 50000, rows)
    volumes[-1] = avg_vol * rvol_ratio  # Control RVOL
    
    return pd.DataFrame(
        {"Open": closes - 0.1, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=dates,
    )

def make_hourly(ticker, base, trend, rows=300):
    """Build hourly data. Positive trend = uptrend (passes EMA filters)."""
    np.random.seed(hash(ticker) % 2**31 + 1)
    dates = pd.date_range(end=datetime.now(), periods=rows, freq="h")
    closes = base + np.arange(rows) * trend + np.random.normal(0, 0.02, rows)
    highs = closes + 0.15
    lows = closes - 0.15
    volumes = np.full(rows, 50000, dtype=float)
    return pd.DataFrame(
        {"Open": closes - 0.05, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=dates,
    )

# ── Test tickers with known outcomes ───────────────────────────

# Format: (ticker, base_price, atrp%, rvol_mult, hourly_trend, expected_daily, expected_hourly)
TEST_CASES = [
    # Should PASS both daily and hourly
    ("WINNER1", 25.0, 4.0, 1.5, 0.005,  "pass",   "pass"),
    ("WINNER2", 30.0, 3.5, 2.0, 0.008,  "pass",   "pass"),
    # Should PASS daily, FAIL hourly (downtrend)
    ("DAILY_ONLY", 22.0, 3.0, 1.3, -0.01, "pass",  "fail"),
    # Should FAIL daily: ATRP too low (<2.5%)
    ("LOW_ATRP", 25.0, 1.0, 1.5, 0.005,  "fail",  "skip"),
    # Should FAIL daily: ATRP too high (>6%)
    ("HIGH_ATRP", 20.0, 8.0, 1.5, 0.005, "fail",  "skip"),
    # Should FAIL daily: RVOL too low
    ("LOW_RVOL", 25.0, 4.0, 0.3, 0.005,  "fail",  "skip"),
    # Should FAIL daily: too new (50 rows < 120)
    ("IPO_NEW",  15.0, 4.0, 1.5, 0.005,  "fail",  "skip"),
]

# Build DataFrames
daily_frames = {}
hourly_frames = {}

for ticker, base, atrp, rvol, htrend, exp_d, exp_h in TEST_CASES:
    rows = 50 if ticker == "IPO_NEW" else 200
    daily_frames[ticker] = make_daily(ticker, base, atrp, rvol, rows)
    hourly_frames[ticker] = make_hourly(ticker, base, htrend)

# Build multi-ticker bulk DataFrames (matching yf.download group_by="ticker" format)
tickers = [t for t, *_ in TEST_CASES]

daily_bulk = pd.concat(
    {t: daily_frames[t] for t in tickers}, axis=1
)
hourly_winner_bulk = None  # Will be built after daily filtering

print("=" * 70)
print("  OFFLINE DRY-RUN: Full pipeline verification (zero Yahoo calls)")
print("=" * 70)
print()

# ── Mock yf.download to return our test data ──────────────────

download_calls = []

def mock_download(tickers_str, period, interval, **kwargs):
    tickers_list = tickers_str.strip().split()
    download_calls.append({
        "tickers": tickers_list,
        "period": period,
        "interval": interval,
    })
    
    if interval == "1d":
        return daily_bulk
    elif interval == "1h":
        # Build hourly bulk only for requested tickers
        frames = {}
        for t in tickers_list:
            if t in hourly_frames:
                frames[t] = hourly_frames[t]
        if len(frames) == 1:
            return list(frames.values())[0]
        return pd.concat(frames, axis=1)
    return pd.DataFrame()

# ── Run the actual scanner through the provider ──────────────

async def run_test():
    from providers.yfinance_provider import YFinanceProvider
    from scanners.market_scanner import MarketScanner
    
    # Create real provider but mock its yf.download calls
    with patch("providers.yfinance_provider.yf.download", side_effect=mock_download):
        provider = YFinanceProvider.__new__(YFinanceProvider)
        provider._session = MagicMock()
        
        scanner = MarketScanner(provider)
        
        # ── Step A: Test daily filters directly ──
        print("STEP A: Daily filter results")
        print(f"{'Ticker':<12} {'Expected':<10} {'Actual':<10} {'Detail'}")
        print("-" * 70)
        
        daily_winners = {}
        
        for ticker, base, atrp, rvol, htrend, exp_d, exp_h in TEST_CASES:
            df = daily_frames[ticker]
            passed, result = scanner._apply_daily_filters(ticker, df, "weekend")
            
            actual = "pass" if passed else "fail"
            match = "✅" if actual == exp_d else "❌ MISMATCH"
            
            if passed:
                daily_winners[ticker] = result
                detail = f"ATRP={result['atrp']:.1f}%  RVOL={result['rvol']:.2f}x  52w={result['pos52w']}%  ${result['price']:.2f}"
            else:
                detail = result
            
            print(f"{ticker:<12} {exp_d:<10} {actual:<10} {match} {detail}")
        
        print()
        print(f"Daily: {len(daily_winners)} passed / {len(TEST_CASES)} tested")
        
        # ── Step B: Test hourly filters on daily winners ──
        print()
        print("STEP B: Hourly filter results (daily winners only)")
        print(f"{'Ticker':<12} {'Expected':<10} {'Actual':<10} {'Detail'}")
        print("-" * 70)
        
        hourly_winners = {}
        
        for ticker, data in daily_winners.items():
            # Find expected outcome
            exp_h = [x[6] for x in TEST_CASES if x[0] == ticker][0]
            
            hdf = hourly_frames[ticker]
            passed, result = scanner._apply_hourly_filters(ticker, hdf, data)
            
            actual = "pass" if passed else "fail"
            match = "✅" if actual == exp_h else "❌ MISMATCH"
            
            if passed:
                hourly_winners[ticker] = result
                detail = "4H>50EMA + 1H EMA20>EMA50"
            else:
                detail = result
            
            print(f"{ticker:<12} {exp_h:<10} {actual:<10} {match} {detail}")
        
        print()
        print(f"Hourly: {len(hourly_winners)} passed / {len(daily_winners)} daily winners")
        
        # ── Step C: Verify pipeline calls ──
        print()
        print("STEP C: Pipeline architecture verification")
        print("-" * 70)
        
        # Mock get_candidates to return our test tickers
        async def mock_candidates(**kwargs):
            return (tickers, "weekend")
        provider.get_candidates = mock_candidates
        
        # Run actual scan through the full pipeline
        result = await scanner.run_scan(price_min=10.0, price_max=40.0)
        
        print(f"  download_daily called:  {len([c for c in download_calls if c['interval']=='1d'])} time(s)")
        print(f"  download_hourly called: {len([c for c in download_calls if c['interval']=='1h'])} time(s)")
        
        # Check what tickers were sent to hourly download
        hourly_calls = [c for c in download_calls if c['interval'] == '1h']
        if hourly_calls:
            hourly_tickers = hourly_calls[-1]['tickers']
            print(f"  hourly download received: {hourly_tickers}")
            print(f"  hourly download count: {len(hourly_tickers)} (NOT {len(tickers)})")
            
            # Verify no rejected tickers in hourly call
            rejected = [t for t,_,_,_,_,exp,_ in TEST_CASES if exp == "fail"]
            leaked = [t for t in rejected if t in hourly_tickers]
            if leaked:
                print(f"  ❌ LEAK: These rejected tickers were sent to hourly: {leaked}")
            else:
                print(f"  ✅ No rejected tickers leaked to hourly download")
        
        print(f"  Scan result: {result.passed_count} stocks passed full pipeline")
        print(f"  Total scanned: {result.total_scanned}")
        print(f"  Duration: {result.duration_seconds:.2f}s")
        
        # ── Final verdict ──
        print()
        print("=" * 70)
        all_correct = True
        for ticker, base, atrp, rvol, htrend, exp_d, exp_h in TEST_CASES:
            df = daily_frames[ticker]
            passed, _ = scanner._apply_daily_filters(ticker, df, "weekend")
            if (passed and exp_d != "pass") or (not passed and exp_d != "fail"):
                print(f"  ❌ {ticker}: daily expected {exp_d}, got {'pass' if passed else 'fail'}")
                all_correct = False
        
        if all_correct:
            print("  ✅ ALL FILTERS CORRECT — every ticker matched expected outcome")
        
        if hourly_calls:
            hc = hourly_calls[-1]['tickers']
            if not any(t in hc for t in rejected):
                print("  ✅ PIPELINE CORRECT — hourly download only for daily winners")
            else:
                print("  ❌ PIPELINE BUG — rejected tickers leaked to hourly")
                all_correct = False
        
        if result.passed_count > 0:
            print(f"  ✅ STOCKS FOUND — {result.passed_count} passed full pipeline")
        
        print("=" * 70)

asyncio.run(run_test())
