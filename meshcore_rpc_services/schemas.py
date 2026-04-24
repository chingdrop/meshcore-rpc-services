"""RPC request/response schemas. Pure data, no I/O."""

from __future__ import annotations

from typing import Any, Callable, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    model_config = ConfigDict(
        extra="forbid", validate_by_alias=True, validate_by_name=True
    )

    v: int = Field(default=SCHEMA_VERSION)
    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)
    # `from` is a Python keyword, so alias it.
    from_: str = Field(alias="from", min_length=1, max_length=128)
    ttl: Optional[int] = Field(default=None, ge=1)
    args: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coalesce_from_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and "from_" in data:
            data = dict(data)
            data["from"] = data.pop("from_")
        return data


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

    def to_json(self) -> str:
        # exclude_none keeps payloads terse (LoRa budget).
        return self.model_dump_json(exclude_none=True)


def _build_error_response(
    cls: type[Response],
    *,
    request_id: str,
    request_type: str,
    to: str,
    code: str,
    message: str,
) -> Response:
    return cls(
        id=request_id,
        type=request_type,
        to=to,
        status="error",
        error=ErrorBody(code=code, message=message),
    )


class _ErrorAccessor:
    """Expose ``Response.error`` as both an instance field and class helper."""

    def __init__(self, builder: Callable[..., Response]) -> None:
        self._builder = builder

    def __get__(
        self, obj: Optional[Response], owner: type[Response]
    ) -> Any:
        if obj is None:
            def _bound_builder(**kwargs: Any) -> Response:
                return self._builder(owner, **kwargs)

            return _bound_builder
        return obj.__dict__.get("error")


Response.error = _ErrorAccessor(_build_error_response)
