"""`node.status` handler tests."""

import time

import pytest

from meshcore_rpc_services.handlers.node_status import NodeStatusHandler
from meshcore_rpc_services.errors import UNAVAILABLE, RpcError
from meshcore_rpc_services.schemas import Request
from meshcore_rpc_services.state import ONLINE_THRESHOLD_S


def _req(args=None, from_="requester"):
    return Request.model_validate({
        "v": 1, "id": "r1", "type": "node.status",
        "from": from_, "ttl": 10, "args": args or {},
    })


@pytest.mark.asyncio
async def test_unavailable_for_unknown_node(ctx):
    with pytest.raises(RpcError) as exc_info:
        await NodeStatusHandler().handle(_req({"node": "ghost"}), ctx)
    assert exc_info.value.code == UNAVAILABLE


@pytest.mark.asyncio
async def test_defaults_to_requester_when_node_arg_absent(ctx, store):
    await store.mark_node_seen("requester", time.time())
    resp = await NodeStatusHandler().handle(_req(), ctx)
    assert resp.status == "ok"
    assert resp.body["id"] == "requester"


@pytest.mark.asyncio
async def test_online_true_when_recently_seen(ctx, store):
    await store.mark_node_seen("n1", time.time())
    resp = await NodeStatusHandler().handle(_req({"node": "n1"}), ctx)
    assert resp.body["online"] is True
    assert resp.body["last_seen_age_s"] >= 0


@pytest.mark.asyncio
async def test_online_false_when_stale(ctx, store):
    old_ts = time.time() - ONLINE_THRESHOLD_S - 1
    await store.mark_node_seen("n1", old_ts)
    resp = await NodeStatusHandler().handle(_req({"node": "n1"}), ctx)
    assert resp.body["online"] is False


@pytest.mark.asyncio
async def test_bat_pct_included_when_known(ctx, store):
    await ctx.state.apply_battery("n1", ts=time.time(), pct=72)
    resp = await NodeStatusHandler().handle(_req({"node": "n1"}), ctx)
    assert resp.body["bat_pct"] == 72


@pytest.mark.asyncio
async def test_bat_pct_absent_when_never_reported(ctx, store):
    await store.mark_node_seen("n1", time.time())
    resp = await NodeStatusHandler().handle(_req({"node": "n1"}), ctx)
    assert "bat_pct" not in resp.body