"""Request-processing pipeline.

Given a validated :class:`Request` plus a :class:`Store`, a router, a
timeout policy, and an emitter callback, run the full lifecycle:

    record_received (dedup) -> route -> execute with timeout -> persist -> emit

Parsing happens upstream in :mod:`transport.adapter` so this function can
be called from any source that hands in a :class:`Request`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from meshcore_rpc_services import errors, lifecycle
from meshcore_rpc_services.errors import RpcError
from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.persistence import Store
from meshcore_rpc_services.router import Router
from meshcore_rpc_services.schemas import Request, Response
from meshcore_rpc_services.timeouts import PendingTracker, TimeoutPolicy

log = logging.getLogger(__name__)

# Callback that publishes a response for a given node. The MQTT service
# passes an aiomqtt-backed function; tests pass a list.append closure.
ResponseEmitter = Callable[[str, Response], Awaitable[None]]


async def process_request(
    request: Request,
    *,
    router: Router,
    store: Store,
    ctx: HandlerContext,
    emit: ResponseEmitter,
    tracker: PendingTracker,
    policy: TimeoutPolicy,
) -> None:
    """Run the full RPC lifecycle for a validated request. Never raises."""
    ttl = policy.resolve(
        request_type=request.type, requested_ttl=request.ttl
    )

    # 1) record_received — decides dedup outcome
    try:
        fresh = await store.record_received(request, ttl)
    except Exception:
        log.exception("record_received failed; emitting internal error")
        await _emit_error_final(
            request, code=errors.INTERNAL,
            message="persistence error", store=store, emit=emit,
        )
        return

    if not fresh:
        await store.record_event(
            request.id, request.from_, lifecycle.REJECTED, "duplicate"
        )
        await _emit_error_final(
            request, code=errors.DUPLICATE,
            message="Duplicate request id for this node",
            store=store, emit=emit,
        )
        return

    # Fresh: mark validated + bump node registry
    await store.record_event(
        request.id, request.from_, lifecycle.VALIDATED
    )
    await store.mark_node_seen(request.from_, time.time())

    # 2) Route
    handler = router.resolve(request.type)
    if handler is None:
        await store.record_event(
            request.id, request.from_, lifecycle.REJECTED, "unknown_type"
        )
        await _emit_error_final(
            request, code=errors.UNKNOWN_TYPE,
            message=f"Unknown request type: {request.type}",
            store=store, emit=emit,
        )
        return

    # 3) Execute with timeout
    await store.record_event(
        request.id, request.from_, lifecycle.HANDLER_STARTED
    )
    try:
        resp = await tracker.run_with_timeout(
            handler.handle(request, ctx), ttl_s=ttl
        )
    except asyncio.TimeoutError:
        await store.record_event(
            request.id, request.from_, lifecycle.TIMEOUT
        )
        await _emit_error_final(
            request, code=errors.TIMEOUT,
            message=f"No response before timeout ({ttl}s)",
            store=store, emit=emit,
        )
        return
    except RpcError as e:
        await _emit_error_final(
            request, code=e.code, message=e.message,
            store=store, emit=emit,
        )
        return
    except Exception as e:  # noqa: BLE001
        log.exception(
            "Handler crashed for request id=%s type=%s",
            request.id, request.type,
        )
        await _emit_error_final(
            request, code=errors.INTERNAL,
            message=f"{type(e).__name__}",
            store=store, emit=emit,
        )
        return

    # 4) Success
    await _publish_and_finalize(
        request, resp, store=store, emit=emit,
        final_state=lifecycle.COMPLETED_OK,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _emit_error_final(
    request: Request,
    *,
    code: str,
    message: str,
    store: Store,
    emit: ResponseEmitter,
) -> None:
    resp = Response.error(
        request_id=request.id,
        request_type=request.type,
        to=request.from_,
        code=code,
        message=message,
    )
    await _publish_and_finalize(
        request, resp, store=store, emit=emit,
        final_state=lifecycle.COMPLETED_ERROR,
        error_code=code,
    )


async def _publish_and_finalize(
    request: Request,
    response: Response,
    *,
    store: Store,
    emit: ResponseEmitter,
    final_state: str,
    error_code: Optional[str] = None,
) -> None:
    try:
        await emit(request.from_, response)
        await store.record_event(
            request.id, request.from_, lifecycle.RESPONSE_PUBLISHED
        )
    except Exception:
        log.exception(
            "Failed to emit response id=%s to=%s", request.id, request.from_
        )
    await store.record_completion(
        request.id,
        request.from_,
        final_state=final_state,
        response=response,
        error_code=error_code,
    )
