"""Bridge core: MQTT retained state → CoT events.

Topology:

    aiomqtt task ──▶ updates self._state[node_id] dict
                     publishes CoT to TakSink immediately on change

    heartbeat task ──▶ every publish_interval_s, republish CoT for every
                       known entity (so TAK gets a steady refresh and stale
                       attributes get nudged forward)

    TakSink ──▶ owns its own connection lifecycle; we just hand it bytes.

The state dict is the entire memory of the bridge. No SQLite, no files.
Restart and the next retained MQTT delivery rebuilds it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiomqtt

from meshcore_rpc_services.config import AppConfig
from meshcore_rpc_services.mqtt import topics

from .cot import build_cot
from .takserver import TakSink

log = logging.getLogger(__name__)

# Topic filters. NODE_PREFIX comes from the canonical contract module —
# imported, not redeclared, so the bridge can never drift from the
# service. The wildcard suffixes are bridge-side concerns.
NODE_LOCATION_FILTER = f"{topics.NODE_PREFIX}/+/location"
NODE_STATE_FILTER = f"{topics.NODE_PREFIX}/+/state"
BASE_LOCATION_TOPIC = topics.BASE_LOCATION

# Synthetic ID we use for the base in our internal state map.
_BASE_KEY = "__base__"


class Bridge:
    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._sink = TakSink(cfg.tak.server.host, cfg.tak.server.port)
        # In-memory state: node_id → dict with at least {lat, lon, ts}.
        # `__base__` holds the home base.
        self._state: Dict[str, Dict[str, Any]] = {}
        self._stop = asyncio.Event()

    async def run(self) -> None:
        log.info(
            "TAK bridge starting: mqtt=%s:%d tak=%s:%d",
            self._cfg.mqtt.host, self._cfg.mqtt.port,
            self._cfg.tak.server.host, self._cfg.tak.server.port,
        )
        self._sink.start()
        heartbeat = asyncio.create_task(self._heartbeat(), name="heartbeat")
        try:
            await self._mqtt_loop()
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            await self._sink.stop()
            log.info("TAK bridge stopped")

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # MQTT ingestion
    # ------------------------------------------------------------------

    async def _mqtt_loop(self) -> None:
        mq = self._cfg.mqtt
        while not self._stop.is_set():
            try:
                async with aiomqtt.Client(
                    hostname=mq.host,
                    port=mq.port,
                    username=mq.username,
                    password=mq.password,
                    # Distinct client_id so we don't collide with the RPC
                    # service when both connect to the same broker.
                    identifier=self._cfg.tak.mqtt_client_id,
                ) as client:
                    await client.subscribe(NODE_LOCATION_FILTER, qos=mq.qos)
                    await client.subscribe(NODE_STATE_FILTER, qos=mq.qos)
                    await client.subscribe(BASE_LOCATION_TOPIC, qos=mq.qos)
                    log.info("MQTT connected and subscribed")
                    async for msg in client.messages:
                        if self._stop.is_set():
                            break
                        await self._handle_mqtt_message(msg)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("MQTT loop error: %s; reconnecting in 5s", e)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5.0)
                    return
                except asyncio.TimeoutError:
                    pass

    async def _handle_mqtt_message(self, msg: aiomqtt.Message) -> None:
        topic = str(msg.topic)
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.debug("Non-JSON payload on %s; skipping", topic)
            return
        if not isinstance(data, dict):
            return

        node_prefix_slash = f"{topics.NODE_PREFIX}/"
        if topic == BASE_LOCATION_TOPIC:
            self._merge_state(_BASE_KEY, data, kind="base")
        elif topic.startswith(node_prefix_slash) and topic.endswith("/location"):
            node_id = _extract_node_id(topic, "/location")
            if node_id:
                self._merge_state(node_id, data, kind="node")
        elif topic.startswith(node_prefix_slash) and topic.endswith("/state"):
            node_id = _extract_node_id(topic, "/state")
            if node_id:
                self._merge_state(node_id, data, kind="node")
        else:
            return

        # Publish a fresh CoT immediately on change.
        await self._publish_one(self._key_for(topic, data))

    def _key_for(self, topic: str, data: dict) -> str:
        if topic == BASE_LOCATION_TOPIC:
            return _BASE_KEY
        # Node id is in the topic, not always in the payload. Pull from
        # whichever is reliable.
        node_prefix_slash = f"{topics.NODE_PREFIX}/"
        if topic.startswith(node_prefix_slash):
            return topic.split("/")[2]
        return data.get("id", "")

    def _merge_state(self, key: str, data: dict, *, kind: str) -> None:
        cur = self._state.setdefault(key, {"_kind": kind})
        cur.update({k: v for k, v in data.items() if v is not None})
        cur["_updated"] = time.time()

    # ------------------------------------------------------------------
    # CoT emission
    # ------------------------------------------------------------------

    async def _heartbeat(self) -> None:
        try:
            while not self._stop.is_set():
                await asyncio.sleep(self._cfg.tak.publish_interval_s)
                for key in list(self._state.keys()):
                    await self._publish_one(key)
        except asyncio.CancelledError:
            return

    async def _publish_one(self, key: str) -> None:
        record = self._state.get(key)
        if not record:
            return
        lat = record.get("lat")
        lon = record.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            return  # no usable position yet

        is_base = key == _BASE_KEY
        cot_type = (
            self._cfg.tak.base_cot_type if is_base
            else self._cfg.tak.field_node_cot_type
        )
        node_id_for_uid = "base" if is_base else key
        uid = f"meshcore.{node_id_for_uid}"
        callsign = (
            "MC-base" if is_base
            else self._cfg.tak.callsign_template.format(id=key[:6])
        )

        # Use the last-known position timestamp so CoT staleness reflects
        # reality rather than wall clock. Fall back to wall clock if
        # there's no embedded ts.
        ts_field = record.get("ts")
        if isinstance(ts_field, (int, float)):
            time_dt = datetime.fromtimestamp(ts_field, tz=timezone.utc)
        else:
            time_dt = datetime.now(timezone.utc)

        # Remarks: stuff useful but non-essential context here. ATAK
        # surfaces this in the marker detail popup.
        remarks_parts = []
        if not is_base:
            age = record.get("last_seen_age_s")
            if isinstance(age, (int, float)):
                remarks_parts.append(f"last_seen_age={int(age)}s")
            bat = record.get("bat_pct")
            if isinstance(bat, (int, float)):
                remarks_parts.append(f"battery={int(bat)}%")
            rssi = record.get("rssi")
            if isinstance(rssi, (int, float)):
                remarks_parts.append(f"rssi={int(rssi)}")
            snr = record.get("snr")
            if isinstance(snr, (int, float)):
                remarks_parts.append(f"snr={float(snr):.1f}")

        cot_xml = build_cot(
            uid=uid,
            cot_type=cot_type,
            lat=float(lat),
            lon=float(lon),
            time_dt=time_dt,
            stale_after_s=self._cfg.tak.stale_after_s,
            callsign=callsign,
            alt_m=_opt_float(record.get("alt")),
            speed_mps=_opt_float(record.get("spd")),
            course_deg=_opt_float(record.get("hdg")),
            accuracy_m=_opt_float(record.get("acc")),
            remarks="; ".join(remarks_parts) if remarks_parts else None,
        )
        await self._sink.send(cot_xml)


def _extract_node_id(topic: str, suffix: str) -> Optional[str]:
    """Extract `<id>` from `<NODE_PREFIX>/<id>/<suffix>` topics."""
    if not topic.endswith(suffix):
        return None
    head = topic[: -len(suffix)]  # <NODE_PREFIX>/<id>
    expected_prefix = f"{topics.NODE_PREFIX}/"
    if not head.startswith(expected_prefix):
        return None
    node_id = head[len(expected_prefix):]
    if not node_id or "/" in node_id:
        return None
    return node_id


def _opt_float(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) else None
