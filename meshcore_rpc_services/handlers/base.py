"""Handler protocol and shared context.

Handlers depend on *ports* (see :mod:`meshcore_rpc_services.ports`), never
on the concrete MQTT bus or SQLite store. That is what lets the same
handler run under the MQTT service today and under a Celery worker
tomorrow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from meshcore_rpc_services.ports import (
        GatewaySnapshotProvider,
        NodeRegistry,
        RequestRepository,
    )
    from meshcore_rpc_services.schemas import Request, Response


@dataclass
class HandlerContext:
    """Dependencies made available to handlers.

    Intentionally narrow. If a handler needs something new, add a port and
    add it here — do not give handlers a bus, a DB connection, or a
    Celery app.
    """

    snapshot: "GatewaySnapshotProvider"
    repo: "RequestRepository"
    nodes: "NodeRegistry"


class Handler(Protocol):
    type: str  # request type this handler serves

    async def handle(
        self, request: "Request", ctx: HandlerContext
    ) -> "Response": ...
