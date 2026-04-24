import time

import pytest

from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.handlers.node_last_seen import handler as nls
from meshcore_rpc_services.schemas import Request

from tests._fakes import FakeNodeRegistry, FakeRepo, FakeSnapshot


def _ctx(nodes):
    return HandlerContext(
        snapshot=FakeSnapshot(), repo=FakeRepo(), nodes=nodes
    )


@pytest.mark.asyncio
async def test_returns_null_for_unknown_node():
    nodes = FakeNodeRegistry()
    req = Request.model_validate(
        {"v": 1, "id": "nl1", "type": "node.last_seen", "from": "me",
         "args": {"node": "ghost"}}
    )
    resp = await nls.handle(req, _ctx(nodes))
    assert resp.body == {"node": "ghost", "ts": None, "age_s": None}


@pytest.mark.asyncio
async def test_returns_age_for_known_node():
    nodes = FakeNodeRegistry()
    past = time.time() - 120
    nodes.preset("alpha", past)
    req = Request.model_validate(
        {"v": 1, "id": "nl2", "type": "node.last_seen", "from": "me",
         "args": {"node": "alpha"}}
    )
    resp = await nls.handle(req, _ctx(nodes))
    assert resp.body is not None
    assert resp.body["node"] == "alpha"
    assert resp.body["ts"] == past
    assert 110 <= resp.body["age_s"] <= 130


@pytest.mark.asyncio
async def test_defaults_to_requester_when_node_arg_missing():
    nodes = FakeNodeRegistry()
    nodes.preset("me", time.time())
    req = Request.model_validate(
        {"v": 1, "id": "nl3", "type": "node.last_seen", "from": "me"}
    )
    resp = await nls.handle(req, _ctx(nodes))
    assert resp.body is not None
    assert resp.body["node"] == "me"
    assert resp.body["ts"] is not None
