"""Transport adapter.

Every inbound MQTT message goes through :func:`inbound_to_request` before it
reaches the core pipeline. Every outbound :class:`Response` goes through
:func:`response_to_outbound` on its way back to MQTT.

Today the gateway's own RPC adapter emits clean JSON on the internal RPC
topics, so the translation here is close to identity — decode bytes, pick
off the right topic, parse JSON, produce :class:`Request`. Explicit though:
when the gateway shape drifts (a change of topic prefix, a different
envelope, a future binary format), this is the **only** file that has to
change in the app layer.

Invariants upheld here:

* The only topic that reaches :func:`inbound_to_request` is a *request*
  topic. Gateway status/health cache updates go through the bus directly.
* Returns ``(request, error)``. Exactly one is non-``None``. If parsing or
  validation failed, ``error`` carries the structured error response and
  the caller can emit it as-is.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from pydantic import ValidationError

from meshcore_rpc_services import errors
from meshcore_rpc_services.schemas import Request, Response

log = logging.getLogger(__name__)


@dataclass
class InboundEnvelope:
    """Normalised shape the adapter emits for the core pipeline.

    Only two inputs today: the JSON-decoded payload plus the topic it came
    from (for routing diagnostics). If we later need the raw bytes, QoS, or
    retained flag, they go here — not into :class:`Request`.
    """

    request: Optional[Request]
    error_response: Optional[Response]
    source_topic: str


def inbound_to_request(
    *, topic: str, raw_payload: bytes | bytearray | memoryview | str
) -> InboundEnvelope:
    """Translate an inbound MQTT message into an :class:`InboundEnvelope`.

    Never raises. Parse and validation failures become an ``error_response``
    so the transport layer can publish them without thinking.
    """
    raw = _decode(raw_payload)

    # 1) JSON decode
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning(
            "adapter: dropping request on %s: invalid JSON (%s)", topic, e
        )
        # No addressable sender → nothing we can publish. Drop.
        return InboundEnvelope(
            request=None, error_response=None, source_topic=topic
        )

    # 2) Schema validate
    try:
        request = Request.model_validate(data)
    except ValidationError as e:
        err_resp = _try_build_bad_request_response(data, e)
        return InboundEnvelope(
            request=None, error_response=err_resp, source_topic=topic
        )

    return InboundEnvelope(
        request=request, error_response=None, source_topic=topic
    )


def response_to_outbound(response: Response) -> Tuple[str, bytes]:
    """Serialise a :class:`Response` for publication.

    Returns ``(node_id, payload_bytes)``. The caller picks the concrete
    response topic using ``topics.rpc_response_topic(node_id)``.
    """
    return response.to, response.to_json().encode("utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode(payload: bytes | bytearray | memoryview | str) -> str:
    if isinstance(payload, (bytes, bytearray, memoryview)):
        return bytes(payload).decode("utf-8", errors="replace")
    return str(payload)


def _try_build_bad_request_response(
    data: object, err: ValidationError
) -> Optional[Response]:
    """Emit a ``bad_request`` if we can find an addressable sender, else None."""
    if not isinstance(data, dict):
        log.warning("adapter: dropping malformed request (non-object payload)")
        return None

    req_id = data.get("id")
    req_type = data.get("type")
    to = data.get("from")
    if not req_id or not to:
        log.warning(
            "adapter: dropping malformed request (no id/from): %s",
            err.errors()[:3],
        )
        return None

    return Response.error(
        request_id=str(req_id),
        request_type=str(req_type or "unknown"),
        to=str(to),
        code=errors.BAD_REQUEST,
        message="Request payload failed validation",
    )
