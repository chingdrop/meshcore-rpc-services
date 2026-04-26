"""MQTT-backed service orchestrator.

Sole transport-aware module. Its job:

1. Hold an :class:`MqttBus`.
2. For each inbound request, run it through :mod:`transport.adapter`
   and hand the clean :class:`Request` to :func:`core.process_request`.
3. Run the retention sweeper as a sibling background task.
4. Log a structured startup summary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from meshcore_rpc_services import core
from meshcore_rpc_services.config import AppConfig
from meshcore_rpc_services.handlers import DEFAULT_HANDLERS
from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.mqtt import topics
from meshcore_rpc_services.persistence import Store
from meshcore_rpc_services.retention import RetentionSweeper
from meshcore_rpc_services.router import Router
from meshcore_rpc_services.schemas import Response
from meshcore_rpc_services.state import StateAggregator
from meshcore_rpc_services.timeouts import PendingTracker, TimeoutPolicy
from meshcore_rpc_services.transport.adapter import (
    inbound_to_request,
    response_to_outbound,
)
from meshcore_rpc_services.transport.bus import MqttBus

log = logging.getLogger(__name__)


def _safe_json(payload: bytes) -> Optional[dict]:
    try:
        return json.loads(payload.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        return None


class Service:
    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg

        self._store = Store(cfg.service.db_path)
        self._bus = MqttBus(cfg.mqtt, store=self._store)

        self._router = Router(DEFAULT_HANDLERS)
        self._tracker = PendingTracker()
        self._policy = TimeoutPolicy(
            default_s=cfg.service.timeouts.default_s,
            min_s=cfg.service.timeouts.min_s,
            max_s=cfg.service.timeouts.max_s,
            per_type_default_s=dict(cfg.service.timeouts.per_type_default_s),
        )

        self._state = StateAggregator(
            store=self._store,
            publish=self._bus.publish,
        )

        self._ctx = HandlerContext(
            store=self._store,
            gateway_snapshot=self._bus.get_gateway_snapshot,
            state=self._state,
        )

        self._sweeper = RetentionSweeper(
            self._store,
            days=cfg.service.retention.days,
            interval_s=cfg.service.retention.interval_s,
        )

        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._log_startup_summary()
        try:
            async with self._bus.connect():
                await self._push_static_base_location()
                self._sweeper.start()
                try:
                    await self._consume_loop()
                finally:
                    await self._sweeper.stop()
        finally:
            if self._tasks:
                log.info("Waiting on %d in-flight request tasks", len(self._tasks))
                await asyncio.wait(self._tasks, timeout=5.0)
            self._store.close()

    def _log_startup_summary(self) -> None:
        tmo = self._cfg.service.timeouts
        ret = self._cfg.service.retention
        log.info("=" * 60)
        log.info("meshcore-rpc-services starting")
        log.info("  broker         : %s:%s", self._cfg.mqtt.host, self._cfg.mqtt.port)
        log.info("  client_id      : %s", self._cfg.mqtt.client_id)
        log.info("  qos            : %s", self._cfg.mqtt.qos)
        log.info("  db             : %s", self._cfg.service.db_path)
        log.info("  log_level      : %s", self._cfg.service.log_level)
        log.info(
            "  ttl policy     : default=%ss min=%ss max=%ss",
            tmo.default_s, tmo.min_s, tmo.max_s,
        )
        if tmo.per_type_default_s:
            log.info("  per-type TTL   :")
            for t, v in sorted(tmo.per_type_default_s.items()):
                log.info("     - %s = %ss", t, v)
        log.info(
            "  retention      : %dd (sweep every %.0fs)",
            ret.days, ret.interval_s,
        )
        log.info("  handlers (%d)   :", len(self._router.types()))
        for t in self._router.types():
            log.info("     - %s", t)
        _gw = topics.GATEWAY_NATIVE_PREFIX
        log.info("  subscribe:")
        log.info("     - %s", topics.RPC_REQUEST)
        log.info("     - %s", topics.GATEWAY_STATUS)
        log.info("     - %s", topics.gateway_native_direct_msg_filter(_gw))
        log.info("     - %s", topics.gateway_native_battery(_gw))
        log.info("     - %s", topics.gateway_native_telemetry(_gw))
        log.info("     - %s", topics.gateway_native_advertisement(_gw))
        log.info("  publish:")
        log.info("     - %s/<node_id>", topics.RPC_RESPONSE_PREFIX)
        log.info("     - %s/<node_id>/{location,battery,state}", topics.NODE_PREFIX)
        log.info("     - %s", topics.BASE_LOCATION)
        log.info("=" * 60)

    # ------------------------------------------------------------------
    # Consume loop
    # ------------------------------------------------------------------

    async def _push_static_base_location(self) -> None:
        cfg = self._cfg.service.base
        if cfg.source != "static" or cfg.static_lat is None or cfg.static_lon is None:
            return
        from meshcore_rpc_services.state import LocationFix
        fix = LocationFix(
            lat=cfg.static_lat,
            lon=cfg.static_lon,
            ts=time.time(),
            fix=3,
        )
        await self._state.apply_base_location(fix, source="static")
        log.info("Base location set from config: lat=%.6f lon=%.6f", fix.lat, fix.lon)

    async def _consume_loop(self) -> None:
        _gw = topics.GATEWAY_NATIVE_PREFIX
        _direct_prefix = f"{_gw}/message/direct/"
        _battery_topic = topics.gateway_native_battery(_gw)
        _telemetry_topic = topics.gateway_native_telemetry(_gw)

        async for msg in self._bus.messages():
            topic = str(msg.topic)
            if topic == topics.RPC_REQUEST:
                task = asyncio.create_task(self._handle_one(topic, msg.payload))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            elif topic.startswith(_direct_prefix) or topic in (_battery_topic, _telemetry_topic):
                try:
                    await self._route_event(topic, msg.payload,
                                            _direct_prefix, _battery_topic, _telemetry_topic)
                except Exception:
                    log.exception("Failed to route event from %s", topic)
            # GATEWAY_STATUS is cached by the bus; everything else is silently ignored.

    async def _route_event(
        self, topic: str, payload: bytes,
        direct_prefix: str, battery_topic: str, telemetry_topic: str,
    ) -> None:
        if topic.startswith(direct_prefix):
            pubkey = topic[len(direct_prefix):]
            await self._state.apply_seen(pubkey, time.time())
        elif topic == battery_topic:
            data = _safe_json(payload)
            if data:
                await self._handle_battery_event(data)
        elif topic == telemetry_topic:
            data = _safe_json(payload)
            if data:
                await self._handle_telemetry_event(data)

    async def _handle_battery_event(self, data: dict) -> None:
        # Field names depend on the meshcore-mqtt gateway payload shape.
        # Populate once real payloads have been captured.
        node_id: Optional[str] = data.get("pubkey") or data.get("node_id")
        if not node_id:
            log.debug("Battery event missing node identifier; dropping: %s", data)
            return
        await self._state.apply_battery(
            node_id, ts=data.get("ts") or time.time(),
            pct=data.get("pct") or data.get("battery_pct"),
            voltage=data.get("voltage"),
            source="telemetry",
        )

    async def _handle_telemetry_event(self, data: dict) -> None:
        # Mark the node as seen; extend with location/battery once payload
        # shape is confirmed from actual gateway captures.
        node_id: Optional[str] = data.get("pubkey") or data.get("node_id")
        if not node_id:
            log.debug("Telemetry event missing node identifier; dropping: %s", data)
            return
        await self._state.apply_seen(node_id, ts=data.get("ts") or time.time())

    async def _emit(self, node_id: str, response: Response) -> None:
        to, payload = response_to_outbound(response)
        await self._bus.publish(topics.rpc_response_topic(to or node_id), payload)

    async def _handle_one(self, topic: str, payload: bytes) -> None:
        env = inbound_to_request(topic=topic, raw_payload=payload)

        if env.request is None:
            if env.error_response is not None:
                try:
                    await self._emit(env.error_response.to, env.error_response)
                except Exception:
                    log.exception("Failed to emit adapter bad_request")
            return

        await core.process_request(
            env.request,
            router=self._router,
            store=self._store,
            ctx=self._ctx,
            emit=self._emit,
            tracker=self._tracker,
            policy=self._policy,
        )
