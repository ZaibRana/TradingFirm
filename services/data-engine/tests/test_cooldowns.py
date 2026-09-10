"""
Cooldown helpers (Part 2.4 refactor).

One pair of helpers now serves the per-ticker refresh cooldown (Part 1.2)
and the per-source cooldowns the dossier sets after a refusal. This file
covers the helpers directly; the refresh endpoint's own 429 behaviour is
unchanged and still covered by tests/test_refresh_endpoint.py.

No network, no Redis server: tests/fake_redis.py runs the real cache code.
"""

import pytest

from cache import (
    MemoryCooldowns,
    cooldown_key,
    cooldown_remaining,
    refresh_cooldown_name,
    start_cooldown,
)
from tests.fake_redis import FakeRedis


@pytest.mark.asyncio
async def test_cooldown_helpers_redis_and_memory():
    # ── Redis path: clear → set → reported → key shape unchanged ──
    redis = FakeRedis()
    memory = MemoryCooldowns()
    name = refresh_cooldown_name("AAPL")

    assert await cooldown_remaining(redis, memory, name, 900) is None

    await start_cooldown(redis, memory, name, 900)
    remaining = await cooldown_remaining(redis, memory, name, 900)
    assert remaining is not None and 0 < remaining <= 900

    # Part 1.2's key is preserved byte-for-byte by the move.
    assert cooldown_key(name) == "tf:cache:refresh:AAPL"
    assert redis.keys() == ["tf:cache:refresh:AAPL"]

    # A source cooldown is the same helper with an opaque name.
    assert cooldown_key("edgar") == "tf:cache:edgar"
    await start_cooldown(redis, memory, "edgar", 900)
    assert await cooldown_remaining(redis, memory, "edgar", 900) is not None
    # ...and is per name: one source's cooldown never covers another's.
    assert await cooldown_remaining(redis, memory, "finnhub", 60) is None

    # ── No Redis: the in-memory clock answers ──
    memory_only = MemoryCooldowns()
    assert await cooldown_remaining(None, memory_only, "finnhub", 60) is None
    await start_cooldown(None, memory_only, "finnhub", 60)
    assert await cooldown_remaining(None, memory_only, "finnhub", 60) is not None
    # An elapsed window reads as clear (ttl 0 = nothing left).
    assert await cooldown_remaining(None, memory_only, "finnhub", 0) is None

    # ── Redis raising: fail open to memory, both on read and on write ──
    broken = FakeRedis(fail_on={"ttl", "set"})
    memory_fb = MemoryCooldowns()
    await start_cooldown(broken, memory_fb, "edgar", 900)   # set raises
    assert broken.keys() == []                              # nothing stored
    left = await cooldown_remaining(broken, memory_fb, "edgar", 900)  # ttl raises
    assert left is not None and 0 < left <= 900             # memory answered

    # ── No Redis and no memory: fail open, never raise ──
    assert await cooldown_remaining(None, None, "edgar", 900) is None
    await start_cooldown(None, None, "edgar", 900)


def test_finnhub_cooldown_key_is_pinned_for_risk_shield():
    from cache import SOURCE_FINNHUB
    assert cooldown_key(SOURCE_FINNHUB) == "tf:cache:finnhub", (
        "risk-shield's news poller reads this key before calling Finnhub "
        "(services/risk-shield/cache.py DATA_ENGINE_FINNHUB_COOLDOWN_KEY, spec 3.5 "
        "decision 4). Rename both together."
    )
