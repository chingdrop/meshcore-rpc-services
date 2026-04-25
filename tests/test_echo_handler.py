import pytest

from meshcore_rpc_services.handlers.echo import handler as echo
from meshcore_rpc_services.schemas import Request


@pytest.mark.asyncio
async def test_echo_roundtrip(ctx):
    req = Request.model_validate(
        {"v": 1, "id": "e1", "type": "echo", "from": "n1",
         "args": {"msg": "hello"}}
    )
    resp = await echo.handle(req, ctx)
    assert resp.status == "ok"
    assert resp.body == {"msg": "hello"}


@pytest.mark.asyncio
async def test_echo_missing_msg_returns_empty_string(ctx):
    req = Request.model_validate(
        {"v": 1, "id": "e2", "type": "echo", "from": "n1"}
    )
    resp = await echo.handle(req, ctx)
    assert resp.body == {"msg": ""}


@pytest.mark.asyncio
async def test_echo_truncates_long_msg(ctx):
    req = Request.model_validate(
        {"v": 1, "id": "e3", "type": "echo", "from": "n1",
         "args": {"msg": "x" * 500}}
    )
    resp = await echo.handle(req, ctx)
    assert resp.body is not None
    assert len(resp.body["msg"]) == 180
