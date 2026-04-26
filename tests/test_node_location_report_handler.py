"""`node.location.report` handler tests."""

import pytest

from meshcore_rpc_services.schemas import Request


def _req(args=None, from_="n1"):
    return Request.model_validate({
        "v": 1, "id": "r1", "type": "node.location.report",
        "from": from_, "ttl": 10, "args": args or {},
    })


@pytest.mark.asyncio
async def test_happy_path_ack_and_persists(ctx, store):
    from meshcore_rpc_services.handlers.node_location_report import NodeLocationReportHandler
    req = _req({"lat": 27.94, "lon": -82.29})
    resp = await NodeLocationReportHandler().handle(req, ctx)

    assert resp.status == "ok"
    assert resp.body["ack"] is True
    assert isinstance(resp.body["ts"], float)

    loc = await store.get_node_location("n1")
    assert loc is not None
    assert loc["lat"] == pytest.approx(27.94)
    assert loc["lon"] == pytest.approx(-82.29)
    assert loc["source"] == "report"


@pytest.mark.asyncio
async def test_optional_fields_stored(ctx, store):
    from meshcore_rpc_services.handlers.node_location_report import NodeLocationReportHandler
    req = _req({"lat": 1.0, "lon": 2.0, "alt": 15.5, "acc": 3.0, "fix": 3, "spd": 1.2, "hdg": 270.0})
    await NodeLocationReportHandler().handle(req, ctx)

    loc = await store.get_node_location("n1")
    assert loc["alt"] == pytest.approx(15.5)
    assert loc["acc"] == pytest.approx(3.0)
    assert loc["fix"] == 3
    assert loc["spd"] == pytest.approx(1.2)
    assert loc["hdg"] == pytest.approx(270.0)


@pytest.mark.asyncio
async def test_uses_provided_ts(ctx, store):
    from meshcore_rpc_services.handlers.node_location_report import NodeLocationReportHandler
    req = _req({"lat": 0.0, "lon": 0.0, "ts": 9_999.0})
    resp = await NodeLocationReportHandler().handle(req, ctx)
    assert resp.body["ts"] == pytest.approx(9_999.0)


@pytest.mark.asyncio
async def test_missing_lat_is_bad_request(ctx):
    from meshcore_rpc_services.handlers.node_location_report import NodeLocationReportHandler
    from meshcore_rpc_services.errors import RpcError, BAD_REQUEST
    req = _req({"lon": -82.0})
    with pytest.raises(RpcError) as exc_info:
        await NodeLocationReportHandler().handle(req, ctx)
    assert exc_info.value.code == BAD_REQUEST


@pytest.mark.asyncio
async def test_missing_lon_is_bad_request(ctx):
    from meshcore_rpc_services.handlers.node_location_report import NodeLocationReportHandler
    from meshcore_rpc_services.errors import RpcError, BAD_REQUEST
    req = _req({"lat": 27.0})
    with pytest.raises(RpcError) as exc_info:
        await NodeLocationReportHandler().handle(req, ctx)
    assert exc_info.value.code == BAD_REQUEST


@pytest.mark.asyncio
async def test_lat_out_of_range_is_bad_request(ctx):
    from meshcore_rpc_services.handlers.node_location_report import NodeLocationReportHandler
    from meshcore_rpc_services.errors import RpcError, BAD_REQUEST
    req = _req({"lat": 91.0, "lon": 0.0})
    with pytest.raises(RpcError) as exc_info:
        await NodeLocationReportHandler().handle(req, ctx)
    assert exc_info.value.code == BAD_REQUEST


@pytest.mark.asyncio
async def test_lon_out_of_range_is_bad_request(ctx):
    from meshcore_rpc_services.handlers.node_location_report import NodeLocationReportHandler
    from meshcore_rpc_services.errors import RpcError, BAD_REQUEST
    req = _req({"lat": 0.0, "lon": 181.0})
    with pytest.raises(RpcError) as exc_info:
        await NodeLocationReportHandler().handle(req, ctx)
    assert exc_info.value.code == BAD_REQUEST


@pytest.mark.asyncio
async def test_publishes_retained_location_and_state(ctx):
    from meshcore_rpc_services.handlers.node_location_report import NodeLocationReportHandler
    req = _req({"lat": 10.0, "lon": 20.0})
    await NodeLocationReportHandler().handle(req, ctx)

    published_topics = [t for (t, _, _) in ctx.state.published]
    assert "mc/node/n1/location" in published_topics
    assert "mc/node/n1/state" in published_topics
    assert all(retain for (_, _, retain) in ctx.state.published)
