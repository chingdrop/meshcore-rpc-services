"""`return_to_base` handler tests."""

import time

import pytest

from meshcore_rpc_services.handlers.return_to_base import ReturnToBaseHandler, _MAX_AGE_S
from meshcore_rpc_services.errors import STALE, UNAVAILABLE, RpcError
from meshcore_rpc_services.schemas import Request
from meshcore_rpc_services.state import LocationFix

_HANDLER = ReturnToBaseHandler()


def _req(args=None, from_="field"):
    return Request.model_validate({
        "v": 1, "id": "r1", "type": "return_to_base",
        "from": from_, "ttl": 10, "args": args or {},
    })


async def _set_base(ctx, lat=27.77, lon=-82.64, ts=None):
    fix = LocationFix(lat=lat, lon=lon, ts=ts or time.time(), fix=3)
    await ctx.state.apply_base_location(fix, source="static")


async def _set_caller(ctx, node_id="field", lat=27.94, lon=-82.29, ts=None):
    fix = LocationFix(lat=lat, lon=lon, ts=ts or time.time())
    await ctx.state.apply_location(node_id, fix, source="report")


# ---------------------------------------------------------------------------
# unavailable cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unavailable_when_base_never_set(ctx):
    await _set_caller(ctx)
    with pytest.raises(RpcError) as exc_info:
        await _HANDLER.handle(_req(), ctx)
    assert exc_info.value.code == UNAVAILABLE


@pytest.mark.asyncio
async def test_unavailable_when_caller_never_reported_and_no_args(ctx):
    await _set_base(ctx)
    with pytest.raises(RpcError) as exc_info:
        await _HANDLER.handle(_req(), ctx)
    assert exc_info.value.code == UNAVAILABLE


# ---------------------------------------------------------------------------
# stale cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_when_caller_fix_too_old(ctx):
    old_ts = time.time() - _MAX_AGE_S - 10
    await _set_caller(ctx, ts=old_ts)
    await _set_base(ctx)
    with pytest.raises(RpcError) as exc_info:
        await _HANDLER.handle(_req(), ctx)
    assert exc_info.value.code == STALE


@pytest.mark.asyncio
async def test_stale_when_base_fix_too_old(ctx):
    await _set_caller(ctx)
    old_ts = time.time() - _MAX_AGE_S - 10
    await _set_base(ctx, ts=old_ts)
    with pytest.raises(RpcError) as exc_info:
        await _HANDLER.handle(_req(), ctx)
    assert exc_info.value.code == STALE


# ---------------------------------------------------------------------------
# happy path — using aggregator state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_via_aggregator_state(ctx):
    await _set_caller(ctx, lat=27.94, lon=-82.29)
    await _set_base(ctx, lat=27.77, lon=-82.64)

    resp = await _HANDLER.handle(_req(), ctx)

    assert resp.status == "ok"
    assert isinstance(resp.body["bearing"], int)
    assert 0 <= resp.body["bearing"] < 360
    assert resp.body["dist_m"] > 0
    assert resp.body["from"]["lat"] == pytest.approx(27.94)
    assert resp.body["base"]["lat"] == pytest.approx(27.77)


@pytest.mark.asyncio
async def test_bearing_and_distance_roughly_correct(ctx):
    # Field node east of base → bearing to base should be westward (~270°).
    await _set_caller(ctx, lat=0.0, lon=1.0)
    await _set_base(ctx, lat=0.0, lon=0.0)

    resp = await _HANDLER.handle(_req(), ctx)

    assert abs(resp.body["bearing"] - 270) <= 1
    # 1° longitude at equator ≈ 111 km.
    assert 109_000 < resp.body["dist_m"] < 113_000


# ---------------------------------------------------------------------------
# happy path — explicit lat/lon in args bypasses aggregator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_args_bypass_aggregator(ctx):
    # Caller provides coordinates directly; no stored location needed.
    await _set_base(ctx, lat=0.0, lon=0.0)

    resp = await _HANDLER.handle(_req({"lat": 0.0, "lon": 1.0}), ctx)

    assert resp.status == "ok"
    assert resp.body["from"]["lat"] == pytest.approx(0.0)
    assert resp.body["from"]["lon"] == pytest.approx(1.0)
    assert resp.body["from"]["age_s"] == 0


@pytest.mark.asyncio
async def test_explicit_args_stale_aggregator_is_ignored(ctx):
    # Even with a stale stored fix, explicit args should succeed.
    old_ts = time.time() - _MAX_AGE_S - 10
    await _set_caller(ctx, ts=old_ts)
    await _set_base(ctx)

    resp = await _HANDLER.handle(_req({"lat": 1.0, "lon": 1.0}), ctx)
    assert resp.status == "ok"


# ---------------------------------------------------------------------------
# response shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_response_keys_present(ctx):
    await _set_caller(ctx)
    await _set_base(ctx)

    resp = await _HANDLER.handle(_req(), ctx)
    body = resp.body
    assert "bearing" in body
    assert "dist_m" in body
    assert {"lat", "lon", "age_s"} <= body["base"].keys()
    assert {"lat", "lon", "age_s"} <= body["from"].keys()
