"""Pending-request tracker. The service uses this to enforce TTL on handlers.

The contract:
    `run_with_timeout(coro, ttl_s)` awaits coro OR fires a TimeoutError after
    ttl_s. The service wraps it and emits a structured timeout response.

We also keep a live count of in-flight requests for diagnostics (used by the
gateway.status handler indirectly via persistence counts, but we keep an
in-memory gauge too for the currently-running view).
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
        """Run coro with a hard timeout. Propagates asyncio.TimeoutError on expiry."""
        await self._inc()
        try:
            return await asyncio.wait_for(coro, timeout=ttl_s)
        finally:
            await self._dec()


def clamp_ttl(requested: int | None, default_s: int, max_s: int) -> int:
    """Resolve the effective TTL. Never returns <1 or >max_s."""
    if requested is None:
        return default_s
    return max(1, min(int(requested), max_s))
