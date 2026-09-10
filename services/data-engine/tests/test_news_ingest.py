"""
Part 3.5 — POST /news/ingest (spec decision 5).

TestClient over the app without the lifespan: body validation is FastAPI's
job, so the route has to go through it. The asyncpg pool is a mock whose
connection records executemany(), so the real db.upsert_news SQL and rows
are asserted. No network, no real database; the real-Postgres dedup proof is
the twin round trip in the part's verification.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from fastapi.testclient import TestClient

import main

ITEM = {
    "publishedAt": "2026-09-10T17:12:29+00:00",
    "source": "CNBC",
    "title": "OpenAI targets work of Wall Street junior bankers",
    "url": "https://www.cnbc.com/2026/09/10/openai-bankers.html",
    "summary": "A short summary.",
}


def _item(**over):
    body = dict(ITEM)
    body.update(over)
    return body


def _pool(fail=None):
    conn = AsyncMock()
    conn.executemany = AsyncMock(side_effect=fail)
    pool = MagicMock()
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool, conn


@pytest.fixture
def client():
    def _make(pool):
        main.app.state.db_pool = pool
        return TestClient(main.app)
    yield _make
    main.app.state.db_pool = None


def test_ingest_stores_market_rows(client):
    pool, conn = _pool()
    second = _item(url="https://www.reuters.com/markets/oil", title="Oil surges 5%", source="Reuters")
    resp = client(pool).post("/news/ingest", json={"items": [ITEM, second]})

    assert resp.status_code == 200
    assert resp.json() == {"received": 2, "sent": 2}
    conn.executemany.assert_awaited_once()
    sql, records = conn.executemany.await_args.args
    assert "INSERT INTO data_engine.news_items" in sql
    assert records[0] == (
        "_MARKET",
        datetime(2026, 9, 10, 17, 12, 29, tzinfo=timezone.utc),
        "CNBC",
        ITEM["title"],
        ITEM["url"],
        "A short summary.",
    )
    assert [r[0] for r in records] == ["_MARKET", "_MARKET"]


@pytest.mark.parametrize("body", [
    {"items": [_item(ticker="AAPL")]},
    {"items": [_item(image="https://example.com/x.png")]},
    {"items": [ITEM], "ticker": "AAPL"},
], ids=["item-ticker", "item-extra-field", "top-level-extra"])
def test_ingest_rejects_ticker_and_extra_fields(client, body):
    pool, conn = _pool()
    resp = client(pool).post("/news/ingest", json=body)
    assert resp.status_code == 422
    conn.executemany.assert_not_awaited()


BAD_BATCHES = [
    ("naive-publishedAt", [_item(publishedAt="2026-09-10T17:12:29")]),
    ("garbage-publishedAt", [_item(publishedAt="yesterday")]),
    ("missing-title", [{k: v for k, v in ITEM.items() if k != "title"}]),
    ("non-http-url", [_item(url="ftp://example.com/a")]),
    ("url-2049", [_item(url="https://" + "a" * (2049 - 8))]),
    ("blank-title", [_item(title="   ")]),
    ("title-1001", [_item(title="t" * 1001)]),
    ("summary-10001", [_item(summary="s" * 10001)]),
    ("source-101", [_item(source="s" * 101)]),
    ("nul-title", [_item(title="bad\x00title")]),
    ("nul-url", [_item(url="https://example.com/\x00")]),
    ("nul-source", [_item(source="CN\x00BC")]),
    ("nul-summary", [_item(summary="x\x00")]),
    ("zero-items", []),
    ("201-items", [_item(url=f"https://example.com/{i}") for i in range(201)]),
    ("one-bad-among-good", [ITEM, _item(url="https://example.com/b", title="")]),
]


@pytest.mark.parametrize("items", [b for _, b in BAD_BATCHES], ids=[i for i, _ in BAD_BATCHES])
def test_ingest_validation_rejects_whole_batch(client, items):
    pool, conn = _pool()
    resp = client(pool).post("/news/ingest", json={"items": items})
    assert resp.status_code == 422
    conn.executemany.assert_not_awaited()


def test_ingest_validation_accepts_the_limits_exactly(client):
    pool, conn = _pool()
    edge = _item(url="https://" + "a" * (2048 - 8), title="t" * 1000,
                 summary="s" * 10000, source="s" * 100)
    batch = [edge] + [_item(url=f"https://example.com/{i}") for i in range(199)]
    resp = client(pool).post("/news/ingest", json={"items": batch})
    assert resp.status_code == 200
    assert resp.json() == {"received": 200, "sent": 200}
    # source and summary may be omitted
    resp = client(pool).post("/news/ingest", json={"items": [
        {"publishedAt": ITEM["publishedAt"], "title": "t", "url": "https://example.com/min"}]})
    assert resp.status_code == 200


def test_ingest_limits_pinned_to_spec():
    # Spec 3.5 decision 5. risk-shield's converter holds the same numbers
    # (test_converter_limits_pinned_to_spec); change both or neither.
    assert (main.NEWS_INGEST_MAX_ITEMS, main.NEWS_URL_MAX, main.NEWS_TITLE_MAX,
            main.NEWS_SUMMARY_MAX, main.NEWS_SOURCE_MAX) == (200, 2048, 1000, 10000, 100)


def test_ingest_collapses_duplicate_urls(client):
    pool, conn = _pool()
    items = [ITEM, _item(title="Same url, other title"), _item(source="Other")]
    resp = client(pool).post("/news/ingest", json={"items": items})
    assert resp.status_code == 200
    assert resp.json() == {"received": 3, "sent": 1}
    _, records = conn.executemany.await_args.args
    assert len(records) == 1


def test_ingest_db_unavailable_503(client):
    resp = client(None).post("/news/ingest", json={"items": [ITEM]})
    assert resp.status_code == 503
    assert resp.json() == {"detail": "database unavailable"}

    for fail in (asyncpg.PostgresError("boom"), asyncpg.InterfaceError("closed"),
                 ConnectionError("reset"), TimeoutError()):
        pool, conn = _pool(fail=fail)
        resp = client(pool).post("/news/ingest", json={"items": [ITEM]})
        assert resp.status_code == 503, type(fail).__name__
        assert resp.json() == {"detail": "database unavailable"}


def test_ingest_repeat_batch_is_idempotent(client):
    pool, conn = _pool()
    c = client(pool)
    batch = {"items": [ITEM, _item(url="https://example.com/b")]}
    first = c.post("/news/ingest", json=batch)
    second = c.post("/news/ingest", json=batch)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"received": 2, "sent": 2}
    (sql1, rec1), (sql2, rec2) = (call.args for call in conn.executemany.await_args_list)
    assert sql1 == sql2
    assert "ON CONFLICT (ticker, url) DO NOTHING" in sql1
    assert rec1 == rec2
    assert {r[0] for r in rec1} == {"_MARKET"}
