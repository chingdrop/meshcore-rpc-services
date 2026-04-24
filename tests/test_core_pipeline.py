"""Exercises `core.process_request` end-to-end with in-memory port fakes."""

from __future__ import annotations

import asyncio

import pytest

from meshcore_rpc_services import core, lifecycle
from meshcore_rpc_services.handlers import DEFAULT_HANDLERS
from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.router import Router
from meshcore_rpc_services.schemas import Request, Response
from meshcore_rpc_services.timeouts import PendingTracker, TimeoutPolicy

from tests._fakes import FakeEmitter, FakeNodeRegistry, FakeRepo, FakeSnapshot


def _ctx(repo: FakeRepo, nodes: FakeNodeRegistry) -> HandlerContext:
    return HandlerContext(snapshot=FakeSnapshot(), repo=repo, nodes=nodes)


def _harness(
    router: Router,
    repo: FakeRepo,
    emitter: FakeEmitter,
    *,
    default_ttl_s: int = 30,
    max_ttl_s: int = 300,
    min_ttl_s: int = 1,
) -> dict:
    nodes = FakeNodeRegistry()
    return {
        "router": router,
        "repo": repo,
        "node_registry": nodes,
        "ctx": _ctx(repo, nodes),
        "emitter": emitter,
        "tracker": PendingTracker(),
        "policy": TimeoutPolicy(
            default_s=default_ttl_s, min_s=min_ttl_s, max_s=max_ttl_s
        ),
    }


def _req(**over):
    base = {"v": 1, "id": "r1", "type": "ping", "from": "n1"}
    base.update(over)
    return Request.model_validate(base)


@pytest.mark.asyncio
async def test_ping_happy_path_records_full_lifecycle():
    router = Router(DEFAULT_HANDLERS)
    repo = FakeRepo()
    emitter = FakeEmitter()
    await core.process_request(_req(), **_harness(router, repo, emitter))

    assert len(emitter.sent) == 1
    _, resp = emitter.sent[0]
    assert resp.status == "ok"
    assert resp.body == {"message": "pong"}

    states = [e[1] for e in repo.events]
    assert "validated" in states
    assert "handler_started" in states
    assert "response_published" in states
    assert repo.completions == [("r1", lifecycle.COMPLETED_OK, None)]


@pytest.mark.asyncio
async def test_unknown_type_is_rejected():
    router = Router([])
    repo = FakeRepo()
    emitter = FakeEmitter()
    await core.process_request(
        _req(type="nope"), **_harness(router, repo, emitter)
    )
    _, resp = emitter.sent[0]
    assert resp.status == "error"
    assert resp.error is not None
    assert resp.error.code == "unknown_type"
    assert repo.completions == [("r1", lifecycle.COMPLETED_ERROR, "unknown_type")]

    states = [e[1] for e in repo.events]
    assert "rejected" in states


@pytest.mark.asyncio
async def test_timeout_fires_on_slow_handler():
    class _Slow:
        type = "slow"
        async def handle(self, req, ctx):
            await asyncio.sleep(5)
            return Response.ok(req, {})

    router = Router([_Slow()])
    repo = FakeRepo()
    emitter = FakeEmitter()
    await core.process_request(
        _req(type="slow", ttl=1),
        **_harness(router, repo, emitter, default_ttl_s=1, max_ttl_s=1),
    )
    _, resp = emitter.sent[0]
    assert resp.status == "error"
    assert resp.error and resp.error.code == "timeout"
    assert repo.completions == [("r1", lifecycle.COMPLETED_ERROR, "timeout")]


@pytest.mark.asyncio
async def test_handler_exception_becomes_internal_error():
    class _Boom:
        type = "boom"
        async def handle(self, req, ctx):
            raise RuntimeError("kaboom")

    router = Router([_Boom()])
    repo = FakeRepo()
    emitter = FakeEmitter()
    await core.process_request(
        _req(type="boom"), **_harness(router, repo, emitter)
    )
    _, resp = emitter.sent[0]
    assert resp.error and resp.error.code == "internal"
    assert repo.completions == [("r1", lifecycle.COMPLETED_ERROR, "internal")]


@pytest.mark.asyncio
async def test_duplicate_request_emits_structured_error_and_persists():
    router = Router(DEFAULT_HANDLERS)
    repo = FakeRepo()
    emitter = FakeEmitter()
    harness = _harness(router, repo, emitter)

    # First: normal processing.
    await core.process_request(_req(id="d1"), **harness)
    # Second, same (from, id): must be rejected.
    await core.process_request(_req(id="d1"), **harness)

    assert len(emitter.sent) == 2
    resp_first = emitter.sent[0][1]
    resp_second = emitter.sent[1][1]
    assert resp_first.status == "ok"
    assert resp_second.status == "error"
    assert resp_second.error and resp_second.error.code == "duplicate"

    # Persistence records two completions.
    assert repo.completions[0] == ("d1", lifecycle.COMPLETED_OK, None)
    assert repo.completions[1] == ("d1", lifecycle.COMPLETED_ERROR, "duplicate")


@pytest.mark.asyncio
async def test_node_last_seen_is_marked_on_fresh_request():
    router = Router(DEFAULT_HANDLERS)
    repo = FakeRepo()
    emitter = FakeEmitter()
    harness = _harness(router, repo, emitter)
    await core.process_request(_req(from_="n42"), **harness)
    nodes: FakeNodeRegistry = harness["node_registry"]
    assert await nodes.get_last_seen("n42") is not None
