"""Handler protocol and shared context.

Handlers receive a :class:`HandlerContext` with:

* ``store``: the :class:`Store` (full persistence surface)
* ``gateway_snapshot``: a callable returning the cached retained gateway
  status/health as a plain dict. Using a callable rather than passing the
  bus in keeps handlers transport-agnostic, and using a plain dict rather
  than a wrapper type keeps the code honest about what's actually there.

No Protocol types. Tests pass in real :class:`Store` instances pointed at
``tmp_path``; that's the interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from meshcore_rpc_services.persistence import Store
from meshcore_rpc_services.schemas import Request, Response


@dataclass
class HandlerContext:
    store: Store
    # Zero-arg async callable returning {"status": str|None, "health": str|None}
    gateway_snapshot: Callable[[], Awaitable[dict[str, Any]]]


class Handler(Protocol):
    """Structural type. Any class with a matching ``type`` + ``handle`` works."""

    type: str

    async def handle(
        self, request: Request, ctx: HandlerContext
    ) -> Response: ...
