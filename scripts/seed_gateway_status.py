#!/usr/bin/env python3
"""Publish a retained gateway status message for the bench.

Useful when you want to exercise the `gateway.status` handler without
running the real gateway.

Usage:
    python scripts/seed_gateway_status.py --state connected
    python scripts/seed_gateway_status.py --state error --detail "serial timeout"
"""

from __future__ import annotations

import argparse
import json
import time

import paho.mqtt.client as mqtt

GATEWAY_STATUS = "mc/gateway/status"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--state", default="connected",
                   choices=["connected", "disconnected", "error", "unknown"])
    p.add_argument("--detail", default=None)
    opts = p.parse_args()

    now = time.time()
    payload = {"state": opts.state, "detail": opts.detail, "ts": now, "since": now}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="seed-gw")
    client.connect(opts.host, opts.port, keepalive=15)
    client.loop_start()
    time.sleep(0.2)
    client.publish(GATEWAY_STATUS, json.dumps(payload), qos=1, retain=True)
    time.sleep(0.3)
    client.loop_stop()
    client.disconnect()
    print(f"Retained on {GATEWAY_STATUS}: {payload}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
