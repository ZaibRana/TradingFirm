"""Part 3.5 — the market news poller. Converter rows (commit 4a) on the
2026-09-10 Finnhub fixture; no network. The twin guard is here too, green
before any poller code runs inside the twin."""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

import config
import news_poller
from news_poller import market_news_items

FIXTURE = Path(__file__).parent / "fixtures" / "finnhub" / "general_news.json"
ITEM_KEYS = {"publishedAt", "title", "url", "source", "summary"}


def _page():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _raw(**over):
    item = {"category": "business", "datetime": 1757524349, "headline": "Headline", "id": 1,
            "image": "", "related": "", "source": "Reuters", "summary": "Summary.",
            "url": "https://example.com/a"}
    item.update(over)
    return item


def _fake_ingest_status(items):
    """data-engine's /news/ingest validation, restated from spec 3.5 decision 5
    with the pinned numbers: 200 when the batch would pass, else 422."""
    if not 1 <= len(items) <= 200:
        return 422
    for item in items:
        if set(item) - ITEM_KEYS or not {"publishedAt", "title", "url"} <= set(item):
            return 422
        if any("\x00" in str(v) for v in item.values()):
            return 422
        at = datetime.fromisoformat(item["publishedAt"])
        if at.tzinfo is None:
            return 422
        if not item["url"].startswith(("http://", "https://")) or len(item["url"]) > 2048:
            return 422
        if not item["title"].strip() or len(item["title"]) > 1000:
            return 422
        if len(item.get("summary", "")) > 10000 or len(item.get("source", "")) > 100:
            return 422
    return 200


def test_market_news_items_from_fixture_and_drops():
    page = _page()
    assert len(page) == 20
    items, counts = market_news_items(page)
    assert counts == {"fetched": 20, "kept": 20, "dropped": 0, "truncated": 0}
    assert all(set(item) == ITEM_KEYS for item in items)
    stamps = [datetime.fromisoformat(item["publishedAt"]) for item in items]
    assert stamps == sorted(stamps)                                   # oldest first
    assert all(s.utcoffset().total_seconds() == 0 for s in stamps)    # aware UTC
    newest = max(page, key=lambda it: it["datetime"])
    assert items[-1]["url"] == newest["url"] and items[-1]["title"] == newest["headline"]

    bad = [
        "not an object",
        {k: v for k, v in _raw().items() if k != "url"},
        {k: v for k, v in _raw().items() if k != "headline"},
        {k: v for k, v in _raw().items() if k != "datetime"},
        _raw(url="ftp://example.com/a"),
        _raw(url="   "),
        _raw(headline="   "),
        _raw(datetime=0),
        _raw(datetime=-5),
        _raw(datetime=True),
        _raw(datetime="1757524349"),
        _raw(datetime=1757524349.5),
        _raw(datetime=10**20),                                        # out of range
    ]
    items, counts = market_news_items(page + bad)
    assert counts == {"fetched": 33, "kept": 20, "dropped": 13, "truncated": 0}

    assert market_news_items([]) == ([], {"fetched": 0, "kept": 0, "dropped": 0, "truncated": 0})
    assert market_news_items({"error": "not a list"})[1]["fetched"] == 0


def test_converter_limits_pinned_to_spec():
    # Spec 3.5 decision 5. data-engine's route holds the same numbers
    # (test_ingest_limits_pinned_to_spec); change both or neither.
    assert (news_poller.NEWS_INGEST_MAX_ITEMS, news_poller.NEWS_URL_MAX, news_poller.NEWS_TITLE_MAX,
            news_poller.NEWS_SUMMARY_MAX, news_poller.NEWS_SOURCE_MAX) == (200, 2048, 1000, 10000, 100)


def test_oversize_item_truncated_or_dropped_before_send():
    raw = [
        _raw(url="https://example.com/" + "u" * 2500, headline="long url"),
        _raw(url="https://example.com/\x00long", datetime=1757524350,
             headline="\x00" + "T" * 1500, summary="S\x00" * 10000, source="R\x00" * 150),
        _raw(url="https://example.com/nul-only", datetime=1757524351, headline="\x00 \x00"),
    ]
    # The fake ingest really rejects the untreated item: the check has teeth.
    untreated = {"publishedAt": "2026-09-10T17:12:30+00:00", "title": raw[1]["headline"],
                 "url": raw[1]["url"], "source": raw[1]["source"], "summary": raw[1]["summary"]}
    assert _fake_ingest_status([untreated]) == 422

    items, counts = market_news_items(raw)
    assert counts == {"fetched": 3, "kept": 1, "dropped": 2, "truncated": 1}
    (item,) = items
    assert item["url"] == "https://example.com/long"
    assert item["title"] == "T" * 1000
    assert item["summary"] == "S" * 10000
    assert item["source"] == "R" * 100
    assert not any("\x00" in v for v in item.values())
    assert _fake_ingest_status(items) == 200

    # The whole recorded page passes too, chunked at the pinned size.
    fixture_items, _ = market_news_items(_page())
    assert _fake_ingest_status(fixture_items[:news_poller.NEWS_INGEST_MAX_ITEMS]) == 200


def test_twin_never_ingests_into_prod_data_engine():
    """The dev twin must never reach prod's data-engine (and so prod's
    database), never poll, and hold no Finnhub key. Guards the compose
    overrides DATA_ENGINE_URL / NEWS_POLL_ENABLED / FINNHUB_API_KEY: fails if
    one is dropped. Runs only inside the twin (SERVICE_NAME=risk-shield-dev)."""
    if os.environ.get("SERVICE_NAME") != "risk-shield-dev":
        pytest.skip("not the risk-shield dev twin")
    live = config.Settings()
    assert live.data_engine_url == "http://data-engine-dev:8001"
    assert "//data-engine:" not in live.data_engine_url
    assert live.news_poll_enabled is False
    assert live.finnhub_configured is False
