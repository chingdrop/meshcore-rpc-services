"""`echo` handler.

Contract:
    request.type == "echo"
    request.args["msg"]: string to echo back (bounded).

Response body: {"msg": <truncated-string>}.

Deliberately more useful than ping for testing: you can verify the roundtrip
preserves your payload.
"""

from __future__ import annotations

from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.schemas import Request, Response

_MAX_LEN = 180  # conservative ceiling for LoRa roundtrips


class EchoHandler:
    type = "echo"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        msg = request.args.get("msg")
        if not isinstance(msg, str):
            msg = ""
        return Response.ok(request, {"msg": msg[:_MAX_LEN]})


handler: Handler = EchoHandler()
