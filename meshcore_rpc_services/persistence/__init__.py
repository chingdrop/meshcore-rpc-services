"""SQLite persistence.

Public surface:

* :class:`Store` — the async persistence API used by core + handlers.
* Lifecycle state constants (re-exported from
  :mod:`meshcore_rpc_services.lifecycle`).
"""

from __future__ import annotations

from meshcore_rpc_services.lifecycle import (
    COMPLETED_ERROR,
    COMPLETED_OK,
    HANDLER_STARTED,
    RECEIVED,
    REJECTED,
    RESPONSE_PUBLISHED,
    TIMEOUT,
    VALIDATED,
)
from meshcore_rpc_services.persistence.sqlite import Store

__all__ = [
    "Store",
    "RECEIVED",
    "VALIDATED",
    "REJECTED",
    "HANDLER_STARTED",
    "RESPONSE_PUBLISHED",
    "TIMEOUT",
    "COMPLETED_OK",
    "COMPLETED_ERROR",
]
