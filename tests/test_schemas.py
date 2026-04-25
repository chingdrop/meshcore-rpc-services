import json

import pytest
from pydantic import ValidationError

from meshcore_rpc_services.schemas import Request, Response


def test_request_parses_locked_contract():
    data = {
        "v": 1,
        "id": "abc123",
        "type": "ping",
        "from": "node-xyz",
        "ttl": 30,
        "args": {"echo": "hello"},
    }
    req = Request.model_validate(data)
    assert req.id == "abc123"
    assert req.type == "ping"
    assert req.from_ == "node-xyz"
    assert req.ttl == 30
    assert req.args == {"echo": "hello"}


def test_request_allows_missing_ttl_and_args():
    req = Request.model_validate({"v": 1, "id": "a", "type": "ping", "from": "n"})
    assert req.ttl is None
    assert req.args == {}


def test_request_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Request.model_validate(
            {"v": 1, "id": "a", "type": "ping", "from": "n", "surprise": 1}
        )


def test_response_ok_shape():
    req = Request.model_validate({"v": 1, "id": "a", "type": "ping", "from": "n"})
    resp = Response.ok(req, {"message": "pong"})
    j = json.loads(resp.to_json())
    assert j == {
        "v": 1,
        "id": "a",
        "type": "ping",
        "to": "n",
        "status": "ok",
        "body": {"message": "pong"},
    }
    # exclude_none: no "error" key leaked.
    assert "error" not in j


def test_response_error_shape():
    resp = Response.make_error(
        request_id="a", request_type="ping", to="n", code="timeout", message="gone"
    )
    j = json.loads(resp.to_json())
    assert j["status"] == "error"
    assert j["error"] == {"code": "timeout", "message": "gone"}
    assert "body" not in j
