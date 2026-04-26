"""`return_to_base` handler.

Computes bearing and distance from the caller (or an explicit lat/lon) to
the home base. The headline application feature.

Contract:
    request.type == "return_to_base"
    request.args (all optional):
        lat, lon  — caller's current position; if absent, the aggregator's
                    last known location for the caller is used

Response body:
    {
        "bearing":  <int degrees 0-359>,
        "dist_m":   <int metres>,
        "base":     {"lat", "lon", "age_s"},
        "from":     {"lat", "lon", "age_s"},
    }

Errors:
    "unavailable" — caller has no known location, or base has never had a fix
    "stale"       — caller's last fix or base fix is older than 10 minutes
"""
from __future__ import annotations

import time

from meshcore_rpc_services.errors import STALE, UNAVAILABLE, RpcError
from meshcore_rpc_services.geo import haversine_m, initial_bearing_deg
from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.schemas import Request, Response

_MAX_AGE_S = 600  # 10 minutes


class ReturnToBaseHandler:
    type = "return_to_base"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        a = request.args

        # Caller location: explicit args win, else look up aggregator state.
        if isinstance(a.get("lat"), (int, float)) and isinstance(a.get("lon"), (int, float)):
            from_lat, from_lon, from_age_s = float(a["lat"]), float(a["lon"]), 0
        else:
            loc = await ctx.state.get_node_location(request.from_)
            if loc is None:
                raise RpcError(UNAVAILABLE, "Caller has no reported location")
            from_age_s = max(0, int(time.time() - loc["ts"]))
            if from_age_s > _MAX_AGE_S:
                raise RpcError(STALE, f"Caller's last fix is {from_age_s}s old")
            from_lat, from_lon = loc["lat"], loc["lon"]

        # Base location.
        base = await ctx.state.get_base_location()
        if base is None:
            raise RpcError(UNAVAILABLE, "Base has no fix")
        base_age_s = max(0, int(time.time() - base["ts"]))
        if base_age_s > _MAX_AGE_S:
            raise RpcError(STALE, f"Base's last fix is {base_age_s}s old")

        bearing = initial_bearing_deg(from_lat, from_lon, base["lat"], base["lon"])
        dist = haversine_m(from_lat, from_lon, base["lat"], base["lon"])

        return Response.ok(request, {
            "bearing": int(round(bearing)),
            "dist_m": int(round(dist)),
            "base": {"lat": base["lat"], "lon": base["lon"], "age_s": base_age_s},
            "from": {"lat": from_lat, "lon": from_lon, "age_s": from_age_s},
        })


handler: Handler = ReturnToBaseHandler()
