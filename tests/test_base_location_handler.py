"""`base.location` handler tests."""

import time

import pytest

from meshcore_rpc_services.handlers.base_location import BaseLocationHandler, BASE_MAX_AGE_S
from meshcore_rpc_services.errors import STALE, UNAVAILABLE, RpcError
from meshcore_rpc_services.schemas import Request
from meshcore_rpc_services.state import LocationFix


def _req():
    return Request.model_validate({
        "v": 1, "id": "r1", "type": "base.location",
        "from": "n1", "ttl": 10,
    })


async def _set_base(ctx, lat=27.77, lon=-82.64, ts=None):
    fix = LocationFix(lat=lat, lon=lon, ts=ts or time.time(), fix=3)
    await ctx.state.apply_base_location(fix, source="static")


@pytest.mark.asyncio
async def test_unavailable_when_no_fix_ever_set(ctx):
    with pytest.raises(RpcError) as exc_info:
        await BaseLocationHandler().handle(_req(), ctx)
    assert exc_info.value.code == UNAVAILABLE


@pytest.mark.asyncio
async def test_happy_path_returns_fix(ctx):
    await _set_base(ctx)
    resp = await BaseLocationHandler().handle(_req(), ctx)

    assert resp.status == "ok"
    assert resp.body["lat"] == pytest.approx(27.77)
    assert resp.body["lon"] == pytest.approx(-82.64)
    assert resp.body["fix"] == 3
    assert isinstance(resp.body["age_s"], int)
    assert resp.body["age_s"] >= 0


@pytest.mark.asyncio
async def test_stale_when_fix_too_old(ctx, monkeypatch):
    old_ts = time.time() - BASE_MAX_AGE_S - 10
    await _set_base(ctx, ts=old_ts)

    with pytest.raises(RpcError) as exc_info:
        await BaseLocationHandler().handle(_req(), ctx)
    assert exc_info.value.code == STALE


@pytest.mark.asyncio
async def test_fresh_fix_not_stale(ctx):
    await _set_base(ctx, ts=time.time() - BASE_MAX_AGE_S + 30)
    resp = await BaseLocationHandler().handle(_req(), ctx)
    assert resp.status == "ok"