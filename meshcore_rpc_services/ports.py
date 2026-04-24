"""Protocols that decouple the core request pipeline from its substrate.

Handlers and :mod:`core` depend only on the types in this file. The MQTT
transport, the SQLite repository, and any future Django/Celery backends
implement these protocols.

Migration notes
---------------
* Django later: replace :class:`RequestRepository` / :class:`NodeRegistry`
  with ORM-backed implementations; no other module changes.
* Celery later: replace :class:`ResponseEmitter` with one that writes to a
  result table / queue; pure :mod:`core` already works without MQTT.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol, Tuple, runtime_checkable

from meshcore_rpc_services.schemas import Request, Response


# ---------------------------------------------------------------------------
# Persistence port
# ---------------------------------------------------------------------------


@runtime_checkable
class RequestRepository(Protocol):
    """Persistence boundary for the request lifecycle.

    Deliberately narrow. Do not add query methods here that only the
    transport or a UI would need — those belong in a separate read-model
    port or, post-Django, on the Django queryset directly.
    """

    async def record_received(
        self, request: Request, ttl_s: int
    ) -> bool:
        """Record a newly-received request.

        Returns ``True`` if this is a fresh ``(from, id)`` and ``False`` if
        we've seen it before (duplicate). Callers use the boolean to decide
        whether to proceed or emit a ``duplicate`` error.
        """
        ...

    async def record_event(
        self, request_id: str, state: str, detail: Optional[str] = None
    ) -> None: ...

    async def record_completion(
        self,
        request_id: str,
        final_state: str,
        response: Optional[Response] = None,
        error_code: Optional[str] = None,
    ) -> None: ...

    async def counts(self) -> Mapping[str, int]: ...

    async def purge_before(self, cutoff_ts: float) -> int:
        """Delete requests + events completed before ``cutoff_ts``.

        Returns the number of request rows deleted.
        """
        ...


# ---------------------------------------------------------------------------
# Gateway state ports
# ---------------------------------------------------------------------------


@runtime_checkable
class GatewaySnapshotProvider(Protocol):
    """Read-only access to the last-known gateway status/health."""

    async def get_snapshot(self) -> Mapping[str, Any]: ...


@runtime_checkable
class GatewaySnapshotSink(Protocol):
    """Write side for gateway status/health snapshots.

    The MQTT bus calls this whenever the gateway publishes to the retained
    status/health topics. Today the SQLite repo persists snapshots for
    history; Django will take over later.
    """

    async def record_snapshot(
        self, *, status: Optional[str], health: Optional[str]
    ) -> None: ...


# ---------------------------------------------------------------------------
# Node registry port
# ---------------------------------------------------------------------------


@runtime_checkable
class NodeRegistry(Protocol):
    """Tracks the most recent time we heard from each node.

    The core pipeline bumps ``last_seen`` for every valid request it
    receives. The ``node.last_seen`` handler reads back from here.
    """

    async def mark_seen(self, node_id: str, ts: float) -> None: ...

    async def get_last_seen(self, node_id: str) -> Optional[float]:
        """Returns a unix timestamp, or ``None`` if we've never seen it."""
        ...


# ---------------------------------------------------------------------------
# Response emission port
# ---------------------------------------------------------------------------


@runtime_checkable
class ResponseEmitter(Protocol):
    """Egress for an RPC response."""

    async def emit(self, node_id: str, response: Response) -> None: ...
