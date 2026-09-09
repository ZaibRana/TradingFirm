"""
TradingFirm — Finnhub context fetchers (Part 2.1).

Five fetchers, each: validate the ticker → Redis cache lookup → one
FinnhubClient call → cache the *raw* Finnhub body → return it. Presentation
is 2.4's job (the dossier), so nothing is reshaped here except in the pure
`*_records` converters that turn raw bodies into db.py row dicts.

    company_news(client, ticker, days=7)   /company-news        TTL 15 min
    recommendations(client, ticker)        /stock/recommendation TTL 24 h
    earnings_calendar(client, ticker)      /calendar/earnings    TTL 24 h
    earnings_surprises(client, ticker)     /stock/earnings       TTL 24 h
    profile(client, ticker)                /stock/profile2       TTL 24 h

Storage (plan: "store news + events"): `sync_context()` fetches news,
calendar and surprises and writes them through db.upsert_news() /
db.upsert_events(). Events:
    ('earnings',          report date)       meta.calendar = {...}
    ('earnings_surprise', fiscal period end) meta.surprise = {...}
Finnhub keys surprises by fiscal period end, not by report date, so the
two cannot share a row; each writer owns its own nested meta key and the
DB merge (existing || new) keeps the other writer's key intact.

Failure policy: Redis absent or failing is fail-open (fetch uncached).
A missing db pool skips storage and logs. Client errors propagate as the
typed FinnhubError family — the caller decides. No retries.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from cache import (
    TTL_FINNHUB_CONTEXT,
    TTL_FINNHUB_NEWS,
    finnhub_key,
    get_cached_json,
    set_cached_json,
)
from providers.context.finnhub_client import FinnhubClient
from tickers import normalize_ticker

logger = logging.getLogger(__name__)

KIND_NEWS = "news"
KIND_RECOMMENDATIONS = "recommendations"
KIND_EARNINGS_CALENDAR = "earnings_calendar"
KIND_EARNINGS_SURPRISES = "earnings_surprises"
KIND_PROFILE = "profile"

EVENT_EARNINGS = "earnings"
EVENT_EARNINGS_SURPRISE = "earnings_surprise"

CALENDAR_LOOKBACK_DAYS = 730
CALENDAR_LOOKAHEAD_DAYS = 120


def _ticker(ticker: str) -> str:
    t = normalize_ticker(ticker)
    if not t.isalpha() or not 1 <= len(t) <= 5:
        raise ValueError(f"Invalid ticker: {ticker!r}")
    return t


async def _cached(
    redis,
    kind: str,
    ticker: str,
    ttl: int,
    fetch: Callable[[], Awaitable[Any]],
) -> tuple[Any, bool]:
    """(body, from_cache). Redis problems are logged and ignored."""
    key = finnhub_key(kind, ticker)
    if redis is not None:
        try:
            body = await get_cached_json(redis, key)
        except Exception as e:
            logger.warning(f"Finnhub cache read failed for {key}: {e}")
            body = None
        if body is not None:
            return body, True

    body = await fetch()

    if redis is not None:
        try:
            await set_cached_json(redis, key, body, ttl)
        except Exception as e:
            logger.warning(f"Finnhub cache write failed for {key}: {e}")
    return body, False


# ── Fetchers (raw bodies) ────────────────────────────────────────────────


async def company_news(
    client: FinnhubClient,
    ticker: str,
    *,
    redis=None,
    days: int = 7,
    today: date | None = None,
) -> list[dict]:
    t = _ticker(ticker)
    today = today or datetime.now(timezone.utc).date()
    params = {
        "symbol": t,
        "from": (today - timedelta(days=days)).isoformat(),
        "to": today.isoformat(),
    }
    body, _ = await _cached(
        redis, KIND_NEWS, t, TTL_FINNHUB_NEWS,
        lambda: client.get("/company-news", **params),
    )
    return body if isinstance(body, list) else []


async def recommendations(client: FinnhubClient, ticker: str, *, redis=None) -> list[dict]:
    t = _ticker(ticker)
    body, _ = await _cached(
        redis, KIND_RECOMMENDATIONS, t, TTL_FINNHUB_CONTEXT,
        lambda: client.get("/stock/recommendation", symbol=t),
    )
    return body if isinstance(body, list) else []


async def earnings_calendar(
    client: FinnhubClient,
    ticker: str,
    *,
    redis=None,
    today: date | None = None,
) -> list[dict]:
    """Past and upcoming report dates: the `earningsCalendar` list."""
    t = _ticker(ticker)
    today = today or datetime.now(timezone.utc).date()
    params = {
        "symbol": t,
        "from": (today - timedelta(days=CALENDAR_LOOKBACK_DAYS)).isoformat(),
        "to": (today + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)).isoformat(),
    }
    body, _ = await _cached(
        redis, KIND_EARNINGS_CALENDAR, t, TTL_FINNHUB_CONTEXT,
        lambda: client.get("/calendar/earnings", **params),
    )
    if isinstance(body, dict):
        items = body.get("earningsCalendar")
        return items if isinstance(items, list) else []
    return []


async def earnings_surprises(client: FinnhubClient, ticker: str, *, redis=None) -> list[dict]:
    t = _ticker(ticker)
    body, _ = await _cached(
        redis, KIND_EARNINGS_SURPRISES, t, TTL_FINNHUB_CONTEXT,
        lambda: client.get("/stock/earnings", symbol=t),
    )
    return body if isinstance(body, list) else []


async def profile(client: FinnhubClient, ticker: str, *, redis=None) -> dict:
    t = _ticker(ticker)
    body, _ = await _cached(
        redis, KIND_PROFILE, t, TTL_FINNHUB_CONTEXT,
        lambda: client.get("/stock/profile2", symbol=t),
    )
    return body if isinstance(body, dict) else {}


# ── Pure converters: raw bodies → db.py row dicts ────────────────────────


def _day(value: str | None) -> datetime | None:
    """'YYYY-MM-DD' → midnight UTC, None if missing or malformed."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def news_records(ticker: str, raw: list[dict]) -> list[dict]:
    """Finnhub /company-news items → upsert_news() rows. Items without a
    url, headline or datetime are dropped (counted in the log)."""
    t = _ticker(ticker)
    rows, dropped = [], 0
    for item in raw or []:
        ts = item.get("datetime")
        url = (item.get("url") or "").strip()
        title = (item.get("headline") or "").strip()
        if not ts or not url or not title:
            dropped += 1
            continue
        rows.append({
            "ticker": t,
            "published_at": datetime.fromtimestamp(int(ts), tz=timezone.utc),
            "source": item.get("source") or "",
            "title": title,
            "url": url,
            "summary": item.get("summary") or "",
        })
    if dropped:
        logger.warning(f"news_records {t}: dropped {dropped} item(s) missing url/headline/datetime")
    return rows


def calendar_events(ticker: str, raw: list[dict]) -> list[dict]:
    """Finnhub earningsCalendar items → ('earnings', report date) rows with
    meta.calendar = the item's estimate/actual fields."""
    t = _ticker(ticker)
    rows, dropped = [], 0
    for item in raw or []:
        at = _day(item.get("date"))
        if at is None:
            dropped += 1
            continue
        rows.append({
            "ticker": t,
            "event_type": EVENT_EARNINGS,
            "event_at": at,
            "meta": {"calendar": {
                "epsEstimate": item.get("epsEstimate"),
                "epsActual": item.get("epsActual"),
                "revenueEstimate": item.get("revenueEstimate"),
                "revenueActual": item.get("revenueActual"),
                "hour": item.get("hour"),
                "quarter": item.get("quarter"),
                "year": item.get("year"),
            }},
        })
    if dropped:
        logger.warning(f"calendar_events {t}: dropped {dropped} item(s) without a date")
    return rows


def surprise_events(ticker: str, raw: list[dict]) -> list[dict]:
    """Finnhub /stock/earnings items → ('earnings_surprise', fiscal period
    end) rows with meta.surprise = actual/estimate/surprise fields."""
    t = _ticker(ticker)
    rows, dropped = [], 0
    for item in raw or []:
        at = _day(item.get("period"))
        if at is None:
            dropped += 1
            continue
        rows.append({
            "ticker": t,
            "event_type": EVENT_EARNINGS_SURPRISE,
            "event_at": at,
            "meta": {"surprise": {
                "actual": item.get("actual"),
                "estimate": item.get("estimate"),
                "surprise": item.get("surprise"),
                "surprisePercent": item.get("surprisePercent"),
                "quarter": item.get("quarter"),
                "year": item.get("year"),
            }},
        })
    if dropped:
        logger.warning(f"surprise_events {t}: dropped {dropped} item(s) without a period")
    return rows


# ── Fetch + store ────────────────────────────────────────────────────────


async def sync_context(
    client: FinnhubClient,
    ticker: str,
    *,
    pool=None,
    redis=None,
    today: date | None = None,
) -> dict:
    """
    Fetch news, earnings calendar and earnings surprises for `ticker` and
    store them. Three Finnhub calls at most (fewer on cache hits). With no
    db pool the fetch still happens and storage is skipped (logged).

    Returns {"ticker", "newsFetched", "newsSent", "eventsSent", "stored"}.
    Finnhub errors propagate; db errors propagate.
    """
    t = _ticker(ticker)
    from db import upsert_events, upsert_news  # deferred: db.py imports asyncpg

    news_raw = await company_news(client, t, redis=redis, today=today)
    cal_raw = await earnings_calendar(client, t, redis=redis, today=today)
    sur_raw = await earnings_surprises(client, t, redis=redis)

    news_rows = news_records(t, news_raw)
    event_rows = calendar_events(t, cal_raw) + surprise_events(t, sur_raw)
    for sample in news_rows[:3]:
        logger.info(f"news {t}: {sample['published_at'].date()} {sample['source']}: {sample['title'][:80]}")
    for sample in event_rows[:3]:
        logger.info(f"event {t}: {sample['event_type']} {sample['event_at'].date()} {sample['meta']}")

    result = {
        "ticker": t,
        "newsFetched": len(news_raw),
        "newsSent": 0,
        "eventsSent": 0,
        "stored": False,
    }
    if pool is None:
        logger.warning(f"sync_context {t}: no db pool, {len(news_rows)} news / {len(event_rows)} events not stored")
        return result

    result["newsSent"] = await upsert_news(pool, news_rows)
    result["eventsSent"] = await upsert_events(pool, event_rows)
    result["stored"] = True
    return result
