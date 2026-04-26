"""Tests for StateAggregator. Real Store, capturing publisher (list.append)."""

import time

import pytest

from meshcore_rpc_services.state import LocationFix, StateAggregator, ONLINE_THRESHOLD_S


# ---------------------------------------------------------------------------
# apply_location
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_location_persists_and_publishes(state, store):
    fix = LocationFix(lat=27.94, lon=-82.29, ts=1_761_500_000.0)
    await state.apply_location("alice", fix, source="report")

    topics_emitted = [t for (t, _, _) in state.published]
    assert "mc/node/alice/location" in topics_emitted
    assert "mc/node/alice/state" in topics_emitted
    assert all(retain for (_, _, retain) in state.published)

    loc = await store.get_node_location("alice")
    assert loc is not None
    assert loc["lat"] == pytest.approx(27.94)
    assert loc["lon"] == pytest.approx(-82.29)
    assert loc["source"] == "report"


@pytest.mark.asyncio
async def test_apply_location_compact_json_drops_nones(state):
    fix = LocationFix(lat=1.0, lon=2.0, ts=1_000.0)
    await state.apply_location("bob", fix, source="report")

    import json
    loc_msg = next(
        payload for (t, payload, _) in state.published if "location" in t
    )
    body = json.loads(loc_msg)
    # alt, acc, fix, spd, hdg are None → should be absent
    assert "alt" not in body
    assert "acc" not in body


# ---------------------------------------------------------------------------
# apply_battery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_battery_persists_and_publishes(state, store):
    await state.apply_battery("alice", ts=1_000.0, pct=85, source="telemetry")

    topics_emitted = [t for (t, _, _) in state.published]
    assert "mc/node/alice/battery" in topics_emitted
    assert "mc/node/alice/state" in topics_emitted

    bat = await store.get_node_battery("alice")
    assert bat is not None
    assert bat["pct"] == 85


# ---------------------------------------------------------------------------
# apply_seen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_seen_updates_last_seen_and_publishes_state(state, store):
    ts = time.time()
    await state.apply_seen("charlie", ts)

    assert await store.get_last_seen("charlie") == pytest.approx(ts)
    topics_emitted = [t for (t, _, _) in state.published]
    assert "mc/node/charlie/state" in topics_emitted


# ---------------------------------------------------------------------------
# get_node_state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_node_state_unknown_node_is_none(state):
    result = await state.get_node_state("unknown-node")
    assert result is None


@pytest.mark.asyncio
async def test_online_flag_true_when_recently_seen(state, store):
    await store.mark_node_seen("dave", time.time())
    st = await state.get_node_state("dave")
    assert st is not None
    assert st["online"] is True


@pytest.mark.asyncio
async def test_online_flag_false_when_stale(state, store, monkeypatch):
    old_ts = time.time() - ONLINE_THRESHOLD_S - 1
    await store.mark_node_seen("dave", old_ts)

    st = await state.get_node_state("dave")
    assert st is not None
    assert st["online"] is False


@pytest.mark.asyncio
async def test_get_node_state_includes_battery_and_loc_ts(state, store):
    fix = LocationFix(lat=10.0, lon=20.0, ts=5_000.0)
    await state.apply_location("eve", fix, source="report")
    await state.apply_battery("eve", ts=5_100.0, pct=70)

    st = await state.get_node_state("eve")
    assert st is not None
    assert st["bat_pct"] == 70
    assert st["loc_ts"] == pytest.approx(5_000.0)


# ---------------------------------------------------------------------------
# apply_base_location
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_base_location_publishes_retained(state, store):
    fix = LocationFix(lat=27.77, lon=-82.64, ts=9_000.0, fix=3)
    await state.apply_base_location(fix, source="static")

    topics_emitted = [t for (t, _, _) in state.published]
    assert "mc/base/location" in topics_emitted
    assert all(retain for (_, _, retain) in state.published)

    base = await store.get_base_state("location")
    assert base is not None
    assert base["lat"] == pytest.approx(27.77)
    assert base["source"] == "static"


@pytest.mark.asyncio
async def test_get_base_location_round_trip(state):
    fix = LocationFix(lat=1.1, lon=2.2, ts=100.0)
    await state.apply_base_location(fix, source="static")

    result = await state.get_base_location()
    assert result is not None
    assert result["lat"] == pytest.approx(1.1)
    assert result["lon"] == pytest.approx(2.2)


@pytest.mark.asyncio
async def test_get_base_location_none_when_never_set(state):
    assert await state.get_base_location() is None