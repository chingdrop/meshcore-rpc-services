"""`gateway.status` handler.

Reports last-known gateway status + health (from the bus-cached snapshot)
plus a compact summary of request counts from the store.

Response body is kept terse for LoRa.
"""

from __future__ import annotations

from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.lifecycle import COMPLETED_ERROR, COMPLETED_OK, TIMEOUT
from meshcore_rpc_services.schemas import Request, Response


class GatewayStatusHandler:
    type = "gateway.status"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        snap = await ctx.gateway_snapshot()
        counts = await ctx.store.counts()

        body = {
            "gw": snap.get("status") or "unknown",
            "hb": snap.get("health") or "unknown",
            "pending": counts.get("pending", 0),
            "ok": counts.get(COMPLETED_OK, 0),
            "err": counts.get(COMPLETED_ERROR, 0),
            "to": counts.get(TIMEOUT, 0),
        }
        return Response.ok(request, body)


handler: Handler = GatewayStatusHandler()
