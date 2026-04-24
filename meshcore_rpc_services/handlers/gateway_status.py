"""`gateway.status` handler.

Reports the last-known gateway status + health (cached by the MQTT bus from
the retained `meshcore/gateway/status` and `meshcore/gateway/health` topics)
plus a compact summary of request counts from persistence.

Response body is kept terse for LoRa.
"""

from __future__ import annotations

from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.schemas import Request, Response


class GatewayStatusHandler:
    type = "gateway.status"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        snap = await ctx.bus.get_gateway_snapshot()
        counts = await ctx.store.counts()

        body = {
            # Gateway self-reported:
            "gw": snap.get("status") or "unknown",
            "hb": snap.get("health") or "unknown",
            # App-layer view:
            "pending": counts.get("pending", 0),
            "ok": counts.get("ok", 0),
            "err": counts.get("error", 0),
            "to": counts.get("timeout", 0),
        }
        return Response.ok(request, body)


handler: Handler = GatewayStatusHandler()
