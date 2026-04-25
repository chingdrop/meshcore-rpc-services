"""Thin async MQTT wrapper around aiomqtt.

Connects, subscribes, publishes, async-iterates messages. Maintains an
in-memory cache of the last retained gateway status + health messages, and
writes each snapshot to the :class:`Store` for history.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import aiomqtt

from meshcore_rpc_services.config import MQTTConfig
from meshcore_rpc_services.mqtt import topics
from meshcore_rpc_services.persistence import Store

log = logging.getLogger(__name__)


class MqttBus:
    """Wraps aiomqtt.Client and exposes a simple publish/subscribe surface."""

    def __init__(self, cfg: MQTTConfig, *, store: Optional[Store] = None) -> None:
        self._cfg = cfg
        self._client: Optional[aiomqtt.Client] = None
        self._store = store  # None in tests that don't care about history

        self._gateway_status: Optional[str] = None
        self._gateway_health: Optional[str] = None
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def connect(self) -> AsyncIterator["MqttBus"]:
        client = aiomqtt.Client(
            hostname=self._cfg.host,
            port=self._cfg.port,
            username=self._cfg.username,
            password=self._cfg.password,
            identifier=self._cfg.client_id,
        )
        async with client:
            self._client = client
            await client.subscribe(topics.GATEWAY_STATUS, qos=self._cfg.qos)
            await client.subscribe(topics.GATEWAY_HEALTH, qos=self._cfg.qos)
            await client.subscribe(topics.RPC_REQUEST, qos=self._cfg.qos)
            log.info(
                "MQTT connected: %s:%s (client_id=%s, qos=%s)",
                self._cfg.host, self._cfg.port,
                self._cfg.client_id, self._cfg.qos,
            )
            try:
                yield self
            finally:
                self._client = None

    async def publish(
        self, topic: str, payload: str | bytes, retain: bool = False
    ) -> None:
        if self._client is None:
            raise RuntimeError("MqttBus not connected")
        data = payload if isinstance(payload, (bytes, bytearray)) else payload.encode("utf-8")
        await self._client.publish(
            topic, payload=data, qos=self._cfg.qos, retain=retain
        )

    async def messages(self) -> AsyncIterator[aiomqtt.Message]:
        if self._client is None:
            raise RuntimeError("MqttBus not connected")
        async for msg in self._client.messages:
            await self._maybe_cache(msg)
            yield msg

    async def _maybe_cache(self, msg: aiomqtt.Message) -> None:
        topic = str(msg.topic)
        if topic not in (topics.GATEWAY_STATUS, topics.GATEWAY_HEALTH):
            return
        payload = (
            msg.payload.decode("utf-8", errors="replace") if msg.payload else ""
        )
        async with self._lock:
            if topic == topics.GATEWAY_STATUS:
                self._gateway_status = payload
            else:
                self._gateway_health = payload
            status_now, health_now = self._gateway_status, self._gateway_health

        if self._store is not None:
            try:
                await self._store.record_gateway_snapshot(
                    status=status_now, health=health_now
                )
            except Exception:
                log.exception("Failed to persist gateway snapshot")

    async def get_gateway_snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "status": self._gateway_status,
                "health": self._gateway_health,
            }
