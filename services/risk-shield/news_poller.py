"""
TradingFirm — the market news poller (Part 3.5).

Every 15 minutes, around the clock (spec 3.5 decision 2), one Finnhub
general-news call; the whole page goes to data-engine's POST /news/ingest,
which stores it under _MARKET and drops what it already has (decision 3: no
minId, no news state in Redis).

  market_news_items  a Finnhub page → ingest items that can never fail the
                     route's validation (decision 5)
  poll_once          one poll: skips → one Finnhub call → convert → overlap
                     check → POST in chunks → news_status (decision 7)

A poll is a success only when Finnhub answered a non-empty page, at least
one item survived conversion and every chunk answered 200 (addition 8).
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from cache import (
    DATA_ENGINE_FINNHUB_COOLDOWN_KEY,
    SOURCE_FINNHUB,
    TTL_COOLDOWN_FINNHUB,
    TTL_COOLDOWN_FINNHUB_AUTH,
    cooldown_remaining,
    start_cooldown,
)
from config import settings
from monitors.errors import FinnhubError, FinnhubNotAuthorized, FinnhubRateLimited

logger = logging.getLogger(__name__)

# ── Limits: spec 3.5 decision 5's table ──────────────────────────
# data-engine's /news/ingest holds the same numbers and answers 422 on a
# violation; this side makes a violation impossible before sending. The two
# services share no package, so each keeps a copy, pinned by a test on each
# side (test_converter_limits_pinned_to_spec here,
# test_ingest_limits_pinned_to_spec in data-engine). Change both or neither.
NEWS_INGEST_MAX_ITEMS = 200
NEWS_URL_MAX = 2048
NEWS_TITLE_MAX = 1000
NEWS_SUMMARY_MAX = 10000
NEWS_SOURCE_MAX = 100

_SCHEMES = ("http://", "https://")


def _clean(value: Any) -> str:
    """A string field without NUL (Postgres TEXT rejects it); a missing or
    non-string value is empty."""
    return value.replace("\x00", "") if isinstance(value, str) else ""


def _published_at(value: Any) -> datetime | None:
    """Finnhub's unix `datetime` → aware UTC, None when it is not a positive
    int or out of range."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def market_news_items(raw: Any) -> tuple[list[dict], dict]:
    """
    A Finnhub general-news page → (items for /news/ingest, counts).

    Items are {publishedAt, title, url, source, summary}, oldest first.
    Dropped (counted): not an object, no usable `datetime`, a url that is not
    http(s) or is longer than NEWS_URL_MAX, a blank headline. Truncated
    (counted per item): title, summary, source past their limits. NUL is
    stripped from every field first.

    Counts: {fetched, kept, dropped, truncated}.
    """
    page = raw if isinstance(raw, list) else []
    kept: list[tuple[datetime, dict]] = []
    dropped = truncated = 0
    for item in page:
        if not isinstance(item, dict):
            dropped += 1
            continue
        at = _published_at(item.get("datetime"))
        url = _clean(item.get("url")).strip()
        title = _clean(item.get("headline")).strip()
        if at is None or not title or not url.startswith(_SCHEMES) or len(url) > NEWS_URL_MAX:
            dropped += 1
            continue

        source = _clean(item.get("source")).strip()
        summary = _clean(item.get("summary"))
        cut = (len(title) > NEWS_TITLE_MAX or len(source) > NEWS_SOURCE_MAX
               or len(summary) > NEWS_SUMMARY_MAX)
        if cut:
            truncated += 1
        kept.append((at, {
            "publishedAt": at.isoformat(),
            "title": title[:NEWS_TITLE_MAX],
            "url": url,
            "source": source[:NEWS_SOURCE_MAX],
            "summary": summary[:NEWS_SUMMARY_MAX],
        }))

    kept.sort(key=lambda pair: pair[0])
    items = [item for _, item in kept]
    counts = {"fetched": len(page), "kept": len(items), "dropped": dropped, "truncated": truncated}
    if dropped or truncated:
        logger.info(f"News page: {dropped} item(s) dropped, {truncated} truncated to the ingest limits")
    return items, counts


# ── One poll (decision 7) ────────────────────────────────────────

INGEST_TIMEOUT = 10.0     # hard bound per POST (decision 6); read at call time


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def initial_news_status() -> dict:
    """app.state.news_status before the first poll: a small dict (G8)."""
    return {
        "lastPollAt": None, "lastSuccessAt": None,
        "fetched": None, "sent": None, "dropped": None, "truncated": None,
        "pageSpanMinutes": None, "oldestAt": None,
        "lastError": None, "consecutive422": 0,
    }


async def _data_engine_cooling_down(r) -> bool:
    """data-engine's Finnhub cooldown (decision 4). Read-only: one TTL, never
    a write. Absent, no expiry, unreadable or no Redis all read as clear."""
    if r is None:
        return False
    try:
        left = await r.ttl(DATA_ENGINE_FINNHUB_COOLDOWN_KEY)
    except Exception as e:
        logger.warning(f"News poll: data-engine's Finnhub cooldown unreadable, treating as clear: {e!r}")
        return False
    return isinstance(left, int) and left > 0


def _end(status: dict, outcome: str, cause: str, level: int, message: str) -> dict:
    status["lastError"] = cause
    logger.log(level, message)
    return {"outcome": outcome, "cause": cause}


async def poll_once(state, client, http, *, clock: Callable[[], datetime] = _utc_now) -> dict:
    """
    One poll on `state` (redis, cooldowns, news_status) with a FinnhubClient
    and an httpx.AsyncClient for data-engine. Returns {outcome, cause}:
    outcome "success" | "skipped" | "failed". Every dependency failure is a
    WARNING and a `lastError`, never a raise; only a success moves
    `lastSuccessAt`. Redis is read only for the two cooldowns: no news state.
    """
    now = clock()
    status = state.news_status
    status["lastPollAt"] = now.isoformat()
    r = getattr(state, "redis", None)
    memory = getattr(state, "cooldowns", None)

    # 1. Skips: no request.
    if not client.configured:
        return _end(status, "skipped", "skipped: unconfigured", logging.WARNING,
                    "News poll skipped: FINNHUB_API_KEY is not set")
    left = await cooldown_remaining(r, memory, SOURCE_FINNHUB, TTL_COOLDOWN_FINNHUB)
    if left is not None:
        return _end(status, "skipped", "skipped: cooldown", logging.INFO,
                    f"News poll skipped: Finnhub cooldown, {left}s left")
    if await _data_engine_cooling_down(r):
        return _end(status, "skipped", "skipped: data-engine cooldown", logging.INFO,
                    "News poll skipped: data-engine's Finnhub cooldown is running")

    # 2. One Finnhub call. A refusal is account-level: start the cooldown.
    try:
        page = await client.general_news()
    except FinnhubRateLimited as e:
        await start_cooldown(r, memory, SOURCE_FINNHUB, TTL_COOLDOWN_FINNHUB)
        return _end(status, "failed", f"finnhub: {type(e).__name__}", logging.WARNING,
                    f"News poll: {e}; Finnhub cooldown {TTL_COOLDOWN_FINNHUB}s")
    except FinnhubNotAuthorized as e:
        await start_cooldown(r, memory, SOURCE_FINNHUB, TTL_COOLDOWN_FINNHUB_AUTH)
        return _end(status, "failed", f"finnhub: {type(e).__name__}", logging.WARNING,
                    f"News poll: {e}; Finnhub cooldown {TTL_COOLDOWN_FINNHUB_AUTH}s")
    except FinnhubError as e:
        return _end(status, "failed", f"finnhub: {type(e).__name__}", logging.WARNING, f"News poll: {e}")

    # 3. Convert. Without minId a real page is never empty (addition 8).
    items, counts = market_news_items(page)
    status.update(fetched=counts["fetched"], dropped=counts["dropped"],
                  truncated=counts["truncated"], sent=0)
    if counts["fetched"] == 0:
        return _end(status, "failed", "finnhub: empty page", logging.WARNING,
                    "News poll: Finnhub returned an empty page")
    if not items:
        return _end(status, "failed", "convert: every item dropped", logging.WARNING,
                    f"News poll: every one of {counts['fetched']} items was dropped")
    for item in reversed(items[-3:]):
        logger.info(f"News: {item['publishedAt']} {item['source']}: {item['title'][:80]}")

    # 4. Overlap against the previous success, then this page's shape.
    oldest = datetime.fromisoformat(items[0]["publishedAt"])
    newest = datetime.fromisoformat(items[-1]["publishedAt"])
    previous = status.get("lastSuccessAt")
    if previous is not None and oldest > datetime.fromisoformat(previous):
        logger.warning(
            f"News page does not reach back to the previous successful poll ({previous}; "
            f"oldest item {oldest.isoformat()}): items may have been missed"
        )
    status.update(pageSpanMinutes=int((newest - oldest).total_seconds() // 60),
                  oldestAt=oldest.isoformat())

    # 5. POST in chunks, oldest first.
    url = f"{settings.data_engine_url.rstrip('/')}/news/ingest"
    sent = 0
    for start in range(0, len(items), NEWS_INGEST_MAX_ITEMS):
        chunk = items[start:start + NEWS_INGEST_MAX_ITEMS]
        try:
            resp = await asyncio.wait_for(http.post(url, json={"items": chunk}), timeout=INGEST_TIMEOUT)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            # First: asyncio.TimeoutError is TimeoutError, an OSError.
            return _end(status, "failed", "ingest: timeout", logging.WARNING,
                        f"News ingest timed out after {sent} of {len(items)} items")
        except (httpx.HTTPError, OSError) as e:
            return _end(status, "failed", f"ingest: {type(e).__name__}", logging.WARNING,
                        f"News ingest unreachable ({type(e).__name__}) after {sent} of {len(items)} items")
        if resp.status_code == 422:
            status["consecutive422"] = status.get("consecutive422", 0) + 1
            n = status["consecutive422"]
            if n == 1:
                level, message = logging.ERROR, (
                    "News ingest rejected the batch (422): the limit copies in risk-shield and "
                    "data-engine have drifted (spec 3.5 decision 5)")
            else:
                level, message = logging.WARNING, f"News ingest still rejecting (422), {n} slots in a row"
            return _end(status, "failed", "ingest: HTTP 422", level, message)
        if resp.status_code != 200:
            return _end(status, "failed", f"ingest: HTTP {resp.status_code}", logging.WARNING,
                        f"News ingest answered HTTP {resp.status_code} after {sent} of {len(items)} items")
        sent += len(chunk)
        status["sent"] = sent

    # 6. Success: the only place lastSuccessAt moves and the 422 count resets.
    status.update(lastSuccessAt=now.isoformat(), lastError=None, consecutive422=0)
    logger.info(
        f"News poll: {counts['fetched']} fetched, {sent} sent, {counts['dropped']} dropped, "
        f"{counts['truncated']} truncated, page span {status['pageSpanMinutes']} min"
    )
    return {"outcome": "success", "cause": None}
