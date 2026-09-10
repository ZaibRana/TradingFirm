"""
Manual Finnhub market-news canary (Part 3.5 decision 10). NEVER run in CI.

    python -m tests.finnhub_news_live     # exactly 1 request

Run it only through the isolated `docker run` line in docs/specs/3.5.md
decision 10: bridge network, DATABASE_URL / REDIS_URL unroutable,
FINNHUB_API_KEY the only value from .env. No Redis, no Postgres. Writes one
file: tests/fixtures/finnhub/general_news.json (the page, trimmed to 20
items). Stops at the first error (G3.5).

Its job is the page readout the poller is sized against: page size (a
probable cap), how far back the page reaches, and decision 5's limits. The
first run (2026-09-10, two requests) found a 100-item page spanning ~41 h
and ids in pickup order rather than publish order, which removed `minId`.

Prints shape and public data only — never the key, never settings.
"""

import asyncio
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from tests.live_guard import assert_isolated_env

FIXTURE = Path(__file__).parent / "fixtures" / "finnhub" / "general_news.json"
FIXTURE_ITEMS = 20
CAP_MULTIPLE = 50          # a page of exactly 50, 100, 200 … is a probable cap
CADENCE_MINUTES = 15
# Decision 5's limits, for this report only.
LIMITS = {"url": 2048, "headline": 1000, "summary": 10000, "source": 100}


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def describe(body: list, key: str) -> dict:
    n = len(body)
    cap = n > 0 and n % CAP_MULTIPLE == 0
    print(f"page size {n}{'  PROBABLE CAP' if cap else ''}")
    out = {"n": n, "cap": cap, "span_min": None}
    if not body:
        return out

    items = [it for it in body if isinstance(it, dict)]
    print(f"    non-dict items: {n - len(items)}")
    print(f"    item fields: {sorted({k for it in items for k in it})}")

    times = [t for t in (_int(it.get("datetime")) for it in items) if t is not None]
    print(f"    datetime: {len(times)}/{n} int")
    if times:
        now = time.time()
        span_min = (max(times) - min(times)) / 60
        out["span_min"] = span_min
        print(f"    newest {_iso(max(times))} ({(now - max(times)) / 60:.0f} min ago), "
              f"oldest {_iso(min(times))} ({(now - min(times)) / 60:.0f} min ago)")
        if span_min > 0:
            print(f"    page span {span_min:.0f} min, items per {CADENCE_MINUTES} min "
                  f"{n / span_min * CADENCE_MINUTES:.1f}")
        else:
            print(f"    page span {span_min:.0f} min")

    print(f"    category values: {dict(Counter(str(it.get('category')) for it in items))}")
    related = Counter(str(it.get("related")) for it in items)
    print(f"    related: empty {related.get('', 0)}/{len(items)}, top {related.most_common(5)}")

    for field, limit in LIMITS.items():
        lengths = [len(it[field]) for it in items if isinstance(it.get(field), str)]
        over = sum(1 for length in lengths if length > limit)
        missing = len(items) - len(lengths)
        print(f"    {field}: longest {max(lengths) if lengths else None}, over {limit}: {over}, "
              f"missing/non-str: {missing}")
    nul = sum(1 for it in items for v in it.values() if isinstance(v, str) and "\x00" in v)
    bad_scheme = sum(1 for it in items
                     if not str(it.get("url") or "").startswith(("http://", "https://")))
    print(f"    fields containing NUL: {nul}; urls not http(s): {bad_scheme}")

    for it in items[:3]:
        t = _int(it.get("datetime"))
        print(f"    sample: {_iso(t) if t else None} {it.get('source')}: {str(it.get('headline'))[:80]}")
    print(f"    key present in body: {key in json.dumps(body)}")
    return out


async def run() -> int:
    from config import settings
    from monitors.errors import FinnhubError
    from monitors.finnhub_client import FinnhubClient

    key = settings.finnhub_api_key.get_secret_value()
    print(f"finnhub key configured: {bool(key)}")
    if not key:
        print("STOP: FINNHUB_API_KEY is empty in this container, no request made")
        return 1

    client = FinnhubClient(key)
    try:
        started = time.monotonic()
        try:
            page = await client.general_news()
        except FinnhubError as e:
            print(f"{type(e).__name__}: {e} ({time.monotonic() - started:.2f}s)")
            print("STOP (G3.5): report before any retry")
            return 1
        print(f"200 in {time.monotonic() - started:.2f}s (limiter wait included)")
        d = describe(page, key)
        print(f"requests made: {client.calls_made}")
        if not page:
            print("STOP (G11): the page is empty — investigate before continuing")
            return 1

        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(page[:FIXTURE_ITEMS], indent=2, ensure_ascii=False) + "\n")
        print(f"fixture: wrote {min(len(page), FIXTURE_ITEMS)} of {len(page)} items to "
              f"tests/fixtures/finnhub/{FIXTURE.name}")

        if d["cap"] and d["span_min"] is not None and d["span_min"] < CADENCE_MINUTES:
            print(f"STOP (G3.5): capped page spans under {CADENCE_MINUTES} min — "
                  "revisit minId with a pickup-order check (spec 3.5, carried forward)")
            return 1
        return 0
    finally:
        await client.aclose()


def main() -> None:
    assert_isolated_env()
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
