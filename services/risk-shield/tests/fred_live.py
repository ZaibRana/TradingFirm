"""
Manual FRED canary (Part 3.2 decision 11). NEVER run in CI.

    python -m tests.fred_live DGS10     # one series, one request
    python -m tests.fred_live --all     # the 8 series, >= 1 s apart

Run it only through the isolated `docker run` line in docs/specs/3.2.md:
no compose network, DATABASE_URL / REDIS_URL unroutable, FRED_API_KEY the
only value from .env. Writes nothing (no Redis, no Postgres, no files).
Stops at the first error, empty body or unexpected shape (G3.5).

Prints shape and public data only — never the URL, never settings.
"""

import argparse
import asyncio
import json
import sys
import time

from tests.live_guard import assert_isolated_env


async def run(series: list[str]) -> int:
    from datetime import datetime, timezone

    from config import settings
    from monitors.errors import FredError
    from monitors.fred import normalize_series, observations_to_envelope
    from monitors.fred_client import FredClient

    key = settings.fred_api_key.get_secret_value()
    print(f"fred key configured: {bool(key)}")
    client = FredClient(key)
    try:
        for n, raw_id in enumerate(series, start=1):
            sid = normalize_series(raw_id)
            started = time.monotonic()
            try:
                raw = await client.observations(sid)
            except FredError as e:
                elapsed = time.monotonic() - started
                print(f"[{n}] {sid}: {type(e).__name__}: {e} ({elapsed:.2f}s)")
                print("STOP (G3.5): report before any retry")
                return 1
            elapsed = time.monotonic() - started
            env = observations_to_envelope(
                sid, raw, client.observation_start(), datetime.now(timezone.utc)
            )
            raw_obs = raw["observations"]
            missing_values = sorted({str(o.get("value")) for o in raw_obs
                                     if isinstance(o, dict) and not _numeric(o.get("value"))})[:3]
            print(f"[{n}] {sid}: 200 in {elapsed:.2f}s (limiter wait included)")
            print(f"    top-level keys: {sorted(raw.keys())}")
            print(f"    observation fields: {sorted(raw_obs[0].keys()) if raw_obs else []}")
            print(f"    raw count: {len(raw_obs)}  kept: {len(env['observations'])}  "
                  f"dropped: {env['dropped']}  non-numeric values seen: {missing_values}")
            print(f"    observationStart: {env['observationStart']}  reason: {env['reason']}")
            print(f"    first 3: {env['observations'][:3]}")
            print(f"    last 3:  {env['observations'][-3:]}")
            print(f"    key present in body: {key in json.dumps(raw)}")
            if env["reason"] is not None:
                print("STOP (G3.5): empty body — report before continuing")
                return 1
        print(f"requests made: {client.calls_made}")
        return 0
    finally:
        await client.aclose()


def _numeric(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def main() -> None:
    assert_isolated_env()
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("series", nargs="?")
    group.add_argument("--all", action="store_true")
    args = parser.parse_args()

    from monitors.fred import FRED_SERIES
    series = list(FRED_SERIES) if args.all else [args.series]
    sys.exit(asyncio.run(run(series)))


if __name__ == "__main__":
    main()
