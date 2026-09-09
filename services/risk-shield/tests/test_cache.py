"""Part 3.1 — cache.py: the key namespace, the normalizer, and every
cached_json branch. All against an in-process fake Redis; no socket."""

import json

import pytest

import cache
from tests.fake_redis import FakeRedis


# ── Keys ─────────────────────────────────────────────────────────

def test_risk_key_shape():
    assert cache.risk_key(cache.KIND_QUOTES) == "tf:risk:cache:quotes"
    assert cache.risk_key(cache.KIND_FRED, "DGS10") == "tf:risk:cache:fred:DGS10"


def test_risk_key_namespace_is_tf_risk():
    """data-engine owns tf:cache:* and both services share Redis DB 0."""
    for key in (
        cache.risk_key(cache.KIND_QUOTES),
        cache.risk_key(cache.KIND_FRED, "vixcls"),
        cache.risk_key(cache.KIND_HEALTH),
    ):
        assert key.startswith("tf:risk:")
        assert not key.startswith("tf:cache:")


def test_risk_key_normalizes_name():
    assert cache.risk_key("fred", " dgs10 ") == cache.risk_key("fred", "DGS10")
    assert cache.risk_key("quotes", "spy") == cache.risk_key("quotes", "SPY")


def test_risk_key_rejects_empty_kind():
    for bad in ("", "   ", None, 3):
        with pytest.raises(ValueError):
            cache.risk_key(bad, "SPY")


def test_canonical_rejects_non_string():
    for bad in (None, 3, ["SPY"]):
        with pytest.raises(ValueError):
            cache.canonical(bad)


def test_health_channel_constant():
    assert cache.CHANNEL_HEALTH == "tf:risk:health"


def test_ttl_constants():
    assert cache.TTL_QUOTES == 300        # plan 3.2: 5 min
    assert cache.TTL_FRED == 21600        # plan 3.2: 6 h
    assert cache.TTL_HEALTH == 300        # 3.4 scheduler cadence


# ── cached_json ──────────────────────────────────────────────────

def _counter(body):
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return body

    return fetch, calls


@pytest.mark.asyncio
async def test_cached_json_first_call_fetches_and_caches():
    r = FakeRedis()
    fetch, calls = _counter({"vix": 17.2})
    body, from_cache = await cache.cached_json(r, "tf:risk:cache:x", 300, fetch)
    assert body == {"vix": 17.2}
    assert from_cache is False
    assert calls["n"] == 1
    assert json.loads(r.store["tf:risk:cache:x"]) == {"vix": 17.2}
    assert r.ttls["tf:risk:cache:x"] == 300


@pytest.mark.asyncio
async def test_cached_json_second_call_is_a_hit():
    r = FakeRedis()
    fetch, calls = _counter({"vix": 17.2})
    await cache.cached_json(r, "k", 300, fetch)
    body, from_cache = await cache.cached_json(r, "k", 300, fetch)
    assert body == {"vix": 17.2}
    assert from_cache is True
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cached_json_caches_empty_body():
    """Empty is an answer, not a miss."""
    for empty in ([], {}):
        r = FakeRedis()
        fetch, calls = _counter(empty)
        body, from_cache = await cache.cached_json(r, "k", 300, fetch)
        assert body == empty and from_cache is False
        body, from_cache = await cache.cached_json(r, "k", 300, fetch)
        assert body == empty and from_cache is True
        assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cached_json_without_redis_fetches():
    fetch, calls = _counter([1, 2])
    body, from_cache = await cache.cached_json(None, "k", 300, fetch)
    assert body == [1, 2]
    assert from_cache is False
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cached_json_get_raise_is_miss(caplog):
    r = FakeRedis(fail_get=True)
    fetch, calls = _counter({"ok": True})
    with caplog.at_level("WARNING"):
        body, from_cache = await cache.cached_json(r, "k", 300, fetch)
    assert body == {"ok": True}
    assert from_cache is False
    assert calls["n"] == 1
    assert any("Cache read failed" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_cached_json_set_raise_still_returns(caplog):
    r = FakeRedis(fail_set=True)
    fetch, calls = _counter({"ok": True})
    with caplog.at_level("WARNING"):
        body, from_cache = await cache.cached_json(r, "k", 300, fetch)
    assert body == {"ok": True}
    assert from_cache is False
    assert any("Cache write failed" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_cached_json_bad_json_is_miss(caplog):
    r = FakeRedis()
    r.store["k"] = "{not json"
    fetch, calls = _counter({"fresh": 1})
    with caplog.at_level("WARNING"):
        body, from_cache = await cache.cached_json(r, "k", 300, fetch)
    assert body == {"fresh": 1}
    assert from_cache is False
    assert json.loads(r.store["k"]) == {"fresh": 1}   # overwritten
    assert any("not valid JSON" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_cached_json_wrong_shape_is_miss(caplog):
    r = FakeRedis()
    r.store["k"] = json.dumps([1, 2, 3])
    fetch, calls = _counter({"fresh": 1})
    with caplog.at_level("WARNING"):
        body, from_cache = await cache.cached_json(
            r, "k", 300, fetch, valid=lambda b: isinstance(b, dict)
        )
    assert body == {"fresh": 1}
    assert from_cache is False
    assert calls["n"] == 1
    assert any("wrong shape" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_cached_json_fetch_raise_propagates_and_caches_nothing():
    r = FakeRedis()

    async def fetch():
        raise RuntimeError("upstream down")

    with pytest.raises(RuntimeError, match="upstream down"):
        await cache.cached_json(r, "k", 300, fetch)
    assert r.store == {}
    assert r.set_calls == []


@pytest.mark.asyncio
async def test_create_redis_passes_socket_timeouts(monkeypatch):
    """Both socket bounds are set; redis-py defaults them to None."""
    seen = {}

    class _Client:
        async def ping(self):
            return True

    def fake_from_url(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return _Client()

    monkeypatch.setattr(cache.aioredis, "from_url", fake_from_url)
    await cache.create_redis("redis://redis:6379/1", timeout=5.0)
    assert seen["socket_connect_timeout"] == 5.0
    assert seen["socket_timeout"] == 5.0
    assert seen["decode_responses"] is True
    assert seen["url"] == "redis://redis:6379/1"
