"""Tests for MqttBus._maybe_cache JSON parsing of the gateway status topic."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from meshcore_rpc_services.mqtt import topics
from meshcore_rpc_services.transport.bus import MqttBus


def _make_msg(topic: str, payload: str) -> MagicMock:
    msg = MagicMock()
    msg.topic = MagicMock()
    msg.topic.__str__ = lambda self: topic
    msg.payload = payload.encode("utf-8") if payload else b""
    return msg


def _make_bus() -> MqttBus:
    cfg = MagicMock()
    cfg.qos = 1
    return MqttBus(cfg, store=None)


@pytest.mark.asyncio
async def test_valid_json_status_is_cached():
    bus = _make_bus()
    payload = json.dumps({"state": "connected", "detail": "uart ok", "since": 1000.0})
    await bus._maybe_cache(_make_msg(topics.GATEWAY_STATUS, payload))

    snap = await bus.get_gateway_snapshot()
    assert snap["state"] == "connected"
    assert snap["detail"] == "uart ok"
    assert snap["since"] == 1000.0
    assert snap["snapped_at"] is not None


@pytest.mark.asyncio
async def test_missing_fields_default_to_unknown_and_none():
    bus = _make_bus()
    await bus._maybe_cache(_make_msg(topics.GATEWAY_STATUS, "{}"))

    snap = await bus.get_gateway_snapshot()
    assert snap["state"] == "unknown"
    assert snap["detail"] is None
    assert snap["since"] is None


@pytest.mark.asyncio
async def test_non_json_payload_does_not_crash_and_leaves_state_unchanged():
    bus = _make_bus()
    await bus._maybe_cache(_make_msg(topics.GATEWAY_STATUS, "not-json"))

    snap = await bus.get_gateway_snapshot()
    # State was never set, so everything is still None.
    assert snap["state"] is None
    assert snap["snapped_at"] is None


@pytest.mark.asyncio
async def test_empty_payload_does_not_crash():
    bus = _make_bus()
    msg = _make_msg(topics.GATEWAY_STATUS, "")
    msg.payload = b""
    await bus._maybe_cache(msg)

    # Empty body is treated as {} — state defaults to "unknown", no crash.
    snap = await bus.get_gateway_snapshot()
    assert snap["state"] == "unknown"


@pytest.mark.asyncio
async def test_non_status_topic_is_ignored():
    bus = _make_bus()
    await bus._maybe_cache(_make_msg(topics.RPC_REQUEST, '{"state": "connected"}'))

    snap = await bus.get_gateway_snapshot()
    assert snap["state"] is None


@pytest.mark.asyncio
async def test_store_record_called_on_valid_payload():
    cfg = MagicMock()
    cfg.qos = 1
    store = MagicMock()
    store.record_gateway_snapshot = AsyncMock()
    bus = MqttBus(cfg, store=store)

    payload = json.dumps({"state": "connected", "since": 999.0})
    await bus._maybe_cache(_make_msg(topics.GATEWAY_STATUS, payload))

    store.record_gateway_snapshot.assert_awaited_once_with(
        state="connected", detail=None, since=999.0
    )