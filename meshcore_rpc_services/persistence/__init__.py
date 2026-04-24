"""Persistence package.

Public surface:

* :class:`SqliteRequestRepository` — the concrete backend shipped in v1.
* Lifecycle state constants, re-exported from
  :mod:`meshcore_rpc_services.lifecycle`.

A future ``DjangoRequestRepository`` will live here as a sibling of
:mod:`sqlite` and will implement the same port.
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
from meshcore_rpc_services.persistence.sqlite import (
    SqliteRequestRepository,
    SqliteStore,
)

# Legacy alias for callers that used "error" as the generic final-error value.
ERROR = COMPLETED_ERROR

__all__ = [
    "SqliteRequestRepository",
    "SqliteStore",
    "RECEIVED",
    "VALIDATED",
    "REJECTED",
    "HANDLER_STARTED",
    "RESPONSE_PUBLISHED",
    "TIMEOUT",
    "COMPLETED_OK",
    "COMPLETED_ERROR",
    "ERROR",
]
