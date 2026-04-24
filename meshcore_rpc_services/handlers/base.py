"""Handler protocol and shared context.

This module intentionally avoids importing transport/persistence modules at
runtime — handlers should be testable with stub contexts that satisfy the
structural types below, without pulling aiomqtt or sqlite into test scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from meshcore_rpc_services.mqtt_bus import MqttBus
    from meshcore_rpc_services.persistence import AsyncStore
    from meshcore_rpc_services.schemas import Request, Response


@dataclass
class HandlerContext:
    """Dependencies made available to handlers.

    Handlers should read from this rather than reaching into the service.
    Keep it narrow — if a handler needs something new, add it here explicitly.
    """

    bus: "MqttBus"
    store: "AsyncStore"


class Handler(Protocol):
    type: str  # request type this handler serves

    async def handle(self, request: "Request", ctx: HandlerContext) -> "Response": ...
