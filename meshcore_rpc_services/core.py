"""Pure request-processing pipeline.

No transport code, no persistence backend, no scheduler. Given a fully
parsed :class:`Request` and the set of injected ports, performs the full
lifecycle: record_received → route → execute with timeout → persist → emit.

The *parse* step sits outside this module in :mod:`transport.adapter` so
that a future non-MQTT transport can feed ``Request`` instances in without
duplicating JSON parsing.

Today :mod:`transport.service` is the only caller. Tomorrow a Celery task
can call :func:`process_request` directly with a queue-based emitter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from meshcore_rpc_services import errors, lifecycle
from meshcore_rpc_services.errors import RpcError
from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.ports import (
    NodeRegistry,
    RequestRepository,
    ResponseEmitter,
)
from meshcore_rpc_services.router import Router
from meshcore_rpc_services.schemas import Request, Response
from meshcore_rpc_services.timeouts import PendingTracker, TimeoutPolicy

log = logging.getLogger(__name__)


async def process_request(
    request: Request,
    *,
    router: Router,
    repo: RequestRepository,
    node_registry: NodeRegistry,
    ctx: HandlerContext,
    emitter: ResponseEmitter,
    tracker: PendingTracker,
    policy: TimeoutPolicy,
) -> None:
    """Run the full RPC lifecycle for a validated request.

    Never raises — all failure modes are translated into error responses
    or logged and dropped.
    """
    ttl = policy.resolve(
        request_type=request.type, requested_ttl=request.ttl
    )

    # 1) record_received — decides dedup outcome
    try:
        fresh = await repo.record_received(request, ttl)
    except Exception:
        log.exception("record_received failed; emitting internal error")
        await _emit_error_final(
            request, code=errors.INTERNAL,
            message="persistence error", repo=repo, emitter=emitter,
        )
        return

    if not fresh:
        # Duplicate (from, id). Structured error + persisted.
        await repo.record_event(request.id, lifecycle.REJECTED, "duplicate")
        await _emit_error_final(
            request, code=errors.DUPLICATE,
            message="Duplicate request id for this node",
            repo=repo, emitter=emitter,
        )
        return

    # New request. Validated is recorded implicitly — transport.adapter
    # already validated it. Mark as validated for the event log.
    await repo.record_event(request.id, lifecycle.VALIDATED)
    await node_registry.mark_seen(request.from_, time.time())

    # 2) Route
    handler = router.resolve(request.type)
    if handler is None:
        await repo.record_event(
            request.id, lifecycle.REJECTED, "unknown_type"
        )
        await _emit_error_final(
            request, code=errors.UNKNOWN_TYPE,
            message=f"Unknown request type: {request.type}",
            repo=repo, emitter=emitter,
        )
        return

    # 3) Execute with timeout
    await repo.record_event(request.id, lifecycle.HANDLER_STARTED)
    try:
        resp = await tracker.run_with_timeout(
            handler.handle(request, ctx), ttl_s=ttl
        )
    except asyncio.TimeoutError:
        await repo.record_event(request.id, lifecycle.TIMEOUT)
        await _emit_error_final(
            request, code=errors.TIMEOUT,
            message=f"No response before timeout ({ttl}s)",
            repo=repo, emitter=emitter,
        )
        return
    except RpcError as e:
        await _emit_error_final(
            request, code=e.code, message=e.message,
            repo=repo, emitter=emitter,
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
            repo=repo, emitter=emitter,
        )
        return

    # 4) Success
    await _publish_and_finalize(
        request, resp, repo=repo, emitter=emitter,
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
    repo: RequestRepository,
    emitter: ResponseEmitter,
) -> None:
    resp = Response.error(
        request_id=request.id,
        request_type=request.type,
        to=request.from_,
        code=code,
        message=message,
    )
    await _publish_and_finalize(
        request, resp, repo=repo, emitter=emitter,
        final_state=lifecycle.COMPLETED_ERROR,
        error_code=code,
    )


async def _publish_and_finalize(
    request: Request,
    response: Response,
    *,
    repo: RequestRepository,
    emitter: ResponseEmitter,
    final_state: str,
    error_code: Optional[str] = None,
) -> None:
    try:
        await emitter.emit(request.from_, response)
        await repo.record_event(request.id, lifecycle.RESPONSE_PUBLISHED)
    except Exception:
        log.exception(
            "Failed to emit response id=%s to=%s", request.id, request.from_
        )
        # Still record completion.
    await repo.record_completion(
        request.id,
        final_state=final_state,
        response=response,
        error_code=error_code,
    )
