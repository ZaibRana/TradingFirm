"""
TradingFirm — Risk Shield in-process rate limiter (Part 3.2).

The pattern of data-engine's providers/context/ratelimit.py, copied (no
shared package). Sliding window: at most `max_calls` per `window` seconds
and at least `min_gap` seconds between consecutive calls. `clock` and
`sleep` are injectable so tests use a fake clock and never really wait.
"""

import asyncio
import time
from collections import deque
from typing import Awaitable, Callable


class RateLimiter:
    def __init__(
        self,
        max_calls: int = 60,
        window: float = 60.0,
        min_gap: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.max_calls = max_calls
        self.window = window
        self.min_gap = min_gap
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    def _wait_needed(self, now: float) -> float:
        while self._calls and self._calls[0] <= now - self.window:
            self._calls.popleft()
        wait = 0.0
        if self._calls:
            wait = max(wait, self._calls[-1] + self.min_gap - now)
            if len(self._calls) >= self.max_calls:
                wait = max(wait, self._calls[0] + self.window - now)
        return wait

    async def acquire(self) -> float:
        """Block until a call may be sent. Returns the seconds waited."""
        async with self._lock:
            waited = 0.0
            while True:
                now = self._clock()
                wait = self._wait_needed(now)
                if wait <= 0:
                    self._calls.append(now)
                    return waited
                await self._sleep(wait)
                waited += wait


# ── The FRED limiter ─────────────────────────────────────────────────────
# FRED allows 120 requests per minute (docs/errors.html, verified
# 2026-09-10). Half of that, and at least 1 s between calls (G6). Module-
# level so every FredClient in the process shares it; one uvicorn worker is
# assumed, as for data-engine's module-level limiters.
FRED_MAX_CALLS_PER_MINUTE = 60
FRED_MIN_GAP = 1.0
fred_limiter = RateLimiter(
    max_calls=FRED_MAX_CALLS_PER_MINUTE, window=60.0, min_gap=FRED_MIN_GAP
)
