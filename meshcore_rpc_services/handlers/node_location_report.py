"""`node.location.report` handler.

Field nodes report their current GPS fix. The aggregator writes to SQLite
and publishes retained state. Response is a tiny ack so the field node
knows it landed.

Contract:
    request.type == "node.location.report"
    request.args:
        lat  (float, required)  — WGS84 latitude  [-90, 90]
        lon  (float, required)  — WGS84 longitude [-180, 180]
        ts   (float, optional)  — Unix epoch; server time used if absent
        alt, acc, spd, hdg      — optional floats
        fix  (int, optional)    — GNSS fix type

Response body: {"ack": true, "ts": <server_ts>}
"""
from __future__ import annotations

import time

from meshcore_rpc_services.errors import BAD_REQUEST, RpcError
from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.schemas import Request, Response
from meshcore_rpc_services.state import LocationFix


class NodeLocationReportHandler:
    type = "node.location.report"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        a = request.args
        lat, lon = a.get("lat"), a.get("lon")
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
            raise RpcError(BAD_REQUEST, "lat and lon are required floats")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise RpcError(BAD_REQUEST, "lat/lon out of range")

        fix = LocationFix(
            lat=float(lat),
            lon=float(lon),
            ts=float(a.get("ts") or time.time()),
            alt=_opt_float(a.get("alt")),
            acc=_opt_float(a.get("acc")),
            fix=_opt_int(a.get("fix")),
            spd=_opt_float(a.get("spd")),
            hdg=_opt_float(a.get("hdg")),
        )
        await ctx.state.apply_location(request.from_, fix, source="report")
        return Response.ok(request, {"ack": True, "ts": fix.ts})


def _opt_float(v):
    return float(v) if isinstance(v, (int, float)) else None


def _opt_int(v):
    return int(v) if isinstance(v, int) else None


handler: Handler = NodeLocationReportHandler()
