"""Exercises core.process_request end-to-end against a real Store."""

from __future__ import annotations

import asyncio

import pytest

from meshcore_rpc_services import core, lifecycle
from meshcore_rpc_services.handlers import DEFAULT_HANDLERS
from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.router import Router
from meshcore_rpc_services.schemas import Request, Response
from meshcore_rpc_services.timeouts import PendingTracker, TimeoutPolicy


def _req(**over):
    base = {"v": 1, "id": "r1", "type": "ping", "from": "n1"}
    if "from_" in over:
        base["from"] = over.pop("from_")
    base.update(over)
    return Request.model_validate(base)


def _harness(router, store, ctx, *, default=30, max_s=300, min_s=1):
    sent: list[tuple[str, Response]] = []

    async def emit(node_id: str, response: Response) -> None:
        sent.append((node_id, response))

    return sent, {
        "router": router,
        "store": store,
        "ctx": ctx,
        "emit": emit,
        "tracker": PendingTracker(),
        "policy": TimeoutPolicy(default_s=default, min_s=min_s, max_s=max_s),
    }


@pytest.mark.asyncio
async def test_ping_happy_path(store, ctx):
    router = Router(DEFAULT_HANDLERS)
    sent, h = _harness(router, store, ctx)

    await core.process_request(_req(), **h)

    assert len(sent) == 1
    _, resp = sent[0]
    assert resp.status == "ok"
    assert resp.body == {"message": "pong"}

    # Final state is completed_ok
    counts = await store.counts()
    assert counts.get(lifecycle.COMPLETED_OK, 0) == 1


@pytest.mark.asyncio
async def test_unknown_type_is_rejected(store, ctx):
    router = Router([])
    sent, h = _harness(router, store, ctx)
    await core.process_request(_req(type="nope"), **h)

    _, resp = sent[0]
    assert resp.status == "error"
    assert resp.error and resp.error.code == "unknown_type"
    counts = await store.counts()
    assert counts.get(lifecycle.COMPLETED_ERROR, 0) == 1


@pytest.mark.asyncio
async def test_timeout_fires_on_slow_handler(store, ctx):
    class _Slow:
        type = "slow"
        async def handle(self, req, ctx):
            await asyncio.sleep(5)
            return Response.ok(req, {})

    router = Router([_Slow()])
    sent, h = _harness(router, store, ctx, default=1, max_s=1)
    await core.process_request(_req(type="slow", ttl=1), **h)

    _, resp = sent[0]
    assert resp.error and resp.error.code == "timeout"


@pytest.mark.asyncio
async def test_handler_exception_becomes_internal_error(store, ctx):
    class _Boom:
        type = "boom"
        async def handle(self, req, ctx):
            raise RuntimeError("kaboom")

    router = Router([_Boom()])
    sent, h = _harness(router, store, ctx)
    await core.process_request(_req(type="boom"), **h)

    _, resp = sent[0]
    assert resp.error and resp.error.code == "internal"


@pytest.mark.asyncio
async def test_duplicate_request_emits_structured_error(store, ctx):
    router = Router(DEFAULT_HANDLERS)
    sent, h = _harness(router, store, ctx)

    await core.process_request(_req(id="d1"), **h)
    await core.process_request(_req(id="d1"), **h)  # same (from, id)

    assert len(sent) == 2
    first, second = sent[0][1], sent[1][1]
    assert first.status == "ok"
    assert second.status == "error"
    assert second.error and second.error.code == "duplicate"


@pytest.mark.asyncio
async def test_node_last_seen_is_marked_on_fresh_request(store, ctx):
    router = Router(DEFAULT_HANDLERS)
    sent, h = _harness(router, store, ctx)
    await core.process_request(_req(from_="n42"), **h)
    assert await store.get_last_seen("n42") is not None
