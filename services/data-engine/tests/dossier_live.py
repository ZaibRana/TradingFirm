"""
LIVE CHECK — Part 2.5. Manual, one ticker, real Finnhub / SEC EDGAR /
yfinance calls. NEVER run in CI, never from pytest (the filename is not
`test_*.py`, so `pytest.ini`'s `python_files` will not collect it).

Prints, for one dossier:
  * every upstream call made, with its own wall-clock time
  * every section's status and how much it carries
  * the budget the endpoint reports
  * a second, cached request: `cached: true` and no upstream call

It drives `main.get_dossier` in-process rather than over HTTP, so the client
objects can be wrapped to time each call. Everything else — the pool, Redis
(the same keys the running service uses), the clients, the refresh helper —
is what `lifespan` builds, from the same settings.

Usage, in a throwaway container off the prod image (the 2.2/2.3 pattern):

    docker compose run --rm --no-deps -v ./services/data-engine:/app \
        data-engine python tests/dossier_live.py AAPL
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Run as `python tests/dossier_live.py`: the service package is the parent.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main
from cache import create_redis
from config import settings
from db import create_db_pool
from providers import get_provider
from providers.context.alphavantage_client import AlphaVantageClient
from providers.context.edgar_client import EdgarClient
from providers.context.finnhub_client import FinnhubClient

CALLS: list[tuple[str, str, float, str]] = []   # (source, what, seconds, outcome)


def _timed(source: str, fn, label):
    """Wrap one client coroutine method so every call it makes is recorded."""
    async def wrapper(*args, **kwargs):
        what = label(*args, **kwargs)
        started = time.perf_counter()
        try:
            result = await fn(*args, **kwargs)
        except Exception as e:
            CALLS.append((source, what, time.perf_counter() - started, type(e).__name__))
            raise
        CALLS.append((source, what, time.perf_counter() - started, "ok"))
        return result
    return wrapper


async def build_state(ticker: str):
    """What lifespan builds, minus the scanner. Redis and Postgres are the
    ones the running service uses, so the cache is shared and real."""
    main.app.state.memory = main.InMemoryStore()
    main.app.state.redis = await create_redis()
    main.app.state.db_pool = await create_db_pool()
    main.app.state.provider = get_provider(settings.data_provider)
    main.app.state.av_client = AlphaVantageClient(settings.alphavantage_api_key)
    main.app.state.finnhub = FinnhubClient(settings.finnhub_api_key)
    main.app.state.edgar = EdgarClient(settings.edgar_user_agent)

    finnhub, edgar = main.app.state.finnhub, main.app.state.edgar
    finnhub.get = _timed("finnhub", finnhub.get, lambda path, **kw: path)
    edgar.get_json = _timed(
        "edgar", edgar.get_json,
        lambda url: "company_tickers" if "company_tickers" in url else "submissions",
    )
    provider = main.app.state.provider
    for name in ("download_daily", "download_hourly", "get_earnings_dates"):
        fn = getattr(provider, name, None)
        if fn is not None:
            # `label=name` binds the loop variable per iteration: without it
            # every provider call is reported under the last name.
            setattr(provider, name, _timed("yfinance", fn, lambda *a, label=name, **k: label))
    av = main.app.state.av_client
    av.get = _timed("alphavantage", av.get, lambda function, symbol: function)

    print(f"configured — finnhub: {finnhub.configured}, edgar: {edgar.configured}, "
          f"alphavantage: {av.configured}, provider: {provider.provider_name}")


def report(label: str, response, elapsed: float):
    body = response.model_dump(mode="json", by_alias=True)
    print(f"\n=== {label} — {elapsed:.2f}s wall clock ===")
    print(f"ticker {body['ticker']}  horizon {body['horizon']}  "
          f"asOf {body['asOf']}  cached {body['cached']}")
    print(f"budget {json.dumps(body['budget'])}")
    print(f"{'section':16} {'status':13} carries")
    for name, section in body["sections"].items():
        carries = {
            k: (len(v) if isinstance(v, list) else v)
            for k, v in section.items()
            if k in ("items", "rows", "count", "truncated", "reactions", "refreshed",
                     "staleWeekdays", "lastBarDate", "dataQuality", "bars", "close", "name")
            and v is not None
        }
        reason = f" reason={section['reason']}" if section.get("reason") else ""
        print(f"{name:16} {section['status']:13} {carries}{reason}")
    return body


async def main_async(ticker: str):
    await build_state(ticker)
    print(f"\nstarted {datetime.now(timezone.utc).isoformat()}  ticker {ticker}")

    started = time.perf_counter()
    # Called in-process, so the Query(...) default has to be passed by hand.
    first = await main.get_dossier(ticker, horizon="swing")
    first_elapsed = time.perf_counter() - started
    body = report("first request (uncached)", first, first_elapsed)

    print(f"\n{'source':14} {'call':22} {'seconds':>8}  outcome")
    for source, what, seconds, outcome in CALLS:
        print(f"{source:14} {what:22} {seconds:8.2f}  {outcome}")
    print(f"{'':14} {'TOTAL ' + str(len(CALLS)) + ' calls':22} "
          f"{sum(c[2] for c in CALLS):8.2f}")

    before = len(CALLS)
    started = time.perf_counter()
    second = await main.get_dossier(ticker, horizon="swing")
    second_body = report("second request (should be cached)", second, time.perf_counter() - started)
    print(f"upstream calls during the second request: {len(CALLS) - before}")

    assert second_body["cached"] is True, "second request was not served from the cache"
    assert second_body["budget"]["upstreamCalls"] == 0, "cached body reports upstream calls"
    assert len(CALLS) == before, "the cached request still went upstream"
    print("\nOK: second request cached, zero upstream calls.")

    await main.app.state.db_pool.close()
    await main.app.state.redis.close()
    for client in (main.app.state.finnhub, main.app.state.edgar, main.app.state.av_client):
        await client.aclose()
    return body


if __name__ == "__main__":
    ticker = (sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper()
    asyncio.run(main_async(ticker))
