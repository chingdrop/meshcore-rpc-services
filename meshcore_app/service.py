"""The orchestrator.

This is the only module that knows about MQTT, persistence, the router, and
the timeout tracker simultaneously. Everything else stays in its lane.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from pydantic import ValidationError

from meshcore_app import errors, persistence
from meshcore_app.config import AppConfig
from meshcore_app.errors import RpcError
from meshcore_app.handlers import DEFAULT_HANDLERS
from meshcore_app.handlers.base import HandlerContext
from meshcore_app.mqtt_bus import MqttBus
from meshcore_app.persistence import AsyncStore, Store
from meshcore_app.router import Router
from meshcore_app.schemas import Request, Response
from meshcore_app.timeouts import PendingTracker, clamp_ttl

log = logging.getLogger(__name__)


class Service:
    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._bus = MqttBus(cfg.mqtt)
        self._store = AsyncStore(Store(cfg.service.db_path))
        self._router = Router(DEFAULT_HANDLERS)
        self._tracker = PendingTracker()
        self._ctx = HandlerContext(bus=self._bus, store=self._store)
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        log.info(
            "Starting meshcore-app (handlers=%s, default_ttl=%ss, max_ttl=%ss)",
            self._router.types(),
            self._cfg.service.default_ttl_s,
            self._cfg.service.max_ttl_s,
        )
        try:
            async with self._bus.connect():
                await self._consume_loop()
        finally:
            # Let in-flight request tasks finish up to a short grace period.
            if self._tasks:
                log.info("Waiting on %d in-flight tasks", len(self._tasks))
                await asyncio.wait(self._tasks, timeout=5.0)
            self._store.close()

    async def _consume_loop(self) -> None:
        async for msg in self._bus.messages():
            if str(msg.topic) != self._cfg.mqtt.request_topic:
                # Non-request topics (gateway status/health) are cached in the bus.
                continue
            # Process each request in its own task so slow handlers don't stall others.
            task = asyncio.create_task(self._process_raw(msg.payload))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    async def _process_raw(self, payload: bytes | bytearray | memoryview | str) -> None:
        raw = (
            payload.decode("utf-8", errors="replace")
            if isinstance(payload, (bytes, bytearray, memoryview))
            else str(payload)
        )

        # 1. Parse JSON
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("Dropping request: invalid JSON (%s)", e)
            # No id/from → nothing actionable to publish back. Log only.
            return

        # 2. Validate schema
        try:
            request = Request.model_validate(data)
        except ValidationError as e:
            await self._emit_bad_request(data, e)
            return

        ttl = clamp_ttl(
            request.ttl, self._cfg.service.default_ttl_s, self._cfg.service.max_ttl_s
        )
        await self._store.record_received(request, ttl)
        await self._store.record_event(request.id, persistence.VALIDATED)

        # 3. Route
        handler = self._router.resolve(request.type)
        if handler is None:
            resp = Response.error(
                request_id=request.id,
                request_type=request.type,
                to=request.from_,
                code=errors.UNKNOWN_TYPE,
                message=f"Unknown request type: {request.type}",
            )
            await self._publish_and_finalize(
                request, resp, final_state="error", error_code=errors.UNKNOWN_TYPE
            )
            return

        # 4. Execute with timeout
        await self._store.record_event(request.id, persistence.HANDLER_STARTED)
        try:
            resp = await self._tracker.run_with_timeout(
                handler.handle(request, self._ctx), ttl_s=ttl
            )
        except asyncio.TimeoutError:
            resp = Response.error(
                request_id=request.id,
                request_type=request.type,
                to=request.from_,
                code=errors.TIMEOUT,
                message=f"No response before timeout ({ttl}s)",
            )
            await self._store.record_event(request.id, persistence.TIMEOUT)
            await self._publish_and_finalize(
                request, resp, final_state="timeout", error_code=errors.TIMEOUT
            )
            return
        except RpcError as e:
            resp = Response.error(
                request_id=request.id,
                request_type=request.type,
                to=request.from_,
                code=e.code,
                message=e.message,
            )
            await self._publish_and_finalize(
                request, resp, final_state="error", error_code=e.code
            )
            return
        except Exception as e:  # noqa: BLE001 — we must catch to emit an error response
            log.exception("Handler crashed for request id=%s type=%s", request.id, request.type)
            resp = Response.error(
                request_id=request.id,
                request_type=request.type,
                to=request.from_,
                code=errors.INTERNAL,
                message=f"{type(e).__name__}",
            )
            await self._publish_and_finalize(
                request, resp, final_state="error", error_code=errors.INTERNAL
            )
            return

        # 5. Publish success
        await self._publish_and_finalize(request, resp, final_state="ok")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _publish_and_finalize(
        self,
        request: Request,
        response: Response,
        *,
        final_state: str,
        error_code: Optional[str] = None,
    ) -> None:
        topic = self._cfg.mqtt.response_topic(request.from_)
        try:
            await self._bus.publish(topic, response.to_json())
            await self._store.record_event(request.id, persistence.RESPONSE_PUBLISHED)
        except Exception:
            log.exception("Failed to publish response id=%s", request.id)
            # We still record completion so state isn't lost.
        await self._store.record_completion(
            request.id, final_state=final_state, response=response, error_code=error_code
        )

    async def _emit_bad_request(self, data: dict, err: ValidationError) -> None:
        """Best-effort error response when the payload didn't validate.

        If we can't recover id/from, we log and drop — there's nowhere to send.
        """
        req_id = data.get("id") if isinstance(data, dict) else None
        req_type = data.get("type") if isinstance(data, dict) else None
        to = data.get("from") if isinstance(data, dict) else None

        if not req_id or not to:
            log.warning("Dropping malformed request (no id/from): %s", err.errors()[:3])
            return

        resp = Response.error(
            request_id=str(req_id),
            request_type=str(req_type or "unknown"),
            to=str(to),
            code=errors.BAD_REQUEST,
            message="Request payload failed validation",
        )
        topic = self._cfg.mqtt.response_topic(str(to))
        try:
            await self._bus.publish(topic, resp.to_json())
        except Exception:
            log.exception("Failed to publish bad_request response")
