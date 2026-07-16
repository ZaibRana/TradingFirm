"""
Professional Day Trading Scanner (pro_scan.py)
==============================================
Filters:
  1. Price $10-$40              (Finviz)
  2. Market Cap > $500M         (Finviz: Small+Mid+Large)
  3. Daily Volume > 1M          (Finviz)
  4. RVOL > 1.5                 (yfinance, Finviz pre-filter when live)
  5. ATRP 3%-6%                 (yfinance calculated)
  6. 4H: Price > 50 EMA         (yfinance)
  7. 1H: 20 EMA > 50 EMA        (yfinance)
  8. Not at 52w top/bottom 10%  (yfinance)
  9. IPO > 6 months             (data history check)

Weekend Mode: Skips Finviz RVOL pre-filter, relaxes RVOL to > 1.0
Enrichment:   Float, News headlines, Sector, Industry
"""

import json
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from finvizfinance.screener.overview import Overview


# ─── Market Status Detection ────────────────────────────────────

def get_market_status():
    """Returns (status, eastern_time).
    Status: 'open' | 'premarket' | 'afterhours' | 'weekend'"""
    et = datetime.now(ZoneInfo("America/New_York"))

    if et.weekday() >= 5:
        return "weekend", et

    mins = et.hour * 60 + et.minute
    if 570 <= mins < 960:       # 9:30 AM – 4:00 PM ET
        return "open", et
    elif mins < 570:
        return "premarket", et
    else:
        return "afterhours", et


# ─── Step 1: Finviz Broad Filter ────────────────────────────────

def get_candidates(price_min=None, price_max=None):
    """Finviz pre-screen. Returns (tickers_list, market_status_str)."""
    status, et = get_market_status()
    print(f"  Market: {status} ({et.strftime('%a %I:%M %p ET')})")
    if status == "weekend":
        print("  ⚠  Weekend mode — results based on last trading day")

    # Price label for logging
    if price_min and price_max:
        price_label = f"${price_min}-${price_max}"
    elif price_min:
        price_label = f">${price_min}"
    elif price_max:
        price_label = f"<${price_max}"
    else:
        price_label = "no filter"
    print(f"  Price range: {price_label}")

    screener = Overview()
    exchanges = ["NASDAQ", "NYSE", "AMEX"]
    cap_sizes = [
        "Small ($300mln to $2bln)",
        "Mid ($2bln to $10bln)",
        "Large ($10bln to $200bln)",
    ]

    # Use broadest Finviz price floor to avoid penny stocks
    finviz_price = "Over $5"
    if price_min and price_min >= 20:
        finviz_price = "Over $20"
    elif price_min and price_min >= 10:
        finviz_price = "Over $10"

    all_dfs = []
    for exchange in exchanges:
        for cap in cap_sizes:
            base = {
                "Exchange": exchange,
                "Price": finviz_price,
                "Average Volume": "Over 1M",
                "Market Cap.": cap,
            }

            try:
                screener.set_filter(filters_dict=base)
                df = screener.screener_view()
                if df is not None and len(df) > 0:
                    all_dfs.append(df)
                    print(f"    {exchange} {cap.split('(')[0].strip()}: {len(df)}")
            except Exception as e:
                print(f"    {exchange} {cap.split('(')[0].strip()}: Error — {e}")

    if not all_dfs:
        return [], status

    combined = pd.concat(all_dfs, ignore_index=True)
    # Apply exact price filter in code
    if price_min:
        combined = combined[combined["Price"] >= price_min]
    if price_max:
        combined = combined[combined["Price"] <= price_max]
    combined = combined.drop_duplicates(subset=["Ticker"])
    tickers = sorted(combined["Ticker"].tolist())

    print(f"  → {len(tickers)} candidates ({price_label})")
    return tickers, status


# ─── Step 2: Bulk Download ──────────────────────────────────────

def download_data(tickers):
    """Download daily (1y) and hourly (3mo) candles via threaded batch."""
    print(f"\nStep 2: Downloading chart data for {len(tickers)} tickers …")
    t_str = " ".join(tickers)

    print("  Daily (1 year) …")
    daily = yf.download(
        t_str, period="1y", interval="1d",
        group_by="ticker", threads=True, progress=False,
    )
    print("  Hourly (3 months) …")
    hourly = yf.download(
        t_str, period="3mo", interval="1h",
        group_by="ticker", threads=True, progress=False,
    )
    print("  Downloads complete.")
    return daily, hourly


def get_df(bulk, ticker):
    """Extract one ticker's OHLCV from a grouped bulk DataFrame."""
    try:
        if ticker in bulk.columns.get_level_values(0):
            df = bulk[ticker].dropna(subset=["Close"])
            return df if len(df) > 0 else None
    except Exception:
        pass
    return None


# ─── Indicator Helpers ──────────────────────────────────────────

def ema(series, period):
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def calc_atr(high, low, close, period=14):
    """Average True Range series."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def aggregate_4h(hourly_df):
    """Resample 1H candles → 4H candles."""
    df = hourly_df.copy()
    df.index = pd.to_datetime(df.index)
    df["_blk"] = df.index.hour // 4
    df["_day"] = df.index.date
    grouped = df.groupby(["_day", "_blk"]).agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna()
    return grouped.reset_index(drop=True)


# ─── Step 3: Apply All Filters ──────────────────────────────────

def apply_filters(ticker, daily_df, hourly_df, market_status):
    """Run every filter. Returns (passed, reason_str | data_dict)."""

    closes = daily_df["Close"]

    # ── IPO / history check (120 trading days ≈ 6 months) ──
    if len(daily_df) < 120:
        return False, f"Too new ({len(daily_df)} days < 120)"

    # ── ATRP 3 – 6 % ──
    atr_series = calc_atr(daily_df["High"], daily_df["Low"], closes)
    last_atr = atr_series.iloc[-1]
    if np.isnan(last_atr) or closes.iloc[-1] == 0:
        return False, "ATR calc failed"
    atrp = (last_atr / closes.iloc[-1]) * 100
    if atrp < 2.5:
        return False, f"ATRP {atrp:.1f}% < 2.5%"
    if atrp > 6.0:
        return False, f"ATRP {atrp:.1f}% > 6%"

    # ── RVOL (time-adjusted for live market) ──
    if len(daily_df) < 22:
        return False, "Not enough days for RVOL"
    last_vol = float(daily_df["Volume"].iloc[-1])
    avg_vol = float(daily_df["Volume"].iloc[-21:-1].mean())

    if market_status == "open":
        # Scale partial-day volume to estimate full-day volume
        # Trading session: 9:30 AM – 4:00 PM ET = 390 minutes
        et_now = datetime.now(ZoneInfo("America/New_York"))
        mins_elapsed = (et_now.hour * 60 + et_now.minute) - 570  # 570 = 9:30 AM
        mins_elapsed = max(mins_elapsed, 5)  # avoid divide-by-zero early
        scale = 390.0 / mins_elapsed
        projected_vol = last_vol * scale
        rvol = (projected_vol / avg_vol) if avg_vol > 0 else 0
    else:
        rvol = (last_vol / avg_vol) if avg_vol > 0 else 0

    rvol_floor = 1.0 if market_status in ("weekend", "premarket") else 1.2
    if rvol < rvol_floor:
        return False, f"RVOL {rvol:.2f}"

    # ── 4H: Price > 50 EMA ──
    h4 = aggregate_4h(hourly_df)
    if len(h4) < 55:
        return False, f"4H data short ({len(h4)} bars)"
    ema50_4h = ema(h4["Close"], 50).iloc[-1]
    price_4h = h4["Close"].iloc[-1]
    if price_4h <= ema50_4h:
        return False, f"4H Price < 50 EMA"

    # ── 1H: EMA 20 > EMA 50 ──
    if len(hourly_df) < 55:
        return False, f"1H data short ({len(hourly_df)} bars)"
    ema20_1h = ema(hourly_df["Close"], 20).iloc[-1]
    ema50_1h = ema(hourly_df["Close"], 50).iloc[-1]
    if ema20_1h <= ema50_1h:
        return False, "1H EMA20 < EMA50"

    # ── 52-week position: not in top/bottom 10 % ──
    hi52 = closes.max()
    lo52 = closes.min()
    rng = hi52 - lo52
    pos = ((closes.iloc[-1] - lo52) / rng) if rng > 0 else 0.5
    if pos > 0.90:
        return False, f"52w high zone ({pos:.0%})"
    if pos < 0.10:
        return False, f"52w low zone ({pos:.0%})"

    # ── All passed ──
    return True, {
        "atrp": round(atrp, 2),
        "atr": round(float(last_atr), 2),
        "rvol": round(rvol, 2),
        "pos52w": round(pos * 100),
        "price": round(float(closes.iloc[-1]), 2),
        "hi52": round(float(hi52), 2),
        "lo52": round(float(lo52), 2),
    }


# ─── Step 4: Enrichment ─────────────────────────────────────────

def enrich(ticker, fdata):
    """Attach float, news, sector, industry via yfinance Ticker."""
    t = yf.Ticker(ticker)
    info = t.info or {}

    # Float display
    flt = info.get("floatShares", 0)
    if flt and flt >= 1e9:
        flt_str = f"{flt / 1e9:.1f}B"
    elif flt and flt >= 1e6:
        flt_str = f"{flt / 1e6:.0f}M"
    elif flt:
        flt_str = f"{flt:,.0f}"
    else:
        flt_str = "N/A"

    # News headlines (top 3 with URLs)
    headlines = []
    try:
        for item in (t.news or [])[:3]:
            if isinstance(item, dict):
                c = item.get("content", {}) or {}
                title = c.get("title", "")
                if not title:
                    title = item.get("title", "")
                url = ""
                canon = c.get("canonicalUrl")
                if isinstance(canon, dict):
                    url = canon.get("url", "")
                if not url:
                    url = item.get("link", "")
                provider = ""
                prov = c.get("provider")
                if isinstance(prov, dict):
                    provider = prov.get("displayName", "")
                if not provider:
                    provider = item.get("publisher", "")
                if title:
                    headlines.append({
                        "title": title,
                        "url": url,
                        "publisher": provider,
                    })
    except Exception:
        pass

    return {
        "symbol": ticker,
        "name": info.get("shortName", ""),
        "price": fdata["price"],
        "sector": info.get("sector", "Other"),
        "industry": info.get("industry", "Other"),
        "marketCap": info.get("marketCap", 0),
        "floatShares": flt,
        "floatStr": flt_str,
        "fiftyTwoWeekHigh": fdata["hi52"],
        "fiftyTwoWeekLow": fdata["lo52"],
        "atrp": fdata["atrp"],
        "atr": fdata["atr"],
        "rvol": fdata["rvol"],
        "pos52w": fdata["pos52w"],
        "bigBodyPct": fdata.get("big_body_pct", 0),
        "med5mRange": fdata.get("med_5m_range", 0),
        "med5mBody": fdata.get("med_5m_body", 0),
        "news": headlines,
    }


# ─── Pipeline ───────────────────────────────────────────────────

def run(advanced=False, price_min=None, price_max=None):
    t0 = datetime.now()
    print("=" * 60)
    print("  PROFESSIONAL DAY TRADING SCANNER")
    print("=" * 60)

    # 1 — Finviz
    print("\nStep 1: Finviz broad filter …")
    tickers, status = get_candidates(price_min=price_min, price_max=price_max)
    if not tickers:
        print("No candidates found. Aborting.")
        # Write empty results so the frontend knows
        _save([], status, 0, t0)
        return

    # 2 — Download
    daily, hourly = download_data(tickers)

    # 3 — Filter
    print(f"\nStep 3: Applying filters ({status} mode) …")
    winners = {}
    rej = {}

    for sym in tickers:
        d = get_df(daily, sym)
        h = get_df(hourly, sym)
        if d is None or h is None:
            continue

        ok, res = apply_filters(sym, d, h, status)
        if ok:
            winners[sym] = res
            print(f"  ✅ {sym:6s}  ATRP {res['atrp']:>5.1f}%  "
                  f"RVOL {res['rvol']:>5.2f}x  52w {res['pos52w']:>3d}%")
        else:
            key = res.split("(")[0].split("<")[0].strip()
            rej[key] = rej.get(key, 0) + 1

    print(f"\n  ▸ {len(winners)} passed / {len(tickers)} scanned")
    print("  Rejection breakdown:")
    for r, c in sorted(rej.items(), key=lambda x: -x[1])[:8]:
        print(f"    {c:>4d}×  {r}")

    # 3.5 — 5M Tradability Check (LAST TRADING DAY ONLY)
    if advanced:
        print(f"\nStep 3.5: Checking 5M tradability on last trading day …")
        tradeable = {}
        for sym, fdata in list(winners.items()):
            try:
                df5 = yf.download(sym, period="5d", interval="5m", progress=False)
                if df5 is None or len(df5) < 50:
                    print(f"  ❌ {sym:6s}  Insufficient 5M data")
                    continue

                # Isolate LAST COMPLETE trading day
                dates = np.unique(df5.index.date)
                last_date = dates[-1]
                last_day = df5[df5.index.date == last_date]

                # If today is incomplete (live market), use previous day
                if len(last_day) < 20 and len(dates) >= 2:
                    last_date = dates[-2]
                    last_day = df5[df5.index.date == last_date]

                if len(last_day) < 20:
                    print(f"  ❌ {sym:6s}  Last day too short ({len(last_day)} bars)")
                    continue

                opens = last_day["Open"].values.flatten()
                closes = last_day["Close"].values.flatten()
                vols = last_day["Volume"].values.flatten()

                # A) Day direction — reject dumps (fell > -1.5%)
                day_open = float(opens[0])
                day_close = float(closes[-1])
                day_chg = (day_close - day_open) / day_open * 100
                if day_chg < -1.5:
                    print(f"  ❌ {sym:6s}  Dumped {day_chg:+.1f}% on {last_date}")
                    continue

                # B) Green/red ratio — reject if > 55% red candles
                diffs = closes - opens
                green = int(np.sum(diffs > 0))
                red = int(np.sum(diffs < 0))
                total_candles = green + red
                red_pct = (red / total_candles * 100) if total_candles > 0 else 50
                if red_pct > 55:
                    print(f"  ❌ {sym:6s}  Too bearish: {red_pct:.0f}% red candles ({green}g/{red}r)")
                    continue

                # C) Big body check — need 10%+ candles with body >= $0.10
                bodies = np.abs(diffs)
                big_body_pct = float(np.sum(bodies >= 0.10) / len(bodies) * 100)
                ranges = (last_day["High"] - last_day["Low"]).values.flatten()
                med_range = float(np.median(ranges))

                if big_body_pct < 10.0:
                    print(f"  ❌ {sym:6s}  5M dead — only {big_body_pct:.0f}% candles move $0.10+ "
                          f"(med=${med_range:.3f})")
                    continue

                # D) Volume distribution — reject if >50% vol in first 30 min
                vol_first = float(np.sum(vols[:6]))
                vol_total = float(np.sum(vols))
                vol_open_pct = (vol_first / vol_total * 100) if vol_total > 0 else 0
                if vol_open_pct > 50:
                    print(f"  ❌ {sym:6s}  Volume dies after open ({vol_open_pct:.0f}% in first 30m)")
                    continue

                fdata["big_body_pct"] = round(big_body_pct, 1)
                fdata["med_5m_range"] = round(med_range, 3)
                fdata["med_5m_body"] = round(float(np.median(bodies)), 3)
                fdata["day_change"] = round(day_chg, 1)
                fdata["green_ratio"] = round(green / total_candles * 100) if total_candles > 0 else 50
                tradeable[sym] = fdata
                print(f"  ✅ {sym:6s}  {day_chg:+.1f}%  {green}g/{red}r  "
                      f"big={big_body_pct:.0f}%  range=${med_range:.3f}  vol_open={vol_open_pct:.0f}%")
            except Exception as e:
                print(f"  ❌ {sym:6s}  5M error: {e}")

        print(f"\n  ▸ {len(tradeable)} tradeable / {len(winners)} passed filters")
        winners = tradeable
    else:
        print("\nStep 3.5: Skipped (advanced 5M filters disabled)")

    # 4 — Enrich (with market cap + float validation)
    print(f"\nStep 4: Enriching {len(winners)} winners …")
    results = []
    for sym, fdata in winners.items():
        enriched = enrich(sym, fdata)

        # Market cap gate — original spec requires > $500M
        mc = enriched.get("marketCap", 0) or 0
        if mc < 500_000_000:
            mc_str = f"${mc / 1e6:.0f}M" if mc > 0 else "N/A"
            print(f"  ❌ {sym:6s}  Market cap {mc_str} < $500M — rejected")
            continue

        # Float gate — 20M to 1B sweet spot for day trading
        flt = enriched.get("floatShares", 0) or 0
        if flt > 0 and flt < 20_000_000:
            print(f"  ❌ {sym:6s}  Float {enriched['floatStr']} < 20M — too illiquid")
            continue
        if flt > 1_000_000_000:
            print(f"  ❌ {sym:6s}  Float {enriched['floatStr']} > 1B — too slow for 5M")
            continue

        results.append(enriched)
        print(f"  ✅ {sym:6s}  {enriched['name']:<30s}  "
              f"{enriched['industry']:<25s}  Float: {enriched['floatStr']}")

    # 5 — Sort by quality: RVOL × ATRP (best first)
    results.sort(key=lambda s: (s.get("rvol", 0) or 0) * (s.get("atrp", 0) or 0), reverse=True)

    # 6 — Save
    _save(results, status, len(tickers), t0)


def _save(results, status, total_scanned, t0):
    """Write pro_results.json."""
    payload = {
        "scanner": "professional",
        "timestamp": datetime.now().isoformat(),
        "duration": str(datetime.now() - t0),
        "marketStatus": status,
        "totalScanned": total_scanned,
        "passedCount": len(results),
        "stocks": results,
    }
    with open("pro_results.json", "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\n{'=' * 60}")
    print(f"  DONE  {len(results)} stocks → pro_results.json")
    print(f"  Time  {datetime.now() - t0}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    adv = "--advanced" in sys.argv
    p_min = None
    p_max = None
    for i, arg in enumerate(sys.argv):
        if arg == "--price-min" and i + 1 < len(sys.argv):
            p_min = float(sys.argv[i + 1])
        if arg == "--price-max" and i + 1 < len(sys.argv):
            p_max = float(sys.argv[i + 1])
    run(advanced=adv, price_min=p_min, price_max=p_max)
