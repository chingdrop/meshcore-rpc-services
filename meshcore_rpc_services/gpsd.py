"""Lightweight async GPSD client.

GPSD speaks a line-delimited JSON protocol over TCP (default port 2947).
We need only a tiny subset:

    {"class":"VERSION", ...}        # banner on connect
    > ?WATCH={"enable":true,"json":true}
    {"class":"DEVICES", ...}
    {"class":"TPV", "lat":..., "lon":..., "alt":..., "epx":..., "epy":...,
                    "speed":..., "track":..., "mode":2|3, "time":"..."}
    {"class":"SKY", ...}            # ignored

We pull TPV messages, hand each fix to a callback, and reconnect on errors.

This module has zero third-party deps — gpsd-py3 and similar are needlessly
heavy and add a bring-your-own-protobuf nightmare. The line-protocol is
trivial and stable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

# Reconnect delays grow on repeated failure, capped.
_RECONNECT_DELAYS = [1.0, 2.0, 5.0, 10.0, 30.0]


@dataclass(frozen=True)
class GpsdFix:
    lat: float
    lon: float
    ts: float
    alt: Optional[float] = None
    acc: Optional[float] = None  # horizontal accuracy in meters (max of epx/epy)
    spd: Optional[float] = None  # m/s
    hdg: Optional[float] = None  # degrees
    fix: Optional[int] = None    # 2 or 3 for usable fixes


FixCallback = Callable[[GpsdFix], Awaitable[None]]


class GpsdClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 2947,
        on_fix: Optional[FixCallback] = None,
        max_acc_m: Optional[float] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_fix = on_fix
        self._max_acc_m = max_acc_m
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="gpsd-client")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._session()
                attempt = 0  # reset backoff on clean disconnect
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("GPSD session ended: %s", e)

            if self._stop.is_set():
                return
            delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                return  # stop was set during the wait
            except asyncio.TimeoutError:
                pass

    async def _session(self) -> None:
        log.info("GPSD connecting to %s:%d", self._host, self._port)
        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            # Skip the VERSION banner if it arrives.
            try:
                await asyncio.wait_for(reader.readline(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

            writer.write(b'?WATCH={"enable":true,"json":true}\n')
            await writer.drain()

            while not self._stop.is_set():
                line = await reader.readline()
                if not line:
                    raise ConnectionError("GPSD closed the connection")

                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict) or msg.get("class") != "TPV":
                    continue

                fix = _parse_tpv(msg)
                if fix is None:
                    continue
                if self._max_acc_m is not None and fix.acc is not None:
                    if fix.acc > self._max_acc_m:
                        log.debug(
                            "GPSD fix dropped: acc=%.1fm > max=%.1fm",
                            fix.acc, self._max_acc_m,
                        )
                        continue

                if self._on_fix is not None:
                    try:
                        await self._on_fix(fix)
                    except Exception:
                        log.exception("on_fix callback raised")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


def _parse_tpv(msg: dict) -> Optional[GpsdFix]:
    """Turn a GPSD TPV record into a GpsdFix, or None if unusable."""
    mode = msg.get("mode")
    if mode not in (2, 3):
        return None  # NO_FIX or signal-only — skip
    lat = msg.get("lat")
    lon = msg.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None

    # Accuracy: GPSD reports epx/epy (meters, 95% conf). Take the worse of
    # the two as a single number; consumers that want lat/lon error bars
    # separately can subscribe to GPSD directly.
    epx = msg.get("epx") if isinstance(msg.get("epx"), (int, float)) else None
    epy = msg.get("epy") if isinstance(msg.get("epy"), (int, float)) else None
    if epx is not None and epy is not None:
        acc = max(epx, epy)
    elif epx is not None:
        acc = epx
    elif epy is not None:
        acc = epy
    else:
        acc = None

    # GPSD's `time` field is ISO-8601; we prefer wall time for simplicity
    # since downstream consumers compare ages against time.time().
    ts = time.time()

    return GpsdFix(
        lat=float(lat),
        lon=float(lon),
        ts=ts,
        alt=float(msg["alt"]) if isinstance(msg.get("alt"), (int, float)) else None,
        acc=float(acc) if acc is not None else None,
        spd=float(msg["speed"]) if isinstance(msg.get("speed"), (int, float)) else None,
        hdg=float(msg["track"]) if isinstance(msg.get("track"), (int, float)) else None,
        fix=int(mode),
    )
