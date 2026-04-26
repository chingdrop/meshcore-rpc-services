"""Per-node state aggregator.

Single owner of:
  * SQLite tables: node_locations, node_battery, base_state
  * Retained MQTT topics: mc/node/<id>/{location,battery,state}, mc/base/location

Inputs come from two sources:
  * RPC handlers (e.g. node.location.report calls apply_location)
  * Bus subscribers to gateway-native topics (e.g. meshcore/battery → apply_battery)

Outputs are:
  * SQLite writes (durable, queryable)
  * MQTT retained publishes (immediate, observable by other consumers)

Each apply_* method does the DB write THEN the MQTT publish. If the publish
fails (broker hiccup), the DB still has the truth and a future republish
loop can resync. This module deliberately has no MQTT awareness beyond a
publish callback handed in at construction.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from meshcore_rpc_services.mqtt import topics
from meshcore_rpc_services.persistence import Store

log = logging.getLogger(__name__)

# Publisher signature: (topic, payload_bytes, retained) -> awaitable
Publisher = Callable[[str, bytes, bool], Awaitable[None]]

# A node is "online" if seen within this many seconds.
ONLINE_THRESHOLD_S = 300


@dataclass(frozen=True)
class LocationFix:
    lat: float
    lon: float
    ts: float
    alt: Optional[float] = None
    acc: Optional[float] = None
    fix: Optional[int] = None
    spd: Optional[float] = None
    hdg: Optional[float] = None


class StateAggregator:
    def __init__(self, store: Store, publish: Publisher) -> None:
        self._store = store
        self._publish = publish
        # Last-known radio metadata per node. Memory-only — these are
        # ephemeral and don't survive a restart, which is fine: a fresh
        # service has no recent radio reception to report on.
        self._last_radio: dict[str, dict[str, Any]] = {}

    # -----------------------------------------------------------------
    # Inbound: apply_*  (called by handlers + bus subscribers)
    # -----------------------------------------------------------------

    async def apply_seen(
        self, node_id: str, ts: float,
        *, rssi: Optional[int] = None, snr: Optional[float] = None,
    ) -> None:
        await self._store.mark_node_seen(node_id, ts)
        if rssi is not None or snr is not None:
            self._last_radio[node_id] = {
                "rssi": rssi, "snr": snr, "ts": ts,
            }
        await self._republish_state(node_id)

    async def apply_location(
        self, node_id: str, fix: LocationFix,
        *, source: str,
        rssi: Optional[int] = None, snr: Optional[float] = None,
    ) -> None:
        await self._store.upsert_node_location(
            node_id=node_id, fix=fix, source=source, rssi=rssi, snr=snr,
        )
        await self._store.mark_node_seen(node_id, fix.ts)
        if rssi is not None or snr is not None:
            self._last_radio[node_id] = {
                "rssi": rssi, "snr": snr, "ts": fix.ts,
            }

        body: dict[str, Any] = {
            "id": node_id,
            "lat": fix.lat, "lon": fix.lon,
            "alt": fix.alt, "acc": fix.acc, "fix": fix.fix,
            "spd": fix.spd, "hdg": fix.hdg,
            "ts": fix.ts,
            "source": source,
            "rssi": rssi, "snr": snr,
        }
        await self._publish(
            topics.node_location_topic(node_id),
            _compact_json(body), True,
        )
        await self._republish_state(node_id)

    async def apply_battery(
        self, node_id: str, ts: float,
        *, pct: Optional[int] = None, voltage: Optional[float] = None,
        source: str = "telemetry",
    ) -> None:
        await self._store.upsert_node_battery(
            node_id=node_id, ts=ts, pct=pct, voltage=voltage, source=source,
        )
        await self._store.mark_node_seen(node_id, ts)

        body: dict[str, Any] = {
            "id": node_id, "pct": pct, "v": voltage,
            "ts": ts, "source": source,
        }
        await self._publish(
            topics.node_battery_topic(node_id),
            _compact_json(body), True,
        )
        await self._republish_state(node_id)

    async def apply_base_location(self, fix: LocationFix, *, source: str) -> None:
        body: dict[str, Any] = {
            "lat": fix.lat, "lon": fix.lon, "alt": fix.alt,
            "acc": fix.acc, "fix": fix.fix,
            "ts": fix.ts, "source": source,
        }
        await self._store.upsert_base_state("location", body)
        await self._publish(
            topics.BASE_LOCATION, _compact_json(body), True,
        )

    # -----------------------------------------------------------------
    # Reads (called by handlers)
    # -----------------------------------------------------------------

    async def get_node_location(self, node_id: str) -> Optional[dict]:
        return await self._store.get_node_location(node_id)

    async def get_node_battery(self, node_id: str) -> Optional[dict]:
        return await self._store.get_node_battery(node_id)

    async def get_node_state(self, node_id: str) -> Optional[dict[str, Any]]:
        last_seen = await self._store.get_last_seen(node_id)
        if last_seen is None:
            return None
        loc = await self._store.get_node_location(node_id)
        bat = await self._store.get_node_battery(node_id)
        radio = self._last_radio.get(node_id) or {}
        now = time.time()
        return {
            "id": node_id,
            "last_seen": last_seen,
            "last_seen_age_s": max(0, int(now - last_seen)),
            "online": (now - last_seen) < ONLINE_THRESHOLD_S,
            "loc_ts": loc.get("ts") if loc else None,
            "bat_pct": bat.get("pct") if bat else None,
            # Last-known signal quality from the most recent reception.
            # Memory-only; resets on service restart.
            "rssi": radio.get("rssi"),
            "snr": radio.get("snr"),
        }

    async def get_base_location(self) -> Optional[dict]:
        return await self._store.get_base_state("location")

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------

    async def _republish_state(self, node_id: str) -> None:
        st = await self.get_node_state(node_id)
        if st is None:
            return
        await self._publish(
            topics.node_state_topic(node_id), _compact_json(st), True,
        )


def _compact_json(d: dict) -> bytes:
    """Serialize to JSON, dropping None values to keep retained payloads small."""
    clean = {k: v for k, v in d.items() if v is not None}
    return json.dumps(clean, separators=(",", ":")).encode("utf-8")