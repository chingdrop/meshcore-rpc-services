"""MQTT-backed service orchestrator.

Sole transport-aware module. Its job is:

1. Hold an :class:`MqttBus`.
2. Build the concrete port adapters (response emitter, snapshot provider,
   snapshot sink, node registry) that wrap the bus and the SQLite repo.
3. For each inbound request, run it through :mod:`transport.adapter` to
   produce a clean :class:`Request`, then hand that to
   :func:`core.process_request`.
4. Run the retention sweeper as a sibling background task.
5. Log a structured startup summary.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

from meshcore_rpc_services import core
from meshcore_rpc_services.config import AppConfig
from meshcore_rpc_services.handlers import DEFAULT_HANDLERS
from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.mqtt import topics
from meshcore_rpc_services.persistence import SqliteRequestRepository, SqliteStore
from meshcore_rpc_services.ports import (
    GatewaySnapshotProvider,
    NodeRegistry,
    RequestRepository,
    ResponseEmitter,
)
from meshcore_rpc_services.retention import RetentionSweeper
from meshcore_rpc_services.router import Router
from meshcore_rpc_services.schemas import Response
from meshcore_rpc_services.timeouts import PendingTracker, TimeoutPolicy
from meshcore_rpc_services.transport.adapter import (
    inbound_to_request,
    response_to_outbound,
)
from meshcore_rpc_services.transport.bus import MqttBus

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Port adapters backed by MqttBus
# ---------------------------------------------------------------------------


class _MqttResponseEmitter:
    """Publishes responses on ``meshcore/rpc/response/<node_id>``."""

    def __init__(self, bus: MqttBus) -> None:
        self._bus = bus

    async def emit(self, node_id: str, response: Response) -> None:
        to, payload = response_to_outbound(response)
        # Defensive: emit() takes node_id explicitly; the response's own
        # ``to`` field is the canonical target. Use the latter.
        topic = topics.rpc_response_topic(to or node_id)
        await self._bus.publish(topic, payload)


class _MqttGatewaySnapshotProvider:
    def __init__(self, bus: MqttBus) -> None:
        self._bus = bus

    async def get_snapshot(self) -> Mapping[str, Any]:
        return await self._bus.get_gateway_snapshot()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Service:
    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg

        # Persistence + ports (one SQLite repo implements three of them)
        store = SqliteStore(cfg.service.db_path)
        repo_impl = SqliteRequestRepository(store)
        self._repo: RequestRepository = repo_impl
        self._node_registry: NodeRegistry = repo_impl
        self._store_for_close = store

        # MQTT bus, with the repo acting as the snapshot sink
        self._bus = MqttBus(cfg.mqtt, snapshot_sink=repo_impl)

        self._router = Router(DEFAULT_HANDLERS)
        self._tracker = PendingTracker()
        self._policy = TimeoutPolicy(
            default_s=cfg.service.timeouts.default_s,
            min_s=cfg.service.timeouts.min_s,
            max_s=cfg.service.timeouts.max_s,
            per_type_default_s=dict(cfg.service.timeouts.per_type_default_s),
        )

        self._snapshot_provider: GatewaySnapshotProvider = (
            _MqttGatewaySnapshotProvider(self._bus)
        )
        self._emitter: ResponseEmitter = _MqttResponseEmitter(self._bus)

        self._ctx = HandlerContext(
            snapshot=self._snapshot_provider,
            repo=self._repo,
            nodes=self._node_registry,
        )

        self._sweeper = RetentionSweeper(
            self._repo,
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
                self._sweeper.start()
                try:
                    await self._consume_loop()
                finally:
                    await self._sweeper.stop()
        finally:
            if self._tasks:
                log.info("Waiting on %d in-flight request tasks", len(self._tasks))
                await asyncio.wait(self._tasks, timeout=5.0)
            self._store_for_close.close()

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
        log.info("  subscribe:")
        log.info("     - %s", topics.RPC_REQUEST)
        log.info("     - %s", topics.GATEWAY_STATUS)
        log.info("     - %s", topics.GATEWAY_HEALTH)
        log.info("  publish:")
        log.info("     - %s/<node_id>", topics.RPC_RESPONSE_PREFIX)
        log.info("=" * 60)

    # ------------------------------------------------------------------
    # Consume loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        async for msg in self._bus.messages():
            topic = str(msg.topic)
            if topic != topics.RPC_REQUEST:
                # Gateway status/health are handled by bus-side caching +
                # the snapshot sink. Nothing to do here.
                continue
            task = asyncio.create_task(self._handle_one(topic, msg.payload))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _handle_one(self, topic: str, payload: bytes) -> None:
        env = inbound_to_request(topic=topic, raw_payload=payload)

        # Adapter-level failure: either an addressable bad_request (emit)
        # or an un-addressable drop (silent).
        if env.request is None:
            if env.error_response is not None:
                try:
                    await self._emitter.emit(
                        env.error_response.to, env.error_response
                    )
                except Exception:
                    log.exception("Failed to emit adapter bad_request")
            return

        await core.process_request(
            env.request,
            router=self._router,
            repo=self._repo,
            node_registry=self._node_registry,
            ctx=self._ctx,
            emitter=self._emitter,
            tracker=self._tracker,
            policy=self._policy,
        )
