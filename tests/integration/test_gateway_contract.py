"""Cross-repo contract test.

Spins up the meshcore-mqtt RPC adapter and meshcore-rpc-services side by
side against a single MQTT broker, and asserts a request flowing through
the gateway-shaped topic surface comes back through the gateway-shaped
response surface. This is the test that catches topic-prefix drift between
the two repos: if either side hardcodes the wrong string, this test fails
with no response.

Skipped automatically when:
  * `meshcore_mqtt` is not importable (the gateway repo isn't installed
    alongside meshcore-rpc-services), or
  * no MQTT broker is reachable at the configured host/port.

To run locally:
    pip install -e ../meshcore-mqtt
    docker compose up -d mosquitto
    pytest -m integration tests/integration/test_gateway_contract.py
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import aiomqtt
import pytest

# Skip the entire module if the gateway repo isn't installed.
meshcore_mqtt = pytest.importorskip(
    "meshcore_mqtt",
    reason=(
        "meshcore-mqtt not installed. Install it alongside this repo "
        "(`pip install -e ../meshcore-mqtt`) to run the contract test."
    ),
)
from meshcore_mqtt.config import (  # noqa: E402
    Config as GwConfig,
    ConnectionType,
    MeshCoreConfig,
    MQTTConfig as GwMQTTConfig,
)
from meshcore_mqtt.rpc_adapter import RpcAdapter  # noqa: E402

from meshcore_rpc_services.mqtt import topics  # noqa: E402

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Collector:
    """Subscribe to a topic and buffer JSON messages."""

    def __init__(self, host: str, port: int, topic: str) -> None:
        self._host = host
        self._port = port
        self._topic = topic
        self._messages: List[Dict[str, Any]] = []
        self._task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()

    async def _run(self) -> None:
        async with aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            identifier=f"contract-collector-{uuid.uuid4().hex[:6]}",
        ) as client:
            await client.subscribe(self._topic, qos=1)
            self._ready.set()
            async for msg in client.messages:
                try:
                    self._messages.append(json.loads(msg.payload.decode("utf-8")))
                except Exception:
                    pass

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        await self._ready.wait()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def wait_for(self, request_id: str, timeout: float = 5.0) -> Dict[str, Any]:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            for m in self._messages:
                if m.get("id") == request_id:
                    return m
            await asyncio.sleep(0.05)
        raise AssertionError(
            f"No response with id={request_id} on {self._topic} within {timeout}s"
        )


def _make_gateway_adapter(host: str, port: int) -> RpcAdapter:
    """Construct an RpcAdapter pointed at the test broker.

    The gateway's MeshCoreConfig requires a connection_type; we pick `tcp`
    because it doesn't actually try to connect — the adapter itself is
    pure MQTT and doesn't touch a serial port.
    """
    cfg = GwConfig(
        mqtt=GwMQTTConfig(broker=host, port=port, topic_prefix="meshcore", qos=1),
        meshcore=MeshCoreConfig(
            connection_type=ConnectionType.TCP, address="127.0.0.1", port=12345
        ),
    )
    return RpcAdapter(cfg)


async def _publish(host: str, port: int, topic: str, payload: bytes) -> None:
    async with aiomqtt.Client(
        hostname=host,
        port=port,
        identifier=f"contract-pub-{uuid.uuid4().hex[:6]}",
    ) as client:
        await client.publish(topic, payload=payload, qos=1)


# ---------------------------------------------------------------------------
# Fixture: gateway adapter running in a background thread
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway_adapter(broker_host, broker_port):
    """Run the gateway's RpcAdapter in a background thread for the test."""
    adapter = _make_gateway_adapter(broker_host, broker_port)
    thread = threading.Thread(target=adapter.run, daemon=True, name="rpc-adapter")
    thread.start()
    # Give it a moment to connect + subscribe.
    time.sleep(0.5)
    try:
        yield adapter
    finally:
        try:
            adapter.client.disconnect()
        except Exception:
            pass
        thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_to_service_ping_roundtrip(
    service_task, gateway_adapter, broker_host, broker_port
):
    """End-to-end through the gateway adapter:
        gateway-shaped inbound  →  rpc service  →  gateway-shaped outbound  →  send_msg

    This test would have failed on the topic-prefix mismatch:
    if the gateway hardcodes `meshcore/rpc/request` while the service
    subscribes to `mc/rpc/req`, the request never reaches the service
    and `send_msg` never fires.
    """
    pubkey = "a" + uuid.uuid4().hex[:11]  # 12-char hex; valid pubkey shape

    # The adapter forwards responses back to the gateway's send_msg topic.
    # We subscribe there to capture what the gateway *would* transmit.
    send_msg_topic = "meshcore/command/send_msg"
    collector = _Collector(broker_host, broker_port, send_msg_topic)
    await collector.start()
    try:
        req_id = uuid.uuid4().hex[:8]
        rpc_envelope = {
            "v": 1,
            "id": req_id,
            "type": "ping",
            # Note: `from` is intentionally omitted. The adapter must fill it
            # in from the topic-attested pubkey_prefix.
            "ttl": 5,
        }
        # The gateway publishes inbound DMs as Event __dict__ — a nested
        # shape with `payload` under the outer object.
        gw_inbound_payload = json.dumps(
            {
                "type": "EventType.CONTACT_MSG_RECV",
                "payload": {
                    "type": "PRIV",
                    "text": json.dumps(rpc_envelope),
                    "pubkey_prefix": pubkey,
                    "SNR": 7.5,
                },
                "attributes": {"pubkey_prefix": pubkey, "txt_type": 0},
            },
            separators=(",", ":"),
        ).encode("utf-8")

        # Simulate the gateway publishing the DM. The adapter is subscribed
        # to `meshcore/message/direct/+` and should turn this into an RPC
        # request on `mc/rpc/req`. The service then routes it, the handler
        # produces a response on `mc/rpc/resp/<pubkey>`, the adapter wraps
        # it as `meshcore/command/send_msg`.
        await _publish(
            broker_host,
            broker_port,
            f"meshcore/message/direct/{pubkey}",
            gw_inbound_payload,
        )

        # Wait for the send_msg command. The send_msg payload is
        # {"destination": <pubkey>, "message": <response json string>}.
        deadline = asyncio.get_event_loop().time() + 5.0
        cmd: Optional[Dict[str, Any]] = None
        while asyncio.get_event_loop().time() < deadline:
            for m in collector._messages:
                if m.get("destination") == pubkey:
                    cmd = m
                    break
            if cmd:
                break
            await asyncio.sleep(0.05)

        assert cmd is not None, (
            "No send_msg command observed within 5s. This typically means "
            "the gateway's RPC topics don't match the service's. Check "
            "rpc_adapter.RPC_REQUEST_TOPIC vs topics.RPC_REQUEST."
        )

        response = json.loads(cmd["message"])
        assert response["id"] == req_id
        assert response["status"] == "ok"
        assert response["body"] == {"msg": "pong"}
        # Identity attestation: the response is addressed to the topic-attested
        # pubkey, NOT to whatever was in the request envelope.
        assert response["to"] == pubkey
    finally:
        await collector.stop()


@pytest.mark.asyncio
async def test_gateway_status_topic_is_consumed_by_service(
    service_task, broker_host, broker_port
):
    """Publish a normalized gateway-status message in the same shape the
    gateway publishes it, then ask the service for `gateway.status` and
    assert it sees the message.

    This catches mismatch on the gateway-status topic specifically.
    """
    # Seed a normalized status payload exactly as the gateway emits it.
    now = time.time()
    gw_status = json.dumps(
        {"state": "connected", "detail": "uart ok", "ts": now, "since": now},
        separators=(",", ":"),
    ).encode("utf-8")

    async with aiomqtt.Client(
        hostname=broker_host,
        port=broker_port,
        identifier=f"contract-seed-{uuid.uuid4().hex[:6]}",
    ) as client:
        await client.publish(
            topics.GATEWAY_STATUS, payload=gw_status, qos=1, retain=True
        )

    # Give the service time to ingest the retained message.
    await asyncio.sleep(0.5)

    pubkey = "b" + uuid.uuid4().hex[:11]  # 12-char hex
    response_topic = topics.rpc_response_topic(pubkey)
    collector = _Collector(broker_host, broker_port, response_topic)
    await collector.start()
    try:
        req_id = uuid.uuid4().hex[:8]
        request = {
            "v": 1,
            "id": req_id,
            "type": "gateway.status",
            "from": pubkey,
            "ttl": 5,
        }
        await _publish(
            broker_host,
            broker_port,
            topics.RPC_REQUEST,
            json.dumps(request).encode("utf-8"),
        )

        resp = await collector.wait_for(req_id, timeout=5.0)
        assert resp["status"] == "ok"
        assert resp["body"]["state"] == "connected"
        assert resp["body"]["detail"] == "uart ok"
    finally:
        await collector.stop()
