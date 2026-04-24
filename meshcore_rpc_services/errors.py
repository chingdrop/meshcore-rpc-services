"""Error code constants used in RPC error responses."""

from __future__ import annotations

# Validation / routing
BAD_REQUEST = "bad_request"
UNKNOWN_TYPE = "unknown_type"
DUPLICATE = "duplicate"

# Execution
TIMEOUT = "timeout"
INTERNAL = "internal"


class RpcError(Exception):
    """Raised by handlers to signal a controlled error response."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
