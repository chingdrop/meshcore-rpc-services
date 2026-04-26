"""`node.location` handler.

Query the last known location for a node. Useful for a Home Node asking
about a field node it hasn't heard from directly.

Contract:
    request.type == "node.location"
    request.args["node"]: target node id; defaults to requester if absent

Response body: {"node", "lat", "lon", "ts", "age_s"}

Errors:
    "unavailable" — node has never reported a location
"""
from __future__ import annotations

import time

from meshcore_rpc_services.errors import UNAVAILABLE, RpcError
from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.schemas import Request, Response


class NodeLocationHandler:
    type = "node.location"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        target = request.args.get("node")
        if not isinstance(target, str) or not target:
            target = request.from_

        loc = await ctx.state.get_node_location(target)
        if loc is None:
            raise RpcError(UNAVAILABLE, f"No location for {target}")

        age_s = max(0, int(time.time() - loc["ts"]))
        return Response.ok(request, {
            "node": target,
            "lat": loc["lat"],
            "lon": loc["lon"],
            "ts": loc["ts"],
            "age_s": age_s,
        })


handler: Handler = NodeLocationHandler()