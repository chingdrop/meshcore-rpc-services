"""Request lifecycle states.

A request moves through a sequence of recorded states. Each transition is
stored as a :class:`request_events` row; the final state is also copied onto
the :class:`requests` row for fast querying.

States are intentionally plain strings (not an Enum type on the boundary)
so that future Django models, external consumers, or operational tooling
can filter on them as-is.

Valid transition sketch:

    received → validated → handler_started → response_published → completed_ok
                                           ↘ timeout            → completed_error
                        ↘ rejected           → response_published → completed_error
    received → rejected  → response_published → completed_error

``rejected`` covers `bad_request`, `unknown_type`, and `duplicate` — any
pre-handler failure. `timeout` is specifically the TTL firing. Handler-raised
``RpcError`` or unexpected exceptions also end up as ``completed_error``.
"""

from __future__ import annotations

# Pre-handler stages
RECEIVED = "received"
VALIDATED = "validated"
REJECTED = "rejected"

# Handler stages
HANDLER_STARTED = "handler_started"
RESPONSE_PUBLISHED = "response_published"
TIMEOUT = "timeout"

# Terminal "final_state" values (stored on requests.final_state)
COMPLETED_OK = "completed_ok"
COMPLETED_ERROR = "completed_error"

# All event-log states. Useful for validation in tests and for building
# admin UI filter dropdowns later.
ALL_EVENT_STATES = frozenset(
    {
        RECEIVED,
        VALIDATED,
        REJECTED,
        HANDLER_STARTED,
        RESPONSE_PUBLISHED,
        TIMEOUT,
    }
)

# All terminal final_state values.
ALL_FINAL_STATES = frozenset({COMPLETED_OK, COMPLETED_ERROR})

__all__ = [
    "RECEIVED",
    "VALIDATED",
    "REJECTED",
    "HANDLER_STARTED",
    "RESPONSE_PUBLISHED",
    "TIMEOUT",
    "COMPLETED_OK",
    "COMPLETED_ERROR",
    "ALL_EVENT_STATES",
    "ALL_FINAL_STATES",
]
