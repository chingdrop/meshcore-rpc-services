"""In-flight request tracker.

Owns two things:

* ``run_with_timeout(coro, ttl_s)`` — enforces the handler's TTL budget.
* ``in_flight()`` — the current count of running handler coroutines, for
  diagnostics.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar

T = TypeVar("T")


class PendingTracker:
    def __init__(self) -> None:
        self._in_flight = 0
        self._lock = asyncio.Lock()

    async def _inc(self) -> None:
        async with self._lock:
            self._in_flight += 1

    async def _dec(self) -> None:
        async with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    def in_flight(self) -> int:
        return self._in_flight

    async def run_with_timeout(self, coro: Awaitable[T], ttl_s: int) -> T:
        await self._inc()
        try:
            return await asyncio.wait_for(coro, timeout=ttl_s)
        finally:
            await self._dec()
