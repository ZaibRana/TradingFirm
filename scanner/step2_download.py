"""
Step 2: yfinance Bulk Download
Downloads 1D, 1H, 5M chart data for all candidates using threaded bulk download.
"""
import yfinance as yf
import pandas as pd
from step1_finviz import get_candidates

def download_charts(tickers):
    """Bulk download chart data for all tickers. Returns dict of DataFrames."""
    print(f"\nDownloading chart data for {len(tickers)} tickers...")
    
    ticker_str = " ".join(tickers)
    
    # 1D candles — 3 months (for 1D MACD, EMA, ATR, 52-week check)
    print("  Fetching 1D candles (3 months)...")
    daily = yf.download(
        ticker_str, period="3mo", interval="1d",
        group_by="ticker", threads=True, progress=False
    )
    
    # 1H candles — 1 month (for 4H aggregation + 1H MACD/EMA/ADX)
    print("  Fetching 1H candles (1 month)...")
    hourly = yf.download(
        ticker_str, period="1mo", interval="1h",
        group_by="ticker", threads=True, progress=False
    )
    
    # 5M candles — 5 days (for chart quality + spread check)
    print("  Fetching 5M candles (5 days)...")
    fivemin = yf.download(
        ticker_str, period="5d", interval="5m",
        group_by="ticker", threads=True, progress=False
    )
    
    print(f"  Download complete.")
    print(f"    1D shape: {daily.shape}")
    print(f"    1H shape: {hourly.shape}")
    print(f"    5M shape: {fivemin.shape}")
    
    return daily, hourly, fivemin


def extract_ticker_data(bulk_df, ticker):
    """Extract a single ticker's OHLCV from a grouped bulk download DataFrame."""
    try:
        if ticker in bulk_df.columns.get_level_values(0):
            df = bulk_df[ticker].dropna(subset=["Close"])
            if len(df) > 0:
                return df
    except (KeyError, TypeError):
        pass
    return None


if __name__ == "__main__":
    tickers = get_candidates()
    print(f"\nGot {len(tickers)} candidates from Finviz")
    
    daily, hourly, fivemin = download_charts(tickers)
    
    # Test: extract data for a few tickers
    test_tickers = tickers[:5]
    for t in test_tickers:
        d = extract_ticker_data(daily, t)
        h = extract_ticker_data(hourly, t)
        f = extract_ticker_data(fivemin, t)
        print(f"  {t}: daily={len(d) if d is not None else 0} "
              f"hourly={len(h) if h is not None else 0} "
              f"5min={len(f) if f is not None else 0}")
