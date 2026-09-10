"""
TradingFirm — the market news poller (Part 3.5).

Every 15 minutes, around the clock (spec 3.5 decision 2), one Finnhub
general-news call; the whole page goes to data-engine's POST /news/ingest,
which stores it under _MARKET and drops what it already has (decision 3: no
minId, no news state in Redis).

This commit holds the converter only: a Finnhub page → ingest items that can
never fail the route's validation (decision 5). The poll and the loop follow.
"""

import logging
from datetime import datetime, timezone
from typing import Any

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
