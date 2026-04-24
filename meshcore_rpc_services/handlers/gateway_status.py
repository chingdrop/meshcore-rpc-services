"""`gateway.status` handler.

Reports last-known gateway status + health (via
:class:`GatewaySnapshotProvider`) plus a compact summary of request counts
from the repository. Both are ports — this handler works unchanged whether
the substrate is MQTT + SQLite (today) or Celery + Django ORM (later).

Response body is kept terse for LoRa.
"""

from __future__ import annotations

from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.lifecycle import COMPLETED_ERROR, COMPLETED_OK, TIMEOUT
from meshcore_rpc_services.schemas import Request, Response


class GatewayStatusHandler:
    type = "gateway.status"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        snap = await ctx.snapshot.get_snapshot()
        counts = await ctx.repo.counts()

        body = {
            # Gateway self-reported:
            "gw": snap.get("status") or "unknown",
            "hb": snap.get("health") or "unknown",
            # App-layer view:
            "pending": counts.get("pending", 0),
            "ok": counts.get(COMPLETED_OK, 0),
            "err": counts.get(COMPLETED_ERROR, 0),
            "to": counts.get(TIMEOUT, 0),
        }
        return Response.ok(request, body)


handler: Handler = GatewayStatusHandler()
