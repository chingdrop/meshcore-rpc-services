"""`ping` handler.

Contract:
    request.type == "ping"
    request.args may contain an optional "echo" string (bounded length).
    Response body: {"message": "pong", "echo": <echo?>} — terse on purpose.
"""

from __future__ import annotations

from meshcore_app.handlers.base import Handler, HandlerContext
from meshcore_app.schemas import Request, Response

_MAX_ECHO_LEN = 64


class PingHandler:
    type = "ping"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        body: dict = {"message": "pong"}
        echo = request.args.get("echo")
        if isinstance(echo, str) and echo:
            body["echo"] = echo[:_MAX_ECHO_LEN]
        return Response.ok(request, body)


# Module-level instance for easy registration.
handler: Handler = PingHandler()
