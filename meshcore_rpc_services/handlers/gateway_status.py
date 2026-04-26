"""`gateway.status` handler.

Reports last-known gateway status + health (from the bus-cached snapshot)
plus a compact summary of request counts from the store.

Response body is kept terse for LoRa. `snap_age_s` is null when no retained
message has arrived yet (i.e. the gateway has never been seen since startup).
"""

from __future__ import annotations

import time
from typing import Optional

from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.lifecycle import COMPLETED_ERROR, COMPLETED_OK, TIMEOUT
from meshcore_rpc_services.schemas import Request, Response


class GatewayStatusHandler:
    type = "gateway.status"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        snap = await ctx.gateway_snapshot()
        counts = await ctx.store.counts()

        snapped_at: Optional[float] = snap.get("snapped_at")
        snap_age_s: Optional[int] = (
            max(0, int(time.time() - snapped_at)) if snapped_at is not None else None
        )

        body = {
            "state": snap.get("state") or "unknown",
            "detail": snap.get("detail"),
            "since": snap.get("since"),
            "snap_age_s": snap_age_s,
            "pending": counts.get("pending", 0),
            "ok": counts.get(COMPLETED_OK, 0),
            "err": counts.get(COMPLETED_ERROR, 0),
            "to": counts.get(TIMEOUT, 0),
        }
        return Response.ok(request, body)


handler: Handler = GatewayStatusHandler()
