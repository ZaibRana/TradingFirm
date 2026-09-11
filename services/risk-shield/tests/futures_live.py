"""
TradingFirm — futures night-bar canary (Part 3.4b step 0). NEVER run in CI.

    python -m tests.futures_live --label R1

Run it only through the isolated `docker run` line in docs/specs/3.4b.md
decision 3: no compose network, unroutable DATABASE_URL / REDIS_URL, and no
key at all (yfinance needs none). It writes nothing — no Redis, no Postgres,
no files. stdout is what the caller redirects into logs/futures-live-<label>.log.

What it answers (spec 3.4b decision 3):
  Q1  does the 1d last close move during the evening session, and does it
      match the 1h last close of the same moment?
  Q2  what date labels the evening bar, before and after UTC midnight?
  Q3  how old is the last 1h bar?
  Q4  does ^VIX carry anything after the 16:15 ET settle?

Nothing here normalizes a bar: the point is what yfinance actually returns.

Exit 1 on a rate-limit record, a raise or an empty frame (G3.5): report
before any retry. Exit 2 is the isolation guard.
"""

import argparse
import asyncio
import gc
import logging
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tests.live_guard import assert_isolated_env

ET = ZoneInfo("America/New_York")
DOWNLOAD_GAP_S = 3      # CLAUDE.md: 3 s between batches
RECENT_BARS = 3         # index labels printed per ticker (Q2)

# In order, the one-ticker canary first (G2, G6): (tickers, interval, period).
STEPS = (
    (("ES=F",), "1d", "5d"),
    (("NQ=F", "^VIX"), "1d", "5d"),
    (("ES=F", "NQ=F", "^VIX"), "1h", "2d"),
)


def now_pair() -> tuple[datetime, str]:
    utc = datetime.now(timezone.utc)
    return utc, f"{utc:%Y-%m-%d %H:%M:%S} UTC / {utc.astimezone(ET):%Y-%m-%d %H:%M:%S} ET"


def stop(reason: str) -> int:
    print(f"STOP (G3.5): {reason} — report before any retry")
    return 1


async def download(tickers, interval: str, period: str):
    """The service's pinned kwargs (threads=False, no session=, its timeout)
    and its log capture, for intervals monitors/quotes.py does not use."""
    import yfinance as yf

    from monitors import quotes

    capture = quotes._LogCapture()
    yf_logger = logging.getLogger("yfinance")
    yf_logger.addHandler(capture)
    try:
        df = await asyncio.to_thread(
            yf.download,
            " ".join(tickers),
            period=period,
            interval=interval,
            group_by="ticker",
            threads=False,
            progress=False,
            auto_adjust=True,
            timeout=quotes.YF_REQUEST_TIMEOUT,
        )
    finally:
        yf_logger.removeHandler(capture)
    return df, capture.messages


def ticker_frame(df, ticker: str, tickers):
    """One ticker's columns, from a MultiIndex frame or a flat single-ticker one."""
    import pandas as pd

    if isinstance(df.columns, pd.MultiIndex):
        if ticker not in df.columns.get_level_values(0):
            return None
        return df[ticker]
    return df if len(tickers) == 1 else None


def report_tickers(df, tickers, utc: datetime) -> dict:
    """Per ticker: rows, the last labels exactly as returned (Q2), the last
    close, and that bar's age (Q3)."""
    out = {}
    for ticker in tickers:
        sub = ticker_frame(df, ticker, tickers)
        if sub is None or "Close" not in sub.columns:
            print(f"    {ticker:6s} absent from the frame")
            continue
        sub = sub.dropna(subset=["Close"])
        if sub.empty:
            print(f"    {ticker:6s} no rows with a close")
            continue
        last, close = sub.index[-1], float(sub["Close"].iloc[-1])
        age = None
        if getattr(last, "tzinfo", None) is not None:
            age = (utc - last.to_pydatetime()).total_seconds() / 60
        print(f"    {ticker:6s} rows={len(sub):4d}  last close={close:.2f}  "
              f"age={'naive index' if age is None else f'{age:.0f} min'}")
        print(f"           last {RECENT_BARS} labels: {[str(i) for i in sub.index[-RECENT_BARS:]]}")
        out[ticker] = {"close": close, "last": str(last), "ageMinutes": age}
    return out


def compare(results: dict) -> None:
    """Q1, within one run: the 1d bar against the 1h bar of the same moment."""
    day, hour = results.get("1d", {}), results.get("1h", {})
    print("\n-- 1d vs 1h (Q1)")
    for ticker in sorted(set(day) & set(hour)):
        d, h = day[ticker]["close"], hour[ticker]["close"]
        gap = (d - h) * 100 / h if h else float("nan")
        print(f"    {ticker:6s} 1d {d:.2f}  1h {h:.2f}  gap {gap:+.3f}%")
        print(f"           1d label {day[ticker]['last']}   1h label {hour[ticker]['last']}")
    print("    Q1 across runs: compare the 1d last closes in the R1 and R2 logs.")


async def run(label: str) -> int:
    import yfinance as yf

    from monitors import quotes

    _, stamp = now_pair()
    print(f"=== futures night-bar canary {label} — {stamp} ===")
    print(f"yfinance {yf.__version__} (expected {quotes.EXPECTED_YF_VERSION})")

    results: dict = {}
    for index, (tickers, interval, period) in enumerate(STEPS):
        if index:
            await asyncio.sleep(DOWNLOAD_GAP_S)
        utc, stamp = now_pair()
        print(f"\n-- {' '.join(tickers)}  interval={interval}  period={period}  at {stamp}")
        started = time.monotonic()
        try:
            df, messages = await download(tickers, interval, period)
        except Exception as e:
            print(f"RAISED after {time.monotonic() - started:.2f}s: {type(e).__name__}: {e}")
            return stop("a download raised")
        print(f"    elapsed {time.monotonic() - started:.2f}s  "
              f"frame {None if df is None else df.shape}  "
              f"index dtype {None if df is None else df.index.dtype}  "
              f"tz {None if df is None else getattr(df.index, 'tz', None)}")
        for message in messages:
            print(f"    yfinance log: {message}")
        if quotes.is_rate_limited(messages):
            return stop("yfinance rate-limited the download")
        if df is None or df.empty:
            return stop("empty frame")

        results.setdefault(interval, {}).update(report_tickers(df, tickers, utc))
        del df           # G8: only the printed numbers survive the step
        gc.collect()

    compare(results)
    print("\nOK — no rate-limit record, no empty frame.")
    return 0


def main() -> None:
    assert_isolated_env()
    parser = argparse.ArgumentParser(description="Part 3.4b step 0: futures night-bar canary")
    parser.add_argument("--label", default="R?", help="run label, e.g. R1")
    sys.exit(asyncio.run(run(parser.parse_args().label)))


if __name__ == "__main__":
    main()
