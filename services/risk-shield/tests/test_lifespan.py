"""Part 3.1 — the real lifespan runs here (`with TestClient(app)`), with
the two factories patched at their module attribute. These are the
startup-branch tests; test_health.py covers response shape with stubbed
state instead.

config.STARTUP_TIMEOUT is monkeypatched down for the slow rows, which is
why main.py reads it as config.STARTUP_TIMEOUT at call time (decision 6).
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import cache
import db
import main


class _FakePool:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _FakeRedisClient:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.fixture
def fast_timeout(monkeypatch):
    """Keep the slow paths honest but quick."""
    monkeypatch.setattr("config.STARTUP_TIMEOUT", 0.05)


def _patch(monkeypatch, *, redis_factory=None, db_factory=None):
    async def ok_redis(*a, **k):
        return _FakeRedisClient()

    async def ok_pool(*a, **k):
        return _FakePool()

    monkeypatch.setattr(cache, "create_redis", redis_factory or ok_redis)
    monkeypatch.setattr(db, "create_db_pool", db_factory or ok_pool)


async def _raises(*a, **k):
    raise ConnectionRefusedError("connection refused")


async def _hangs(*a, **k):
    await asyncio.sleep(30)
    raise AssertionError("should have been cancelled by the startup bound")


def test_lifespan_both_up_sets_state(monkeypatch):
    _patch(monkeypatch)
    with TestClient(main.app) as client:
        assert isinstance(main.app.state.db_pool, _FakePool)
        assert isinstance(main.app.state.redis, _FakeRedisClient)
        body = client.get("/health").json()
        assert body["db_connected"] is True
        assert body["redis_connected"] is True


def test_lifespan_db_refused_boots_degraded(monkeypatch, caplog):
    _patch(monkeypatch, db_factory=_raises)
    with caplog.at_level("WARNING"):
        with TestClient(main.app) as client:
            assert main.app.state.db_pool is None
            assert main.app.state.redis is not None
            resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["db_connected"] is False
    assert resp.json()["redis_connected"] is True
    assert any("Database unavailable" in r.getMessage() for r in caplog.records)


def test_lifespan_redis_refused_boots_degraded(monkeypatch, caplog):
    _patch(monkeypatch, redis_factory=_raises)
    with caplog.at_level("WARNING"):
        with TestClient(main.app) as client:
            assert main.app.state.redis is None
            assert main.app.state.db_pool is not None
            resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["redis_connected"] is False
    assert resp.json()["db_connected"] is True
    assert any("Redis unavailable" in r.getMessage() for r in caplog.records)


def test_lifespan_both_down_still_200(monkeypatch, caplog):
    _patch(monkeypatch, redis_factory=_raises, db_factory=_raises)
    with caplog.at_level("WARNING"):
        with TestClient(main.app) as client:
            resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db_connected"] is False
    assert body["redis_connected"] is False
    assert body["status"] == "ok"


def test_lifespan_db_slow_times_out_and_boots(monkeypatch, caplog, fast_timeout):
    """A Postgres that is restarting rather than refusing must not stall
    the boot — the whole point of decision 6."""
    _patch(monkeypatch, db_factory=_hangs)
    with caplog.at_level("WARNING"):
        with TestClient(main.app) as client:
            assert main.app.state.db_pool is None
            resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["db_connected"] is False
    assert any("Database unavailable" in r.getMessage() for r in caplog.records)


def test_lifespan_redis_slow_times_out_and_boots(monkeypatch, caplog, fast_timeout):
    """The bound covers the factory itself, not only the PING."""
    _patch(monkeypatch, redis_factory=_hangs)
    with caplog.at_level("WARNING"):
        with TestClient(main.app) as client:
            assert main.app.state.redis is None
            resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["redis_connected"] is False
    assert any("Redis unavailable" in r.getMessage() for r in caplog.records)


def test_lifespan_shutdown_tolerates_none(monkeypatch):
    """Shutdown with a dependency that never came up must not raise."""
    _patch(monkeypatch, redis_factory=_raises, db_factory=_raises)
    with TestClient(main.app):
        pass
    assert main.app.state.redis is None
    assert main.app.state.db_pool is None


def test_lifespan_shutdown_closes_both(monkeypatch):
    _patch(monkeypatch)
    with TestClient(main.app):
        pool = main.app.state.db_pool
        client = main.app.state.redis
    assert pool.closed is True
    assert client.closed is True
