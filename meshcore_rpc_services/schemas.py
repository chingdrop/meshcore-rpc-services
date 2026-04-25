"""RPC request/response schemas. Pure data, no I/O."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class Request(BaseModel):
    """Inbound RPC request.

    The contract (locked):
        {
          "v": 1,
          "id": "abc123",
          "type": "ping",
          "from": "meshcore_node_id",
          "ttl": 30,
          "args": {}
        }
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    v: int = Field(default=SCHEMA_VERSION)
    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)
    # `from` is a Python keyword, so alias it.
    from_: str = Field(alias="from", min_length=1, max_length=128)
    ttl: Optional[int] = Field(default=None, ge=1)
    args: Dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class Response(BaseModel):
    """Outbound RPC response.

    A Response carries either `body` (on ok) or `error` (on error), not both.
    """

    model_config = ConfigDict(extra="forbid")

    v: int = SCHEMA_VERSION
    id: str
    type: str
    to: str
    status: Literal["ok", "error"]
    body: Optional[Dict[str, Any]] = None
    error: Optional[ErrorBody] = None

    @classmethod
    def ok(cls, request: Request, body: Dict[str, Any]) -> "Response":
        return cls(
            id=request.id,
            type=request.type,
            to=request.from_,
            status="ok",
            body=body,
        )

    @classmethod
    def make_error(
            cls,
            *,
            request_id: str,
            request_type: str,
            to: str,
            code: str,
            message: str,
    ) -> "Response":
        return cls(
            id=request_id,
            type=request_type,
            to=to,
            status="error",
            error=ErrorBody(code=code, message=message),
        )

    def to_json(self) -> str:
        # exclude_none keeps payloads terse (LoRa budget).
        return self.model_dump_json(exclude_none=True)
