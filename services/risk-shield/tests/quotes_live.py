"""
Manual core-quotes canary (Part 3.2 decision 11). NEVER run in CI.

    python -m tests.quotes_live SPY      # one ticker: 2 requests (cold tz cache)
    python -m tests.quotes_live --core   # the 17: 34 requests (cold tz cache)

Run it only through the isolated `docker run` line in docs/specs/3.2.md.
Writes nothing (no Redis, no Postgres, no files; yfinance's tz cache dies
with the --rm container). Stops on a rate-limit record, a degraded
envelope, a raise, or a run slower than the 255 s cold worst case (G3.5).
"""

import argparse
import asyncio
import sys
import time

from tests.live_guard import assert_isolated_env

COLD_WORST_CASE_S = 255


async def run(tickers: list[str]) -> int:
    from datetime import datetime, timezone

    import yfinance

    from monitors import quotes
    from monitors.errors import QuotesError

    print(f"yfinance {yfinance.__version__} (expected {quotes.EXPECTED_YF_VERSION})")
    print(f"tickers ({len(tickers)}): {' '.join(tickers)}")
    started = time.monotonic()
    try:
        df, messages = await quotes.download_frame(tickers)
    except QuotesError as e:
        print(f"RAISED after {time.monotonic() - started:.2f}s: {e}")
        print("STOP (G3.5): report before any retry")
        return 1
    elapsed = time.monotonic() - started

    print(f"elapsed: {elapsed:.2f}s")
    if df is None:
        print("frame: None (YFRateLimitError raised)")
    else:
        print(f"frame shape: {df.shape}  column names: {list(df.columns.names)}")
        print(f"first 6 columns: {list(df.columns[:6])}")
        print(f"index dtype: {df.index.dtype}  first/last: {df.index[0] if len(df) else None} / "
              f"{df.index[-1] if len(df) else None}")
    env = quotes.build_envelope(df, tickers, lambda: datetime.now(timezone.utc))
    del df
    for t, s in env["tickers"].items():
        print(f"    {t:6s} rows={len(s['date']):3d}  {s['date'][0]} → {s['date'][-1]}  "
              f"last close={s['close'][-1]}  last volume={s['volume'][-1]}")
    print(f"missing: {env['missing']}  reason: {env['reason']}")
    print(f"yfinance log records captured ({len(messages)}):")
    for m in messages:
        print(f"    {m}")
    rate_limited = quotes.is_rate_limited(messages)
    print(f"rate limited: {rate_limited}")

    if rate_limited or env["reason"] is not None or elapsed > COLD_WORST_CASE_S:
        print("STOP (G3.5): report before continuing")
        return 1
    return 0


def main() -> None:
    assert_isolated_env()
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("ticker", nargs="?")
    group.add_argument("--core", action="store_true")
    args = parser.parse_args()

    from cache import canonical
    from monitors.quotes import CORE_TICKERS
    tickers = list(CORE_TICKERS) if args.core else [canonical(args.ticker)]
    sys.exit(asyncio.run(run(tickers)))


if __name__ == "__main__":
    main()
