import pytest

from meshcore_rpc_services.handlers.gateway_status import handler as gw
from meshcore_rpc_services.schemas import Request, Response


@pytest.mark.asyncio
async def test_gateway_status_returns_compact_body(ctx, store):
    from meshcore_rpc_services.lifecycle import COMPLETED_OK
    fake = Request.model_validate(
        {"v": 1, "id": "x", "type": "ping", "from": "n1"}
    )
    ok_resp = Response.ok(fake, {"message": "pong"})
    await store.record_received(fake, ttl_s=5)
    await store.record_completion("x", "n1", final_state=COMPLETED_OK, response=ok_resp)

    req = Request.model_validate(
        {"v": 1, "id": "g1", "type": "gateway.status", "from": "n1"}
    )
    resp = await gw.handle(req, ctx)
    assert resp.status == "ok"
    assert resp.body is not None
    assert resp.body["state"] == "connected"
    assert resp.body["ok"] == 1
    assert resp.body["err"] == 0


@pytest.mark.asyncio
async def test_gateway_status_handles_unknown(ctx, snapshot_fn):
    snapshot_fn.state["state"] = None

    req = Request.model_validate(
        {"v": 1, "id": "g2", "type": "gateway.status", "from": "n1"}
    )
    resp = await gw.handle(req, ctx)
    assert resp.body is not None
    assert resp.body["state"] == "unknown"
    assert resp.body["pending"] == 0


@pytest.mark.asyncio
async def test_gateway_status_includes_detail_and_since(ctx, snapshot_fn):
    snapshot_fn.state["detail"] = "serial timeout"
    snapshot_fn.state["since"] = 1761500000.0

    req = Request.model_validate(
        {"v": 1, "id": "g3", "type": "gateway.status", "from": "n1"}
    )
    resp = await gw.handle(req, ctx)
    assert resp.body["detail"] == "serial timeout"
    assert resp.body["since"] == 1761500000.0