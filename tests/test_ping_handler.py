import pytest

from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.handlers.ping import handler as ping_handler
from meshcore_rpc_services.schemas import Request

from tests._fakes import FakeNodeRegistry, FakeRepo, FakeSnapshot


def _ctx() -> HandlerContext:
    return HandlerContext(
        snapshot=FakeSnapshot(), repo=FakeRepo(), nodes=FakeNodeRegistry()
    )


@pytest.mark.asyncio
async def test_ping_pong():
    req = Request.model_validate(
        {"v": 1, "id": "p1", "type": "ping", "from": "n1"}
    )
    resp = await ping_handler.handle(req, _ctx())
    assert resp.status == "ok"
    assert resp.body == {"message": "pong"}
    assert resp.to == "n1"
    assert resp.id == "p1"


@pytest.mark.asyncio
async def test_ping_echo_truncated():
    req = Request.model_validate(
        {
            "v": 1, "id": "p2", "type": "ping", "from": "n1",
            "args": {"echo": "x" * 200},
        }
    )
    resp = await ping_handler.handle(req, _ctx())
    assert resp.body is not None
    assert len(resp.body["echo"]) == 64
