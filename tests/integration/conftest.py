"""Integration test harness.

These tests talk to a real MQTT broker. If no broker is reachable at
MESHCORE_RPC_SERVICES_TEST_MQTT_HOST:PORT (default localhost:1883), every
test in this directory is skipped with a clear reason.

Run:

    # With docker compose up -d mosquitto
    pytest -m integration

or

    MESHCORE_RPC_SERVICES_TEST_MQTT_HOST=broker.lan pytest -m integration
"""

from __future__ import annotations

import os
import socket
import tempfile
import uuid

import pytest
import pytest_asyncio

from meshcore_rpc_services.config import (
    AppConfig,
    MQTTConfig,
    RetentionConfig,
    ServiceConfig,
    TimeoutConfig,
)
from meshcore_rpc_services.transport import Service


def _broker_host() -> str:
    return os.environ.get(
        "MESHCORE_RPC_SERVICES_TEST_MQTT_HOST", "localhost"
    )


def _broker_port() -> int:
    return int(os.environ.get("MESHCORE_RPC_SERVICES_TEST_MQTT_PORT", "1883"))


def _broker_reachable() -> bool:
    try:
        with socket.create_connection(
            (_broker_host(), _broker_port()), timeout=0.5
        ):
            return True
    except OSError:
        return False


# Auto-skip every test in this directory if no broker is reachable.
def pytest_collection_modifyitems(config, items):
    if _broker_reachable():
        return
    skip = pytest.mark.skip(
        reason=f"No MQTT broker at {_broker_host()}:{_broker_port()}"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def broker_host() -> str:
    return _broker_host()


@pytest.fixture
def broker_port() -> int:
    return _broker_port()


@pytest_asyncio.fixture
async def service_task(tmp_path, broker_host, broker_port):
    """Start Service in a background task. Yields the AppConfig used.

    The test body publishes requests and asserts on responses; the Service
    does the actual processing. Uses a unique client_id per test so parallel
    runs don't clobber each other.
    """
    import asyncio

    cfg = AppConfig(
        mqtt=MQTTConfig(
            host=broker_host,
            port=broker_port,
            client_id=f"rpc-svc-test-{uuid.uuid4().hex[:6]}",
            qos=1,
        ),
        service=ServiceConfig(
            db_path=str(tmp_path / "it.sqlite3"),
            log_level="WARNING",
            timeouts=TimeoutConfig(default_s=5, min_s=1, max_s=10),
            retention=RetentionConfig(days=30, interval_s=3600.0),
        ),
    )

    service = Service(cfg)
    task = asyncio.create_task(service.run())
    # Give it a moment to subscribe.
    await asyncio.sleep(0.5)
    try:
        yield cfg
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
