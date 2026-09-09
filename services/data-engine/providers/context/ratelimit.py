"""
TradingFirm — in-process rate limiter shared by the context clients.

Written for Finnhub in Part 2.1 (60/min + 1.2 s gap) and moved here in
Part 2.2 so the EDGAR client can run the same code at 10 req/s. Sliding
window: at most `max_calls` per `window` seconds and at least `min_gap`
seconds between consecutive calls. `clock` and `sleep` are injectable so
tests use a fake clock and never really wait.
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
        min_gap: float = 1.2,
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


# ── Part 2.2: the EDGAR limiter ──────────────────────────────────────────
# One mechanism: a rolling window of 10 per second (the SEC fair-access
# cap), no minimum gap. Module-level so every EdgarClient in the process
# shares it. One uvicorn worker is assumed (the Dockerfile CMD has no
# --workers); more workers would multiply the rate — deferred, see
# docs/progress.md row 2.2. Tests inject their own instance with a fake
# clock (EdgarClient(limiter=...)).
EDGAR_MAX_CALLS_PER_SECOND = 10
edgar_limiter = RateLimiter(max_calls=EDGAR_MAX_CALLS_PER_SECOND, window=1.0, min_gap=0.0)


# ── Part 2.3: the Alpha Vantage limiter ──────────────────────────────────
# Free tier: 5 requests per minute (and 25 per day, which is not a rate
# and is detected by body shape in alphavantage_client, not tracked here).
# No minimum gap: five calls may go out back to back, the sixth waits.
# Module-level so every AlphaVantageClient in the process shares it; same
# one-uvicorn-worker assumption as the EDGAR limiter above.
ALPHAVANTAGE_MAX_CALLS_PER_MINUTE = 5
alphavantage_limiter = RateLimiter(
    max_calls=ALPHAVANTAGE_MAX_CALLS_PER_MINUTE, window=60.0, min_gap=0.0
)
