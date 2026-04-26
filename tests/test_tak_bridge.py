"""Tests for the bridge core. We exercise it without a real MQTT broker
or TAK server by stubbing the sink and feeding messages directly to
_handle_mqtt_message.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from meshcore_rpc_services.config import AppConfig
from meshcore_rpc_services.tak.bridge import Bridge, _extract_node_id


def _msg(topic: str, payload: dict):
    """Mimic an aiomqtt.Message just enough for _handle_mqtt_message."""
    return SimpleNamespace(
        topic=topic,
        payload=json.dumps(payload).encode("utf-8"),
    )


@pytest.fixture
def bridge():
    cfg = AppConfig()
    b = Bridge(cfg)
    # Replace the sink with a mock so we capture sends without networking.
    b._sink = SimpleNamespace(send=AsyncMock())
    return b


# ---------------------------------------------------------------------------
# Topic parsing
# ---------------------------------------------------------------------------

def test_extract_node_id_location():
    assert _extract_node_id("mc/node/alice/location", "/location") == "alice"


def test_extract_node_id_state():
    assert _extract_node_id("mc/node/a1b2c3d4e5f6/state", "/state") == "a1b2c3d4e5f6"


def test_extract_node_id_wrong_topic_returns_none():
    assert _extract_node_id("mc/base/location", "/location") is None
    assert _extract_node_id("meshcore/node/x/location", "/location") is None


def test_extract_node_id_empty_returns_none():
    assert _extract_node_id("mc/node//location", "/location") is None


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_node_location_emits_cot(bridge):
    msg = _msg(
        "mc/node/alice/location",
        {"id": "alice", "lat": 27.94, "lon": -82.29, "ts": 1761500000.0},
    )
    await bridge._handle_mqtt_message(msg)

    assert bridge._sink.send.await_count == 1
    cot = bridge._sink.send.await_args.args[0]
    assert b'uid="meshcore.alice"' in cot
    assert b'lat="27.9400000"' in cot
    assert b'lon="-82.2900000"' in cot


@pytest.mark.asyncio
async def test_base_location_emits_base_cot(bridge):
    msg = _msg(
        "mc/base/location",
        {"lat": 27.77, "lon": -82.64, "ts": 1761500000.0, "fix": 3},
    )
    await bridge._handle_mqtt_message(msg)

    assert bridge._sink.send.await_count == 1
    cot = bridge._sink.send.await_args.args[0]
    assert b'uid="meshcore.base"' in cot
    # Base type is a-f-G-U-C-I (default)
    assert b'type="a-f-G-U-C-I"' in cot


@pytest.mark.asyncio
async def test_state_message_merges_with_location(bridge):
    """A node might publish location first, then state. Both should
    contribute to a single tracked entity."""
    await bridge._handle_mqtt_message(_msg(
        "mc/node/alice/location",
        {"lat": 27.94, "lon": -82.29, "ts": 1.0},
    ))
    await bridge._handle_mqtt_message(_msg(
        "mc/node/alice/state",
        {"id": "alice", "online": True, "last_seen_age_s": 5,
         "bat_pct": 78, "rssi": -92, "snr": 7.5},
    ))

    # Two CoT events emitted (one per message). The latest carries remarks.
    last_cot = bridge._sink.send.await_args.args[0]
    assert b"battery=78" in last_cot
    assert b"rssi=-92" in last_cot
    assert b"snr=7.5" in last_cot


@pytest.mark.asyncio
async def test_state_without_location_does_not_emit(bridge):
    """A state message with no lat/lon shouldn't produce a CoT (CoT requires
    a point, and we have nothing real to point at)."""
    await bridge._handle_mqtt_message(_msg(
        "mc/node/alice/state",
        {"id": "alice", "online": True, "last_seen_age_s": 5},
    ))
    assert bridge._sink.send.await_count == 0


@pytest.mark.asyncio
async def test_garbage_payload_is_dropped_silently(bridge):
    bad = SimpleNamespace(topic="mc/node/x/location", payload=b"not json")
    await bridge._handle_mqtt_message(bad)
    assert bridge._sink.send.await_count == 0


@pytest.mark.asyncio
async def test_callsign_template_used(bridge):
    bridge._cfg.tak.callsign_template = "FN-{id}"
    await bridge._handle_mqtt_message(_msg(
        "mc/node/deadbeef1234/location",
        {"lat": 1.0, "lon": 2.0, "ts": 1.0},
    ))
    cot = bridge._sink.send.await_args.args[0]
    assert b'callsign="FN-deadbe"' in cot  # truncated to first 6 chars
