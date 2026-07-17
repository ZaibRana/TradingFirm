"""
Unified Stock Scanner (scan.py)
Combines all steps:
1. Finviz Broad Filter
2. yfinance Threaded Bulk Download
3. Technical and Candle Quality Filters
4. Enrichment & Analyst Rating filter (BUY or Strong Buy)
5. Save results to results.json
"""

import sys
import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
from finvizfinance.screener.overview import Overview

# ─── Step 1: Finviz Broad Filter ────────────────────────────────

def get_finviz_candidates():
    print("Step 1: Running Finviz Broad Filter...")
    screener = Overview()

    exchanges = ["NASDAQ", "NYSE", "AMEX"]
    cap_sizes = [
        "Mid ($2bln to $10bln)",
        "Large ($10bln to $200bln)",
    ]

    all_dfs = []
    for exchange in exchanges:
        for cap in cap_sizes:
            try:
                filters = {
                    "Exchange": exchange,
                    "Price": "Over $10",
                    "Average Volume": "Over 1M",
                    "Market Cap.": cap,
                    "IPO Date": "More than a year ago",
                }
                screener.set_filter(filters_dict=filters)
                df = screener.screener_view()
                if df is not None and len(df) > 0:
                    all_dfs.append(df)
                    print(f"  {exchange} {cap.split('(')[0].strip()}: {len(df)} stocks")
            except Exception as e:
                print(f"  {exchange} {cap}: Error - {e}")

    if not all_dfs:
        print("No candidates found via Finviz!")
        return []

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined[combined["Price"] <= 50.0]
    combined = combined.drop_duplicates(subset=["Ticker"])
    tickers = combined["Ticker"].tolist()

    print(f"  Total unique candidates after $50 cap: {len(tickers)}")

    return tickers

# ─── Step 2: yfinance Bulk Download ──────────────────────────────

def download_all_charts(tickers):
    print(f"\nStep 2: Downloading daily, hourly, and 5M chart data for {len(tickers)} tickers...")
    ticker_str = " ".join(tickers)
    
    daily = yf.download(ticker_str, period="3mo", interval="1d", group_by="ticker", threads=True, progress=False)
    hourly = yf.download(ticker_str, period="1mo", interval="1h", group_by="ticker", threads=True, progress=False)
    fivemin = yf.download(ticker_str, period="5d", interval="5m", group_by="ticker", threads=True, progress=False)
    
    print("  Downloads finished.")
    return daily, hourly, fivemin

def extract_ticker_df(bulk_df, ticker):
    try:
        if ticker in bulk_df.columns.get_level_values(0):
            df = bulk_df[ticker].dropna(subset=["Close"])
            if len(df) > 0:
                return df
    except Exception:
        pass
    return None

# ─── Indicators & Math Helpers ──────────────────────────────────

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def macd(closes, fast=12, slow=26, signal=9):
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def get_atr(high, low, close, period=14):
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean().iloc[-1]

def get_adx(high, low, close, period=14):
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    plus_dm[(plus_dm < minus_dm)] = 0
    minus_dm[(minus_dm < plus_dm)] = 0

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_vals = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr_vals)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr_vals)

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx_vals = dx.ewm(span=period, adjust=False).mean()
    return adx_vals

def aggregate_4h(hourly_df):
    df = hourly_df.copy()
    df.index = pd.to_datetime(df.index)
    df["block"] = df.index.hour // 4
    df["date"] = df.index.date
    grouped = df.groupby(["date", "block"]).agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna()
    return grouped.reset_index(drop=True)

# ─── Step 3: Run Filters ─────────────────────────────────────────

def run_filters(ticker, daily_df, hourly_df, fivemin_df):
    # 1. ATR Check
    atr_val = get_atr(daily_df["High"], daily_df["Low"], daily_df["Close"], 14)
    if np.isnan(atr_val) or atr_val < 1.0:
        return False, f"ATR ${atr_val:.2f} < $1"

    # 2. 1D Check — "not bearish" (pass if ANY one is true)
    closes_1d = daily_df["Close"]
    _, _, hist_1d = macd(closes_1d)
    last_hist_1d = hist_1d.iloc[-1]
    prev_hist_1d = hist_1d.iloc[-2] if len(hist_1d) >= 2 else -999

    ema9_1d = ema(closes_1d, 9).iloc[-1]
    ema21_1d = ema(closes_1d, 21).iloc[-1]

    passed_1d = False
    reason_1d = ""

    # A) MACD histogram is positive
    if last_hist_1d > 0:
        passed_1d = True
        reason_1d = f"1D MACD positive ({last_hist_1d:.3f})"

    # B) MACD histogram is rising (momentum shifting)
    elif last_hist_1d > prev_hist_1d:
        passed_1d = True
        reason_1d = f"1D MACD rising ({prev_hist_1d:.3f}→{last_hist_1d:.3f})"

    # C) EMA9 > EMA21 (daily trend still up)
    elif ema9_1d > ema21_1d:
        passed_1d = True
        reason_1d = f"1D EMA9>21 (trend up)"

    # D) Bearish candle shrinking
    if not passed_1d:
        opens_1d = daily_df["Open"]
        red_sizes = []
        for i in range(len(daily_df) - 1, max(len(daily_df) - 6, -1), -1):
            if closes_1d.iloc[i] < opens_1d.iloc[i]:
                red_sizes.append(abs(closes_1d.iloc[i] - opens_1d.iloc[i]))
            if len(red_sizes) >= 3:
                break

        if len(red_sizes) >= 2:
            last_red = red_sizes[0]
            prev_avg = np.mean(red_sizes[1:])
            if last_red < prev_avg * 0.7:
                passed_1d = True
                reason_1d = f"1D bearish weakening"

    if not passed_1d:
        return False, f"1D bearish (hist={last_hist_1d:.3f}, EMA9<21)"

    # 3. 4H MACD and EMA Checks
    fourh_df = aggregate_4h(hourly_df)
    if len(fourh_df) < 30:
        return False, "Insufficient 4H data"
    closes_4h = fourh_df["Close"]
    _, _, hist_4h = macd(closes_4h)
    if hist_4h.iloc[-1] <= 0:
        return False, f"4H MACD negative ({hist_4h.iloc[-1]:.3f})"
    
    ema9_4h = ema(closes_4h, 9).iloc[-1]
    ema21_4h = ema(closes_4h, 21).iloc[-1]
    if ema9_4h <= ema21_4h:
        return False, "4H EMA9 <= EMA21"

    # 4. 1H MACD, EMA, and ADX Checks
    closes_1h = hourly_df["Close"]
    _, _, hist_1h = macd(closes_1h)
    if hist_1h.iloc[-1] <= 0:
        return False, f"1H MACD negative ({hist_1h.iloc[-1]:.3f})"
    
    ema9_1h = ema(closes_1h, 9).iloc[-1]
    ema21_1h = ema(closes_1h, 21).iloc[-1]
    if ema9_1h <= ema21_1h:
        return False, "1H EMA9 <= EMA21"

    adx_vals = get_adx(hourly_df["High"], hourly_df["Low"], closes_1h, 14)
    if len(adx_vals) < 3:
        return False, "1H ADX insufficient data"
    last_adx = adx_vals.iloc[-1]
    if not (last_adx > adx_vals.iloc[-2] and last_adx > adx_vals.iloc[-3]):
        return False, f"1H ADX not rising ({last_adx:.1f})"

    # 5. Range check: moved 1-2 points in previous 2-3 days
    last3 = daily_df.tail(3)
    ranges = (last3["High"] - last3["Low"]).values
    if np.mean(ranges) < 1.0:
        return False, f"Avg range ${np.mean(ranges):.2f} < $1"

    # 6. Peak check: not at 52-week extreme
    current_price = closes_1d.iloc[-1]
    high_52w = closes_1d.max()
    low_52w = closes_1d.min()
    range_52w = high_52w - low_52w
    if range_52w > 0:
        pos = (current_price - low_52w) / range_52w
        if pos > 0.95 or pos < 0.05:
            return False, f"At 52w extreme position ({pos:.0%})"

    # 7. Intraday 5M quality check
    if fivemin_df is None or len(fivemin_df) < 24:
        return False, "Insufficient 5M data"
    recent_5m = fivemin_df.tail(24)
    if recent_5m["Volume"].mean() < 1000:
        return False, "5M average volume dead (<1000)"
    avg_spread = (recent_5m["High"] - recent_5m["Low"]).mean()
    spread_pct = (avg_spread / recent_5m["Close"].mean()) * 100
    if spread_pct > 1.5:
        return False, f"5M spread distorted ({spread_pct:.2f}%)"

    return True, {
        "atr": atr_val,
        "adx_1h": last_adx,
        "avg_range_3d": np.mean(ranges),
    }

# ─── Step 4: Enrichment & Rating Checks ──────────────────────────

def enrich_and_verify(ticker, details):
    print(f"  Enriching & verifying {ticker}...")
    t = yf.Ticker(ticker)
    info = t.info or {}
    
    # Check analyst rating filter (strong_buy, buy)
    rating = info.get("recommendationKey", "none").lower()
    if rating not in ["buy", "strong_buy"]:
        print(f"    ❌ Rejected: Analyst rating is '{rating}' (needs BUY or higher)")
        return None

    # Calculate options sentiment
    options_data = {}
    try:
        expirations = t.options
        if expirations:
            nearest = expirations[0]
            chain = t.option_chain(nearest)
            calls, puts = chain.calls, chain.puts
            c_vol = int(calls["volume"].sum()) if "volume" in calls else 0
            p_vol = int(puts["volume"].sum()) if "volume" in puts else 0
            pc_ratio = p_vol / c_vol if c_vol > 0 else 999
            
            options_data = {
                "expiration": nearest,
                "callVolume": c_vol,
                "putVolume": p_vol,
                "putCallRatio": round(pc_ratio, 2),
                "bullish": pc_ratio < 0.7 and c_vol > 500,
            }
    except Exception:
        options_data = {"error": "Options chain unretrievable"}

    data = {
        "symbol": ticker,
        "name": info.get("shortName", ""),
        "price": info.get("currentPrice", info.get("regularMarketPrice", 0)),
        "sector": info.get("sector", "Other"),
        "industry": info.get("industry", "Other"),
        "marketCap": info.get("marketCap", 0),
        "analystRating": rating,
        "analystTargetPrice": info.get("targetMeanPrice", 0),
        "atr": round(details["atr"], 2),
        "adx": round(details["adx_1h"], 1),
        "avgRange3d": round(details["avg_range_3d"], 2),
        "options": options_data,
    }
    return data

# ─── Main Scanner Pipeline ──────────────────────────────────────

def run_scanner():
    start_time = datetime.now()
    
    # Step 1: Finviz Screen
    candidates = get_finviz_candidates()
    if not candidates:
        print("No candidates. Scan stopped.")
        return
        
    # Step 2: Download data
    daily, hourly, fivemin = download_all_charts(candidates)
    
    # Step 3: Run technical filters
    passed_technical = {}
    print("\nStep 3: Running technical and candle validation filters...")
    for t in candidates:
        d = extract_ticker_df(daily, t)
        h = extract_ticker_df(hourly, t)
        f = extract_ticker_df(fivemin, t)
        
        if d is None or h is None:
            continue
            
        ok, res = run_filters(t, d, h, f)
        if ok:
            passed_technical[t] = res
            print(f"  ✅ {t} passed technical filters.")

    # Step 4: Enrich & Analyst Rating Gate
    print(f"\nStep 4: Enriching and checking Analyst ratings for the {len(passed_technical)} survivors...")
    final_stocks = []
    for t, details in passed_technical.items():
        enriched = enrich_and_verify(t, details)
        if enriched:
            final_stocks.append(enriched)
            print(f"  🏆 {t} passed all scanner filters!")

    # Step 5: Save JSON
    output_data = {
        "timestamp": datetime.now().isoformat(),
        "scanDuration": str(datetime.now() - start_time),
        "totalScanned": len(candidates),
        "passedCount": len(final_stocks),
        "stocks": final_stocks,
    }
    
    with open("results.json", "w") as f:
        json.dump(output_data, f, indent=2)
        
    print(f"\nScan completed. Saved {len(final_stocks)} results to results.json.")

if __name__ == "__main__":
    run_scanner()
