"""TCP transport to a TAK Server.

CoT-over-TCP is the simplest, most widely supported flavor: open a socket
to the TAK Server's CoT port, write newline-delimited XML events, done.
No handshake, no auth (in v1), no protocol negotiation.

Failure handling:

  * If the connection drops, we reconnect with exponential backoff.
  * Writes that fail are logged and dropped — TAK is not a transactional
    store and the bridge will republish on the next interval anyway.
  * We do not buffer beyond a small in-memory queue. A bridge that can't
    talk to TAK for an hour shouldn't fill a disk; the retained MQTT
    topics are the source of truth, not us.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)

_RECONNECT_DELAYS = [1.0, 2.0, 5.0, 10.0, 30.0]
# Outbound queue cap. If TAK is unreachable, we drop oldest events
# rather than block the bridge or grow memory unboundedly.
_QUEUE_CAP = 256


class TakSink:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=_QUEUE_CAP)
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="tak-sink")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

    async def send(self, cot_xml: bytes) -> None:
        """Enqueue a CoT event for transmission. Drops oldest on overflow."""
        try:
            self._queue.put_nowait(cot_xml)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(cot_xml)
            except asyncio.QueueFull:
                log.warning("TAK outbound queue full; dropping CoT event")

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._session()
                attempt = 0
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("TAK connection ended: %s", e)

            if self._stop.is_set():
                return
            delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                pass

    async def _session(self) -> None:
        log.info("TAK connecting to %s:%d", self._host, self._port)
        reader, writer = await asyncio.open_connection(self._host, self._port)
        log.info("TAK connected")
        try:
            while not self._stop.is_set():
                # Wait for an outbound event with a timeout so the loop
                # can periodically check `_stop`.
                try:
                    cot = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                writer.write(cot)
                try:
                    await writer.drain()
                except (ConnectionError, BrokenPipeError) as e:
                    # Re-queue the dropped event at the front so the next
                    # session retries it.
                    try:
                        self._queue.put_nowait(cot)
                    except asyncio.QueueFull:
                        pass
                    raise e
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            log.info("TAK session closed")
