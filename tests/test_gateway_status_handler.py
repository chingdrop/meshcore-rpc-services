import pytest

from meshcore_app.handlers.base import HandlerContext
from meshcore_app.handlers.gateway_status import handler as gw_handler
from meshcore_app.schemas import Request


class _StubBus:
    def __init__(self, status="connected", health="ok"):
        self._snap = {"status": status, "health": health}

    async def get_gateway_snapshot(self):
        return self._snap


class _StubStore:
    def __init__(self, counts):
        self._counts = counts

    async def counts(self):
        return self._counts


@pytest.mark.asyncio
async def test_gateway_status_returns_compact_body():
    req = Request.model_validate(
        {"v": 1, "id": "g1", "type": "gateway.status", "from": "n1"}
    )
    ctx = HandlerContext(
        bus=_StubBus("connected", "ok"),
        store=_StubStore({"ok": 5, "error": 1, "timeout": 2, "pending": 3}),
    )
    resp = await gw_handler.handle(req, ctx)
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
    ctx = HandlerContext(
        bus=_StubBus(status=None, health=None), store=_StubStore({})
    )
    resp = await gw_handler.handle(req, ctx)
    assert resp.body is not None
    assert resp.body["gw"] == "unknown"
    assert resp.body["hb"] == "unknown"
    assert resp.body["pending"] == 0
