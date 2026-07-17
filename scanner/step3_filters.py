"""
Step 3: Apply ALL filters to downloaded chart data.
Only the user's criteria — nothing invented.

Filters:
1. ATR >= $1
2. 1D MACD positive + EMA 9>21 (or bearish candle shrinking)
3. 4H MACD positive + EMA 9>21
4. 1H MACD positive + EMA 9>21 + ADX rising
5. Moved 1-2 points in last 3 days
6. Not at 52-week peak or lowest
7. 5M chart not distorted (volume + spread)
"""
import numpy as np
import pandas as pd


# ─── Indicator Calculations ─────────────────────────────────────

def ema(series, period):
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def macd(closes, fast=12, slow=26, signal=9):
    """MACD line, signal line, histogram."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(high, low, close, period=14):
    """Average True Range."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean().iloc[-1]


def adx(high, low, close, period=14):
    """ADX — returns last 3 values for slope check."""
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
    """Aggregate 1H candles to 4H candles."""
    df = hourly_df.copy()
    df.index = pd.to_datetime(df.index)
    # Group by date + 4-hour block
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


# ─── Filter Functions ────────────────────────────────────────────

def check_atr(daily_df):
    """ATR must be >= $1."""
    val = atr(daily_df["High"], daily_df["Low"], daily_df["Close"], 14)
    if np.isnan(val) or val < 1.0:
        return False, f"ATR ${val:.2f} < $1"
    return True, f"ATR ${val:.2f}"


def check_1d_macd_ema(daily_df):
    """1D MACD histogram positive + EMA 9 > EMA 21.
    OR: bearish candle shrinking (last red candle shorter than previous 3 red candles)."""
    closes = daily_df["Close"]

    # MACD check
    _, _, hist = macd(closes)
    last_hist = hist.iloc[-1]
    macd_up = last_hist > 0

    # EMA cross check
    ema9 = ema(closes, 9).iloc[-1]
    ema21 = ema(closes, 21).iloc[-1]
    ema_crossed = ema9 > ema21

    if macd_up and ema_crossed:
        return True, f"1D MACD +{last_hist:.3f}, EMA9>21"

    # "About to come up" check: last bearish candle shorter than previous 3
    opens = daily_df["Open"]
    red_candles = []
    for i in range(len(daily_df) - 1, max(len(daily_df) - 5, -1), -1):
        if closes.iloc[i] < opens.iloc[i]:  # Red candle
            red_candles.append(abs(closes.iloc[i] - opens.iloc[i]))
        if len(red_candles) >= 4:
            break

    if len(red_candles) >= 4:
        last_red = red_candles[0]
        prev_avg = np.mean(red_candles[1:4])
        if last_red < prev_avg * 0.7:  # Last red is 30%+ smaller
            return True, f"1D bearish weakening ({last_red:.2f} < avg {prev_avg:.2f})"

    if not macd_up:
        return False, f"1D MACD negative ({last_hist:.3f})"
    return False, f"1D EMA9 < EMA21"


def check_4h_macd_ema(fourh_df):
    """4H MACD histogram positive + EMA 9 > EMA 21."""
    closes = fourh_df["Close"]
    _, _, hist = macd(closes)
    last_hist = hist.iloc[-1]

    ema9 = ema(closes, 9).iloc[-1]
    ema21 = ema(closes, 21).iloc[-1]

    if last_hist <= 0:
        return False, f"4H MACD negative ({last_hist:.3f})"
    if ema9 <= ema21:
        return False, f"4H EMA9 < EMA21"
    return True, f"4H MACD +{last_hist:.3f}, EMA9>21"


def check_1h_macd_ema_adx(hourly_df):
    """1H MACD positive + EMA 9>21 + ADX rising (higher than previous 2)."""
    closes = hourly_df["Close"]

    # MACD
    _, _, hist = macd(closes)
    last_hist = hist.iloc[-1]
    if last_hist <= 0:
        return False, f"1H MACD negative ({last_hist:.3f})"

    # EMA cross
    ema9 = ema(closes, 9).iloc[-1]
    ema21 = ema(closes, 21).iloc[-1]
    if ema9 <= ema21:
        return False, f"1H EMA9 < EMA21"

    # ADX rising
    adx_vals = adx(hourly_df["High"], hourly_df["Low"], closes, 14)
    if len(adx_vals) < 3:
        return False, "1H ADX insufficient data"
    last_adx = adx_vals.iloc[-1]
    prev1 = adx_vals.iloc[-2]
    prev2 = adx_vals.iloc[-3]
    if not (last_adx > prev1 and last_adx > prev2):
        return False, f"1H ADX not rising ({prev2:.1f}→{prev1:.1f}→{last_adx:.1f})"

    return True, f"1H MACD +{last_hist:.3f}, EMA9>21, ADX {last_adx:.1f}↑"


def check_daily_range(daily_df):
    """Stock moved 1-2 points in last 3 days."""
    last3 = daily_df.tail(3)
    ranges = (last3["High"] - last3["Low"]).values
    avg_range = np.mean(ranges)
    if avg_range < 1.0:
        return False, f"Avg range ${avg_range:.2f} < $1 (doesn't move enough)"
    return True, f"Avg 3-day range ${avg_range:.2f}"


def check_not_extreme(daily_df):
    """Not at 52-week peak or lowest."""
    closes = daily_df["Close"]
    current = closes.iloc[-1]
    high_52w = closes.max()
    low_52w = closes.min()
    range_52w = high_52w - low_52w
    if range_52w == 0:
        return False, "No price range"

    position = (current - low_52w) / range_52w  # 0 = at low, 1 = at high
    if position > 0.95:
        return False, f"At 52w high ({position:.0%})"
    if position < 0.05:
        return False, f"At 52w low ({position:.0%})"
    return True, f"52w position {position:.0%}"


def check_5m_quality(fivemin_df):
    """5M chart not distorted — volume not dead, spread reasonable."""
    if fivemin_df is None or len(fivemin_df) < 20:
        return False, "Insufficient 5M data"

    # Check last 2 hours (24 candles) of 5M data
    recent = fivemin_df.tail(24)
    avg_vol = recent["Volume"].mean()
    if avg_vol < 1000:
        return False, f"5M avg vol {avg_vol:.0f} (dead)"

    # Spread check: avg (high-low) should not be too wide relative to close
    avg_spread = (recent["High"] - recent["Low"]).mean()
    avg_close = recent["Close"].mean()
    spread_pct = (avg_spread / avg_close) * 100 if avg_close > 0 else 999
    if spread_pct > 1.5:
        return False, f"5M spread {spread_pct:.2f}% (distorted)"

    return True, f"5M vol {avg_vol:.0f}, spread {spread_pct:.2f}%"


# ─── Main Filter Runner ──────────────────────────────────────────

def run_all_filters(ticker, daily_df, hourly_df, fivemin_df):
    """Run ALL filters on a single stock. Returns (passed, reasons)."""
    reasons = []

    # 1. ATR
    ok, msg = check_atr(daily_df)
    if not ok:
        return False, [msg]
    reasons.append(msg)

    # 2. 1D MACD + EMA
    ok, msg = check_1d_macd_ema(daily_df)
    if not ok:
        return False, [msg]
    reasons.append(msg)

    # 3. 4H MACD + EMA (aggregate hourly to 4H first)
    fourh_df = aggregate_4h(hourly_df)
    if len(fourh_df) < 30:
        return False, ["Insufficient 4H data"]
    ok, msg = check_4h_macd_ema(fourh_df)
    if not ok:
        return False, [msg]
    reasons.append(msg)

    # 4. 1H MACD + EMA + ADX
    ok, msg = check_1h_macd_ema_adx(hourly_df)
    if not ok:
        return False, [msg]
    reasons.append(msg)

    # 5. Daily range 1-2 points
    ok, msg = check_daily_range(daily_df)
    if not ok:
        return False, [msg]
    reasons.append(msg)

    # 6. Not at 52w extreme
    ok, msg = check_not_extreme(daily_df)
    if not ok:
        return False, [msg]
    reasons.append(msg)

    # 7. 5M chart quality
    ok, msg = check_5m_quality(fivemin_df)
    if not ok:
        return False, [msg]
    reasons.append(msg)

    return True, reasons


if __name__ == "__main__":
    from step1_finviz import get_candidates
    from step2_download import download_charts, extract_ticker_data

    tickers = get_candidates()
    daily, hourly, fivemin = download_charts(tickers)

    passed = []
    failed_reasons = {}

    for t in tickers:
        d = extract_ticker_data(daily, t)
        h = extract_ticker_data(hourly, t)
        f = extract_ticker_data(fivemin, t)

        if d is None or h is None or len(d) < 30 or len(h) < 30:
            failed_reasons[t] = "Insufficient data"
            continue

        ok, reasons = run_all_filters(t, d, h, f)
        if ok:
            passed.append(t)
            print(f"  ✅ {t}: {' | '.join(reasons)}")
        else:
            failed_reasons[t] = reasons[0]

    print(f"\n{'='*60}")
    print(f"RESULTS: {len(passed)} passed out of {len(tickers)}")
    print(f"Passed: {', '.join(passed) if passed else 'None'}")
    print(f"\nSample rejections:")
    for t, r in list(failed_reasons.items())[:15]:
        print(f"  ❌ {t}: {r}")
