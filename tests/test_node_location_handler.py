"""`node.location` handler tests."""

import time

import pytest

from meshcore_rpc_services.errors import UNAVAILABLE, RpcError
from meshcore_rpc_services.handlers.node_location import NodeLocationHandler
from meshcore_rpc_services.schemas import Request
from meshcore_rpc_services.state import LocationFix


def _req(args=None, from_="requester"):
    return Request.model_validate({
        "v": 1, "id": "r1", "type": "node.location",
        "from": from_, "ttl": 10, "args": args or {},
    })


async def _report(ctx, node_id, lat=10.0, lon=20.0):
    fix = LocationFix(lat=lat, lon=lon, ts=time.time())
    await ctx.state.apply_location(node_id, fix, source="report")


@pytest.mark.asyncio
async def test_unavailable_for_unknown_node(ctx):
    with pytest.raises(RpcError) as exc_info:
        await NodeLocationHandler().handle(_req({"node": "ghost"}), ctx)
    assert exc_info.value.code == UNAVAILABLE


@pytest.mark.asyncio
async def test_defaults_to_requester_when_node_arg_absent(ctx):
    await _report(ctx, "requester", lat=5.0, lon=6.0)
    resp = await NodeLocationHandler().handle(_req(), ctx)
    assert resp.status == "ok"
    assert resp.body["node"] == "requester"
    assert resp.body["lat"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_explicit_node_arg_queries_that_node(ctx):
    await _report(ctx, "target", lat=1.0, lon=2.0)
    resp = await NodeLocationHandler().handle(_req({"node": "target"}, from_="other"), ctx)
    assert resp.status == "ok"
    assert resp.body["node"] == "target"
    assert resp.body["lat"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_response_includes_age_s(ctx):
    await _report(ctx, "n1")
    resp = await NodeLocationHandler().handle(_req({"node": "n1"}), ctx)
    assert isinstance(resp.body["age_s"], int)
    assert resp.body["age_s"] >= 0
