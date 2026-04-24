import pytest

from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.handlers.gateway_status import handler as gw
from meshcore_rpc_services.schemas import Request

from tests._fakes import FakeNodeRegistry, FakeSnapshot


class _StubRepo:
    def __init__(self, counts):
        self._counts = counts
    async def counts(self):
        return self._counts
    async def record_received(self, request, ttl_s): ...
    async def record_event(self, request_id, state, detail=None): ...
    async def record_completion(self, request_id, final_state, response=None, error_code=None): ...
    async def purge_before(self, cutoff): return 0


def _ctx(snap, counts):
    return HandlerContext(
        snapshot=snap, repo=_StubRepo(counts), nodes=FakeNodeRegistry()
    )


@pytest.mark.asyncio
async def test_gateway_status_returns_compact_body():
    req = Request.model_validate(
        {"v": 1, "id": "g1", "type": "gateway.status", "from": "n1"}
    )
    ctx = _ctx(
        FakeSnapshot("connected", "ok"),
        {"completed_ok": 5, "completed_error": 1, "timeout": 2, "pending": 3},
    )
    resp = await gw.handle(req, ctx)
    assert resp.status == "ok"
    assert resp.body == {
        "gw": "connected",
        "hb": "ok",
        "pending": 3,
        "ok": 5,
        "err": 1,
        "to": 2,
    }


@pytest.mark.asyncio
async def test_gateway_status_handles_unknown():
    req = Request.model_validate(
        {"v": 1, "id": "g2", "type": "gateway.status", "from": "n1"}
    )
    ctx = _ctx(FakeSnapshot(status=None, health=None), {})
    resp = await gw.handle(req, ctx)
    assert resp.body is not None
    assert resp.body["gw"] == "unknown"
    assert resp.body["hb"] == "unknown"
    assert resp.body["pending"] == 0
