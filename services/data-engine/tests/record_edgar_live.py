"""
TradingFirm — Record SEC EDGAR fixtures (LIVE, manual, run once).

Not collected by pytest (pytest.ini: test_*.py only). Makes two real
EDGAR calls for one ticker through the real client and limiter and writes
trimmed JSON to tests/fixtures/edgar/ for the respx-mocked unit tests:

    company_tickers.json      the requested ticker plus a few well-known
                              entries (original shape, re-indexed keys)
    <TICKER>_submissions.json top-level scalars kept, `filings.recent`
                              arrays cut to RECENT_LIMIT rows, `files` []

Also the plan §18 verification for Part 2.2: a 403 means EDGAR rejected
the User-Agent (or throttled); the script prints that and stops (G6).
The User-Agent is read from EDGAR_USER_AGENT and only its length and
shape ("<app> <email>": has a space and an @) are printed.

Run (compose injects EDGAR_USER_AGENT from .env; source is bind-mounted so
the fixtures land on the host):

    docker compose run --rm --no-deps -v ./services/data-engine:/app \
        data-engine python tests/record_edgar_live.py AAPL
"""

import asyncio
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.context.edgar import parse_cik_map, parse_submissions  # noqa: E402
from providers.context.edgar_client import (  # noqa: E402
    EDGAR_TICKERS_URL,
    EdgarClient,
    EdgarError,
    EdgarRateLimited,
    submissions_url,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "edgar"
KEEP_TICKERS = ("AAPL", "MSFT", "GOOG", "GOOGL", "BRK-B")
RECENT_LIMIT = 60  # a full recent block is up to 1000 rows / ~1 MB; the tests need a slice


def _trim_tickers(body: dict, ticker: str) -> dict:
    keep = set(KEEP_TICKERS) | {ticker}
    entries = [e for e in body.values() if isinstance(e, dict) and e.get("ticker") in keep]
    return {str(i): e for i, e in enumerate(entries)}


def _trim_submissions(body: dict) -> dict:
    out = {k: v for k, v in body.items() if k != "filings"}
    recent = (body.get("filings") or {}).get("recent") or {}
    out["filings"] = {
        "recent": {k: (v[:RECENT_LIMIT] if isinstance(v, list) else v) for k, v in recent.items()},
        "files": [],
    }
    return out


async def main(ticker: str) -> int:
    ua = os.environ.get("EDGAR_USER_AGENT", "")
    if not ua.strip():
        print("EDGAR_USER_AGENT is empty in this environment; nothing recorded.")
        return 2
    print(f"user-agent present (length {len(ua.strip())}, has space: {' ' in ua.strip()}, has @: {'@' in ua}; not shown)")

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    client = EdgarClient(ua)
    try:
        t0 = time.monotonic()
        try:
            tickers_body = await client.get_json(EDGAR_TICKERS_URL)
        except EdgarRateLimited as e:
            print(f"[1/2] company_tickers.json: BLOCKED — STOPPING (G6). {e}")
            return 1
        except EdgarError as e:
            print(f"[1/2] company_tickers.json: ERROR — {e}")
            return 1
        mapping = parse_cik_map(tickers_body)
        print(f"[1/2] company_tickers.json: 200 in {time.monotonic() - t0:.2f}s, {len(mapping)} tickers; "
              f"sample {list(mapping.items())[:3]}")
        out = FIXTURES_DIR / "company_tickers.json"
        out.write_text(json.dumps(_trim_tickers(tickers_body, ticker), indent=1) + "\n")
        print(f"        -> {out.name} ({out.stat().st_size} B)")

        cik = mapping.get(ticker)
        if cik is None:
            print(f"{ticker} is not in company_tickers.json; no submissions call made")
            return 1
        print(f"        {ticker} → CIK {cik}")

        t0 = time.monotonic()
        try:
            sub_body = await client.get_json(submissions_url(cik))
        except EdgarRateLimited as e:
            print(f"[2/2] submissions: BLOCKED — STOPPING (G6). {e}")
            return 1
        except EdgarError as e:
            print(f"[2/2] submissions: ERROR — {e}")
            return 1
        recent = (sub_body.get("filings") or {}).get("recent") or {}
        n = len(recent.get("accessionNumber") or [])
        print(f"[2/2] submissions: 200 in {time.monotonic() - t0:.2f}s, top-level keys {sorted(sub_body)[:12]}, "
              f"recent rows {n}, recent columns {sorted(recent)}")
        parsed, oldest = parse_submissions(ticker, cik, sub_body, today=date.today())
        print(f"        rows within 90 days: {len(parsed)}; oldest filingDate in the block: {oldest}")
        for sample in parsed[:3]:
            print(f"        {sample['filed_on']} {sample['form']:<8} {sample['accession']} accepted {sample['accepted_at']}")
            print(f"            {sample['url']}")
            print(f"            meta {json.dumps(sample['meta'])[:300]}")
        forms = {}
        for f in parsed:
            forms[f["form"]] = forms.get(f["form"], 0) + 1
        print(f"        forms in parsed {len(parsed)} rows: {dict(sorted(forms.items(), key=lambda kv: -kv[1]))}")
        out = FIXTURES_DIR / f"{ticker}_submissions.json"
        out.write_text(json.dumps(_trim_submissions(sub_body), indent=1) + "\n")
        print(f"        -> {out.name} ({out.stat().st_size} B)")
    finally:
        await client.aclose()

    print(f"\ncalls made: {client.calls_made}")
    return 0


if __name__ == "__main__":
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper().strip()
    raise SystemExit(asyncio.run(main(symbol)))
