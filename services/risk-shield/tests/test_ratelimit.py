"""Part 3.2 — ratelimit.py with a fake clock: nothing really waits."""

import pytest

import ratelimit


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _limiter(clock, **kw):
    return ratelimit.RateLimiter(clock=clock, sleep=clock.sleep, **kw)


@pytest.mark.asyncio
async def test_ratelimit_first_call_no_wait():
    clock = FakeClock()
    lim = _limiter(clock, max_calls=5, window=60.0, min_gap=1.0)
    assert await lim.acquire() == 0.0
    assert clock.slept == []


@pytest.mark.asyncio
async def test_ratelimit_enforces_min_gap():
    clock = FakeClock()
    lim = _limiter(clock, max_calls=100, window=60.0, min_gap=1.0)
    await lim.acquire()
    waited = await lim.acquire()
    assert waited == pytest.approx(1.0)
    clock.now += 5.0
    assert await lim.acquire() == 0.0


@pytest.mark.asyncio
async def test_ratelimit_caps_window():
    clock = FakeClock()
    lim = _limiter(clock, max_calls=3, window=10.0, min_gap=0.0)
    for _ in range(3):
        assert await lim.acquire() == 0.0
    waited = await lim.acquire()   # 4th inside the window waits for the 1st to age out
    assert waited == pytest.approx(10.0)


def test_fred_limiter_is_module_level_60_per_minute():
    lim = ratelimit.fred_limiter
    assert isinstance(lim, ratelimit.RateLimiter)
    assert lim.max_calls == 60
    assert lim.window == 60.0
    assert lim.min_gap == 1.0
