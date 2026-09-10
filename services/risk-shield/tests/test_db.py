"""Part 3.1 — db.py: the error tuple and the bounded pool factory.
asyncpg.create_pool is patched; nothing opens a socket."""

import asyncio
import json
from datetime import datetime, timezone

import asyncpg
import pytest

import db


def test_db_errors_membership():
    assert db.DB_ERRORS == (asyncpg.PostgresError, asyncpg.InterfaceError, ConnectionError)


def test_db_errors_excludes_timeout():
    """The Part 2.4 regression: asyncio.TimeoutError *is* the builtin
    TimeoutError, which subclasses OSError. With OSError in the tuple,
    every timed-out call would be reported as a dead database."""
    assert asyncio.TimeoutError is TimeoutError
    assert issubclass(TimeoutError, OSError)
    assert OSError not in db.DB_ERRORS
    assert not issubclass(TimeoutError, db.DB_ERRORS)
    assert not isinstance(asyncio.TimeoutError(), db.DB_ERRORS)


@pytest.mark.asyncio
async def test_create_db_pool_uses_stripped_dsn_and_pinned_sizes(monkeypatch):
    seen = {}

    async def fake_create_pool(**kwargs):
        seen.update(kwargs)
        return "POOL"

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(
        db.settings, "database_url",
        "postgresql+asyncpg://tf_user:pw@postgres:5432/tradingfirm_dev",
    )
    pool = await db.create_db_pool()
    assert pool == "POOL"
    assert seen["dsn"] == "postgresql://tf_user:pw@postgres:5432/tradingfirm_dev"
    assert "+asyncpg" not in seen["dsn"]
    assert seen["min_size"] == 2
    assert seen["max_size"] == 10
    assert seen["command_timeout"] == 30
    assert seen["timeout"] == 5.0          # decision 6: bounds connecting


@pytest.mark.asyncio
async def test_create_db_pool_reads_startup_timeout_at_call_time(monkeypatch):
    """The bound comes from config at call time, so a test (and 3.4) can
    patch it — no from-import copy."""
    seen = {}

    async def fake_create_pool(**kwargs):
        seen.update(kwargs)
        return "POOL"

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr("config.STARTUP_TIMEOUT", 0.25)
    await db.create_db_pool()
    assert seen["timeout"] == 0.25


@pytest.mark.asyncio
async def test_create_db_pool_logs_dsn_without_password(monkeypatch, caplog):
    password = "p4ssw0rd-3f9ac1"

    async def fake_create_pool(**kwargs):
        return "POOL"

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(
        db.settings, "database_url",
        f"postgresql+asyncpg://tf_user:{password}@postgres:5432/tradingfirm_dev",
    )
    with caplog.at_level("DEBUG"):
        await db.create_db_pool()
    joined = "\n".join(f"{r.getMessage()} {r.args}" for r in caplog.records)
    assert password not in joined
    assert "tf_user" not in joined
    assert "postgres:5432/tradingfirm_dev" in joined


@pytest.mark.asyncio
async def test_create_db_pool_raise_propagates(monkeypatch):
    """Closed here; the lifespan is what catches it (test_lifespan.py)."""
    async def fake_create_pool(**kwargs):
        raise asyncpg.InvalidCatalogNameError("database does not exist")

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    with pytest.raises(asyncpg.PostgresError):
        await db.create_db_pool()


# ── risk.health_checks (Part 3.4) ────────────────────────────────

AT = datetime(2026, 9, 10, 20, 20, tzinfo=timezone.utc)
PREV = datetime(2026, 9, 9, 20, 20, tzinfo=timezone.utc)


class _RecordingPool:
    def __init__(self, row=None, rows=()):
        self.row, self.rows, self.calls = row, list(rows), []

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.row

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.rows


def _flat(sql):
    return " ".join(sql.split())


@pytest.mark.asyncio
async def test_insert_health_check_sql_and_params():
    pool = _RecordingPool()
    health = {"score": 64, "regime": "CAUTIOUS", "coverage": 80, "stale": True, "staleMonitors": ["vix"],
              "checkedAt": AT.isoformat(), "monitors": {"vix": {"score": 60}}, "inputs": {"source": "last_known"}}
    await db.insert_health_check(pool, health, "settle", "declining", {"score": 70, "checkedAt": PREV})
    [(op, sql, args)] = pool.calls
    assert op == "execute"
    assert _flat(sql) == ("INSERT INTO risk.health_checks (checked_at, score, regime, trend, indicators) "
                          "VALUES ($1, $2, $3, $4, $5::jsonb)")
    assert args[:4] == (AT, 64, "CAUTIOUS", "declining")
    assert args[0].tzinfo is not None
    assert json.loads(args[4]) == {
        "kind": "settle", "coverage": 80, "stale": True, "staleMonitors": ["vix"],
        "monitors": {"vix": {"score": 60}}, "inputs": {"source": "last_known"},
        "settleScore": 70, "settleCheckedAt": "2026-09-09T20:20:00+00:00",
    }
    # No settle base; a NaN never reaches SQL.
    health["monitors"]["vix"]["raw"] = {"level": float("nan")}
    pool.calls.clear()
    with pytest.raises(ValueError):
        await db.insert_health_check(pool, health, "market", None, None)
    assert pool.calls == []
    assert db.DB_FAILURES == (*db.DB_ERRORS, TimeoutError)


@pytest.mark.asyncio
async def test_latest_health_check_queries():
    row = {"checked_at": AT, "score": 64, "regime": "CAUTIOUS", "trend": None, "indicators": "{}"}
    pool = _RecordingPool(row=row)
    assert await db.latest_health_check(pool) == row
    assert await db.latest_scored_health_check(pool) == row
    (_, latest_sql, latest_args), (_, scored_sql, scored_args) = pool.calls
    assert _flat(latest_sql) == ("SELECT checked_at, score, regime, trend, indicators "
                                 "FROM risk.health_checks ORDER BY checked_at DESC LIMIT 1")
    assert _flat(scored_sql) == ("SELECT checked_at, score, regime FROM risk.health_checks "
                                 "WHERE score IS NOT NULL ORDER BY checked_at DESC LIMIT 1")
    assert latest_args == scored_args == ()
    empty = _RecordingPool(row=None)
    assert await db.latest_health_check(empty) is None
    assert await db.latest_scored_health_check(empty) is None


@pytest.mark.asyncio
async def test_settle_base_query():
    before = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)
    pool = _RecordingPool(row={"checked_at": PREV, "score": 70})
    assert await db.settle_base(pool, before) == {"score": 70, "checkedAt": PREV}
    [(op, sql, args)] = pool.calls
    assert op == "fetchrow" and args == (before,)
    assert _flat(sql) == ("SELECT checked_at, score FROM risk.health_checks "
                          "WHERE indicators->>'kind' = 'settle' AND score IS NOT NULL AND checked_at < $1 "
                          "ORDER BY checked_at DESC LIMIT 1")
    assert await db.settle_base(_RecordingPool(row=None), before) is None


@pytest.mark.asyncio
async def test_health_history_query():
    since = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)
    rows = [
        {"checked_at": PREV, "score": 64, "regime": "CAUTIOUS", "trend": "stable", "kind": "market", "stale": "false"},
        {"checked_at": AT, "score": None, "regime": None, "trend": None, "kind": "settle", "stale": "true"},
        {"checked_at": AT, "score": 70, "regime": "HEALTHY", "trend": None, "kind": None, "stale": "garbage"},
    ]
    pool = _RecordingPool(rows=rows)
    result = await db.health_history(pool, since)
    [(op, sql, args)] = pool.calls
    assert op == "fetch" and args == (since,)
    assert _flat(sql) == ("SELECT checked_at, score, regime, trend, indicators->>'kind' AS kind, "
                          "indicators->>'stale' AS stale FROM risk.health_checks "
                          "WHERE checked_at >= $1 ORDER BY checked_at ASC")
    assert [r["stale"] for r in result] == [False, True, None]
    assert result[1]["score"] is None
    assert set(result[0]) == {"checked_at", "score", "regime", "trend", "kind", "stale"}
    assert await db.health_history(_RecordingPool(rows=[]), since) == []
