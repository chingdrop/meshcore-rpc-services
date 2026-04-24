#!/usr/bin/env python3
"""Publish retained gateway status + health messages for the bench.

Useful when you want to exercise the `gateway.status` handler without
running the real gateway.

Usage:
    python scripts/seed_gateway_status.py --status connected --health ok
"""

from __future__ import annotations

import argparse
import time

import paho.mqtt.client as mqtt

GATEWAY_STATUS = "meshcore/gateway/status"
GATEWAY_HEALTH = "meshcore/gateway/health"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--status", default="connected")
    p.add_argument("--health", default="ok")
    opts = p.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="seed-gw")
    client.connect(opts.host, opts.port, keepalive=15)
    client.loop_start()
    time.sleep(0.2)
    client.publish(GATEWAY_STATUS, opts.status.encode("utf-8"), qos=1, retain=True)
    client.publish(GATEWAY_HEALTH, opts.health.encode("utf-8"), qos=1, retain=True)
    time.sleep(0.3)
    client.loop_stop()
    client.disconnect()
    print(f"Retained on {GATEWAY_STATUS}: {opts.status}")
    print(f"Retained on {GATEWAY_HEALTH}: {opts.health}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
