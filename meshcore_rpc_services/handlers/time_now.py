"""`time.now` handler.

Returns the current wall-clock time from the app-layer host.

Contract:
    request.type == "time.now"
    request.args: ignored.

Response body:
    {"ts": <unix_epoch_seconds_float>, "iso": "<RFC3339 UTC>"}

Both representations included because the float is cheap for machines
and the iso string is cheap for humans reading logs. Together they are
~40 bytes — well inside the LoRa budget.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.schemas import Request, Response


class TimeNowHandler:
    type = "time.now"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        now = time.time()
        iso = (
            datetime.fromtimestamp(now, tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        return Response.ok(request, {"ts": now, "iso": iso})


handler: Handler = TimeNowHandler()
