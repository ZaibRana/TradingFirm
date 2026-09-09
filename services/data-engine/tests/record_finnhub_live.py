"""
TradingFirm — Record Finnhub fixtures (LIVE, manual, run once).

Not collected by pytest (pytest.ini: test_*.py only). Makes five real
Finnhub calls for one ticker through the real client and limiter (1.2 s
gaps) and writes the raw JSON to tests/fixtures/finnhub/<TICKER>_<kind>.json
for the respx-mocked unit tests.

Also the §18 verification for Part 2.1: a 401/403 on an endpoint means it
is outside the free tier; the script prints that and moves on, so the
report shows exactly which endpoints are reachable before fetchers are
written for them. The key is read from FINNHUB_API_KEY and never printed.

Run (compose injects FINNHUB_API_KEY from .env; source is bind-mounted so
the fixtures land on the host):

    docker compose run --rm --no-deps -v ./services/data-engine:/app \
        data-engine python tests/record_finnhub_live.py AAPL
"""

import asyncio
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.context.finnhub_client import (  # noqa: E402
    FinnhubAuthError,
    FinnhubClient,
    FinnhubError,
    FinnhubRateLimited,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "finnhub"


def _calls(ticker: str, today: date) -> list[tuple[str, str, dict]]:
    """(kind, path, params) in call order. Canary first: profile2 is the
    cheapest, most certainly free endpoint."""
    return [
        ("profile", "/stock/profile2", {"symbol": ticker}),
        ("news", "/company-news", {
            "symbol": ticker,
            "from": (today - timedelta(days=7)).isoformat(),
            "to": today.isoformat(),
        }),
        ("recommendations", "/stock/recommendation", {"symbol": ticker}),
        ("earnings_calendar", "/calendar/earnings", {
            "symbol": ticker,
            "from": (today - timedelta(days=730)).isoformat(),
            "to": (today + timedelta(days=120)).isoformat(),
        }),
        ("earnings_surprises", "/stock/earnings", {"symbol": ticker}),
    ]


def _summarize(kind: str, body) -> str:
    if isinstance(body, list):
        head = json.dumps(body[:3], default=str)
        return f"list of {len(body)}; first 3: {head[:600]}"
    if isinstance(body, dict):
        if kind == "earnings_calendar":
            items = body.get("earningsCalendar", [])
            return f"earningsCalendar of {len(items)}; first 3: {json.dumps(items[:3])[:600]}"
        return f"dict keys={sorted(body)[:12]}; sample: {json.dumps(body, default=str)[:600]}"
    return f"{type(body).__name__}: {str(body)[:200]}"


async def main(ticker: str) -> int:
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key.strip():
        print("FINNHUB_API_KEY is empty in this environment; nothing recorded.")
        return 2
    print(f"key present (length {len(key.strip())}, not shown)")

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    client = FinnhubClient(key)
    today = date.today()
    reachable, unreachable = [], []

    try:
        for i, (kind, path, params) in enumerate(_calls(ticker, today), start=1):
            t0 = time.monotonic()
            try:
                body = await client.get(path, **params)
            except FinnhubRateLimited as e:
                print(f"[{i}/5] {kind:<20} {path}: 429 — STOPPING (G6). {e}")
                unreachable.append((kind, "429"))
                break
            except FinnhubAuthError as e:
                print(f"[{i}/5] {kind:<20} {path}: AUTH/PREMIUM — {e}")
                unreachable.append((kind, "401/403"))
                if i == 1:
                    print("canary failed; not making further calls")
                    break
                continue
            except FinnhubError as e:
                print(f"[{i}/5] {kind:<20} {path}: ERROR — {e}")
                unreachable.append((kind, "error"))
                if i == 1:
                    print("canary failed; not making further calls")
                    break
                continue
            dt = time.monotonic() - t0
            out = FIXTURES_DIR / f"{ticker}_{kind}.json"
            out.write_text(json.dumps(body, indent=1, default=str) + "\n")
            print(f"[{i}/5] {kind:<20} {path}: 200 in {dt:.2f}s -> {out.name} ({out.stat().st_size} B)")
            print(f"        {_summarize(kind, body)}")
            reachable.append(kind)
    finally:
        await client.aclose()

    print(f"\ncalls made: {client.calls_made}; reachable: {reachable}; unreachable: {unreachable}")
    return 0 if reachable else 1


if __name__ == "__main__":
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper().strip()
    raise SystemExit(asyncio.run(main(symbol)))
