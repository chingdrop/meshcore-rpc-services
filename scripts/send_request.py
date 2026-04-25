#!/usr/bin/env python3
"""Publish a test RPC request and print the response, if any.

Usage:
    python scripts/send_request.py --type ping --from mynode
    python scripts/send_request.py --type echo --from mynode --args '{"msg": "hi"}'
    python scripts/send_request.py --type gateway.status --from mynode
    python scripts/send_request.py --type node.last_seen --from mynode --args '{"node": "other-node"}'

Requires only ``paho-mqtt``, which is already transitively available via
``aiomqtt``. This script does not import the service code so you can run
it against a deployed instance from anywhere on the network.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from typing import Any

import paho.mqtt.client as mqtt

RPC_REQUEST_TOPIC = "meshcore/rpc/request"
RPC_RESPONSE_PREFIX = "meshcore/rpc/response"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--type", required=True, help="RPC type, e.g. ping")
    p.add_argument("--from", dest="from_", default=f"devbench-{uuid.uuid4().hex[:6]}")
    p.add_argument("--id", default=None)
    p.add_argument("--ttl", type=int, default=10)
    p.add_argument("--args", default="{}", help="JSON object")
    p.add_argument("--wait", type=float, default=10.0, help="seconds to wait for response")
    opts = p.parse_args()

    try:
        args_obj: dict[str, Any] = json.loads(opts.args)
    except json.JSONDecodeError as e:
        print(f"Bad --args JSON: {e}", file=sys.stderr)
        return 2

    req_id = opts.id or uuid.uuid4().hex[:8]
    request = {
        "v": 1,
        "id": req_id,
        "type": opts.type,
        "from": opts.from_,
        "ttl": opts.ttl,
        "args": args_obj,
    }
    response_topic = f"{RPC_RESPONSE_PREFIX}/{opts.from_}"

    got_response: dict[str, Any] = {}

    def on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: Any, *_: Any) -> None:
        client.subscribe(response_topic, qos=1)

    def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            body = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if body.get("id") == req_id:
            got_response["value"] = body
            client.disconnect()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"send-req-{req_id}")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(opts.host, opts.port, keepalive=30)
    client.loop_start()

    # Give the subscribe a moment to land.
    time.sleep(0.2)
    client.publish(
        RPC_REQUEST_TOPIC,
        json.dumps(request).encode("utf-8"),
        qos=1,
    )
    print(f"-> {RPC_REQUEST_TOPIC}: {json.dumps(request)}")

    deadline = time.time() + opts.wait
    while not got_response and time.time() < deadline:
        time.sleep(0.1)
    client.loop_stop()
    client.disconnect()

    if got_response:
        print(f"<- {response_topic}: {json.dumps(got_response['value'])}")
        return 0
    print(f"(no response within {opts.wait}s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
