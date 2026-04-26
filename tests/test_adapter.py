import json

from meshcore_rpc_services.schemas import Response
from meshcore_rpc_services.transport.adapter import (
    inbound_to_request,
    response_to_outbound,
)


def _bytes(obj) -> bytes:
    return json.dumps(obj).encode()


def test_valid_request_round_trips():
    env = inbound_to_request(
        topic="mc/rpc/req",
        raw_payload=_bytes(
            {"v": 1, "id": "a", "type": "ping", "from": "n1", "ttl": 10}
        ),
    )
    assert env.request is not None
    assert env.error_response is None
    assert env.request.id == "a"
    assert env.request.from_ == "n1"


def test_bad_json_is_silently_dropped():
    env = inbound_to_request(
        topic="mc/rpc/req", raw_payload=b"not-json"
    )
    assert env.request is None
    assert env.error_response is None


def test_schema_error_with_addressable_sender_emits_bad_request():
    env = inbound_to_request(
        topic="mc/rpc/req",
        raw_payload=_bytes({"v": 1, "id": "x", "from": "n1"}),  # missing 'type'
    )
    assert env.request is None
    assert env.error_response is not None
    assert env.error_response.status == "error"
    assert env.error_response.error is not None
    assert env.error_response.error.code == "bad_request"
    assert env.error_response.to == "n1"


def test_schema_error_without_addressable_sender_is_dropped():
    env = inbound_to_request(
        topic="mc/rpc/req",
        raw_payload=_bytes({"v": 1}),  # no id, no from
    )
    assert env.request is None
    assert env.error_response is None


def test_non_object_payload_is_dropped():
    env = inbound_to_request(
        topic="mc/rpc/req",
        raw_payload=_bytes([1, 2, 3]),
    )
    assert env.request is None
    assert env.error_response is None


def test_response_to_outbound_encodes_utf8():
    resp = Response.ok(
        __import__("meshcore_rpc_services").schemas.Request.model_validate(
            {"v": 1, "id": "a", "type": "ping", "from": "n1"}
        ),
        {"message": "pong"},
    )
    node_id, payload = response_to_outbound(resp)
    assert node_id == "n1"
    parsed = json.loads(payload.decode())
    assert parsed["to"] == "n1"
    assert parsed["body"] == {"message": "pong"}
