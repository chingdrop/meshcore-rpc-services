"""`node.status` handler.

Query the aggregated status for a node: online/offline, last-seen age,
and battery if available.

Contract:
    request.type == "node.status"
    request.args["node"]: target node id; defaults to requester if absent

Response body: {"id", "online", "last_seen_age_s", "bat_pct"?}

Errors:
    "unavailable" — node has never been seen
"""
from __future__ import annotations

from meshcore_rpc_services.errors import UNAVAILABLE, RpcError
from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.schemas import Request, Response


class NodeStatusHandler:
    type = "node.status"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        target = request.args.get("node")
        if not isinstance(target, str) or not target:
            target = request.from_

        st = await ctx.state.get_node_state(target)
        if st is None:
            raise RpcError(UNAVAILABLE, f"No state for {target}")

        body = {
            "id": st["id"],
            "online": st["online"],
            "last_seen_age_s": st["last_seen_age_s"],
        }
        if st.get("bat_pct") is not None:
            body["bat_pct"] = st["bat_pct"]
        return Response.ok(request, body)


handler: Handler = NodeStatusHandler()