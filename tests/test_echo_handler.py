import pytest

from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.handlers.echo import handler as echo
from meshcore_rpc_services.schemas import Request

from tests._fakes import FakeNodeRegistry, FakeRepo, FakeSnapshot


def _ctx():
    return HandlerContext(
        snapshot=FakeSnapshot(), repo=FakeRepo(), nodes=FakeNodeRegistry()
    )


@pytest.mark.asyncio
async def test_echo_roundtrip():
    req = Request.model_validate(
        {"v": 1, "id": "e1", "type": "echo", "from": "n1",
         "args": {"msg": "hello"}}
    )
    resp = await echo.handle(req, _ctx())
    assert resp.status == "ok"
    assert resp.body == {"msg": "hello"}


@pytest.mark.asyncio
async def test_echo_missing_msg_returns_empty_string():
    req = Request.model_validate(
        {"v": 1, "id": "e2", "type": "echo", "from": "n1"}
    )
    resp = await echo.handle(req, _ctx())
    assert resp.body == {"msg": ""}


@pytest.mark.asyncio
async def test_echo_truncates_long_msg():
    req = Request.model_validate(
        {"v": 1, "id": "e3", "type": "echo", "from": "n1",
         "args": {"msg": "x" * 500}}
    )
    resp = await echo.handle(req, _ctx())
    assert resp.body is not None
    assert len(resp.body["msg"]) == 180
