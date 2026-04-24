"""Periodic retention sweeper.

Runs as a background asyncio task alongside the consume loop. Each tick it
asks the repo to purge request rows (and their events, and stale gateway
snapshots) older than the configured retention window.

Deliberately tiny. When Celery arrives, this becomes a periodic Celery beat
task that calls the same :meth:`RequestRepository.purge_before` method.
"""

from __future__ import annotations

import asyncio
import logging
import time

from meshcore_rpc_services.ports import RequestRepository

log = logging.getLogger(__name__)

_SECONDS_PER_DAY = 86_400.0


class RetentionSweeper:
    def __init__(
        self,
        repo: RequestRepository,
        *,
        days: int,
        interval_s: float,
    ) -> None:
        if days < 1:
            raise ValueError("retention days must be >= 1")
        if interval_s < 1.0:
            raise ValueError("retention interval must be >= 1 second")
        self._repo = repo
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
        """Perform a single purge pass. Returns number of rows deleted."""
        cutoff = time.time() - (self._days * _SECONDS_PER_DAY)
        try:
            deleted = await self._repo.purge_before(cutoff)
        except Exception:
            log.exception("retention sweep failed")
            return 0
        if deleted:
            log.info(
                "retention: purged %d requests older than %dd",
                deleted, self._days,
            )
        else:
            log.debug(
                "retention: no rows older than %dd", self._days,
            )
        return deleted

    async def _run(self) -> None:
        # Run one sweep right away so a fresh start cleans old data.
        await self.run_once()
        while True:
            try:
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                raise
            await self.run_once()
