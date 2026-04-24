"""Thin async MQTT wrapper around aiomqtt.

Knows nothing about RPC semantics. Just connect / subscribe / publish /
async-iterate messages. Also caches the last retained gateway status + health
messages so handlers can read them without hitting the broker.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import aiomqtt

from meshcore_rpc_services.config import MQTTConfig

log = logging.getLogger(__name__)


class MqttBus:
    """Wraps aiomqtt.Client and exposes a simple publish/subscribe surface."""

    def __init__(self, cfg: MQTTConfig) -> None:
        self._cfg = cfg
        self._client: Optional[aiomqtt.Client] = None

        # Caches for gateway state (populated on retained-msg receipt).
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
            # Subscribe to gateway status/health so we always have a snapshot.
            await client.subscribe(self._cfg.gateway_status_topic, qos=self._cfg.qos)
            await client.subscribe(self._cfg.gateway_health_topic, qos=self._cfg.qos)
            await client.subscribe(self._cfg.request_topic, qos=self._cfg.qos)
            log.info("MQTT connected: %s:%s", self._cfg.host, self._cfg.port)
            try:
                yield self
            finally:
                self._client = None

    async def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        if self._client is None:
            raise RuntimeError("MqttBus not connected")
        await self._client.publish(
            topic, payload=payload.encode("utf-8"), qos=self._cfg.qos, retain=retain
        )

    async def messages(self) -> AsyncIterator[aiomqtt.Message]:
        """Yield every inbound message on subscribed topics.

        The caller is responsible for topic-dispatch; this keeps the bus dumb.
        It also side-effect-caches gateway status/health here so that
        downstream handlers can read them synchronously via the getters.
        """
        if self._client is None:
            raise RuntimeError("MqttBus not connected")
        async for msg in self._client.messages:
            await self._maybe_cache(msg)
            yield msg

    async def _maybe_cache(self, msg: aiomqtt.Message) -> None:
        topic = str(msg.topic)
        payload = msg.payload.decode("utf-8", errors="replace") if msg.payload else ""
        if topic == self._cfg.gateway_status_topic:
            async with self._lock:
                self._gateway_status = payload
        elif topic == self._cfg.gateway_health_topic:
            async with self._lock:
                self._gateway_health = payload

    async def get_gateway_snapshot(self) -> dict:
        async with self._lock:
            return {
                "status": self._gateway_status,
                "health": self._gateway_health,
            }
