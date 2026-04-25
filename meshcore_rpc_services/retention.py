"""Periodic retention sweeper.

Runs as a background asyncio task alongside the consume loop. Each tick it
asks the store to purge request rows (and their events, and stale gateway
snapshots) older than the configured retention window.

If Celery is introduced later, the sweep becomes a Celery beat task that
calls :meth:`Store.purge_before` directly. The interface doesn't change.
"""

from __future__ import annotations

import asyncio
import logging
import time

from meshcore_rpc_services.persistence import Store

log = logging.getLogger(__name__)

_SECONDS_PER_DAY = 86_400.0


class RetentionSweeper:
    def __init__(
        self,
        store: Store,
        *,
        days: int,
        interval_s: float,
    ) -> None:
        if days < 1:
            raise ValueError("retention days must be >= 1")
        if interval_s < 1.0:
            raise ValueError("retention interval must be >= 1 second")
        self._store = store
        self._days = days
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="retention-sweeper")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_once(self) -> int:
        cutoff = time.time() - (self._days * _SECONDS_PER_DAY)
        try:
            deleted = await self._store.purge_before(cutoff)
        except Exception:
            log.exception("retention sweep failed")
            return 0
        if deleted:
            log.info(
                "retention: purged %d requests older than %dd",
                deleted, self._days,
            )
        else:
            log.debug("retention: no rows older than %dd", self._days)
        return deleted

    async def _run(self) -> None:
        await self.run_once()
        while True:
            try:
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                raise
            await self.run_once()
