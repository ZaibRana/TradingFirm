"""Part 3.1 — db.py: the error tuple and the bounded pool factory.
asyncpg.create_pool is patched; nothing opens a socket."""

import asyncio

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
