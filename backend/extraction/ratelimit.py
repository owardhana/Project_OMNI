"""A minimal async rate limiter for the extraction LLM calls (Feature 2).

OpenRouter's free tier caps requests **account-wide per minute** (observed limit 16/min
for free models). Bounded concurrency alone can't respect a *per-minute* budget — a burst
of retries blows through it and every call comes back 429. This paces call *starts* to a
fixed minimum interval (``60 / rate_per_min``), across all concurrent verdict tasks, so we
approach the cap without tripping it — turning "rate limited" from a 429 storm into a
predictable slow drip (which is the accepted trade for a $0 always-on backfill).

Reserve-then-sleep: the next slot is claimed under a lock and the wait happens outside it,
so N waiters get N distinct, evenly-spaced slots instead of all waking together.
"""

from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    def __init__(self, per_minute: float):
        self._interval = 60.0 / per_minute if per_minute and per_minute > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0  # monotonic time the next slot may start

    async def acquire(self) -> None:
        if self._interval <= 0:
            return  # unlimited
        async with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        delay = slot - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
