import pytest

from meshcore_rpc_services.handlers.ping import handler as ping
from meshcore_rpc_services.schemas import Request


@pytest.mark.asyncio
async def test_ping_pong(ctx):
    req = Request.model_validate(
        {"v": 1, "id": "p1", "type": "ping", "from": "n1"}
    )
    resp = await ping.handle(req, ctx)
    assert resp.status == "ok"
    assert resp.body == {"message": "pong"}
    assert resp.to == "n1"
    assert resp.id == "p1"


@pytest.mark.asyncio
async def test_ping_echo_truncated(ctx):
    req = Request.model_validate(
        {
            "v": 1, "id": "p2", "type": "ping", "from": "n1",
            "args": {"echo": "x" * 200},
        }
    )
    resp = await ping.handle(req, ctx)
    assert resp.body is not None
    assert len(resp.body["echo"]) == 64
