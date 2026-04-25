"""`node.last_seen` handler.

Returns the most recent time the app layer heard from a given node.

Contract:
    request.type == "node.last_seen"
    request.args["node"]: the node id to look up. If missing or empty,
                          defaults to the requester's own id.

Response body:
    {"node": "<id>", "ts": <unix> | null, "age_s": <int> | null}
"""

from __future__ import annotations

import time

from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.schemas import Request, Response


class NodeLastSeenHandler:
    type = "node.last_seen"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        target = request.args.get("node")
        if not isinstance(target, str) or not target:
            target = request.from_

        ts = await ctx.store.get_last_seen(target)
        if ts is None:
            body = {"node": target, "ts": None, "age_s": None}
        else:
            body = {
                "node": target,
                "ts": ts,
                "age_s": max(0, int(time.time() - ts)),
            }
        return Response.ok(request, body)


handler: Handler = NodeLastSeenHandler()
