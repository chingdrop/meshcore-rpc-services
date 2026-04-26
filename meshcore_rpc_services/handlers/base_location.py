"""`base.location` handler.

Field nodes ask: where is the home base?

Contract:
    request.type == "base.location"
    request.args: ignored

Response body: {"lat", "lon", "ts", "age_s", "fix"}

Errors:
    "unavailable" — base has never had a fix
    "stale"       — base fix exists but is older than BASE_MAX_AGE_S
"""
from __future__ import annotations

import time

from meshcore_rpc_services.errors import STALE, UNAVAILABLE, RpcError
from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.schemas import Request, Response

BASE_MAX_AGE_S = 600  # 10 minutes


class BaseLocationHandler:
    type = "base.location"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        loc = await ctx.state.get_base_location()
        if loc is None:
            raise RpcError(UNAVAILABLE, "Base has no GPS fix yet")

        age_s = max(0, int(time.time() - loc.get("ts", 0)))
        if age_s > BASE_MAX_AGE_S:
            raise RpcError(STALE, f"Base fix is {age_s}s old")

        return Response.ok(request, {
            "lat": loc["lat"],
            "lon": loc["lon"],
            "ts": loc["ts"],
            "age_s": age_s,
            "fix": loc.get("fix"),
        })


handler: Handler = BaseLocationHandler()