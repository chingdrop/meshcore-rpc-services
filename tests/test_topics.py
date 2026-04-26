import pytest

from meshcore_rpc_services.mqtt import topics


def test_rpc_response_topic_builds_correctly():
    assert topics.rpc_response_topic("node-1") == "meshcore/rpc/response/node-1"


def test_rpc_response_topic_rejects_empty():
    with pytest.raises(ValueError):
        topics.rpc_response_topic("")


def test_internal_constants_are_stable():
    assert topics.RPC_REQUEST == "meshcore/rpc/request"
    assert topics.RPC_RESPONSE_PREFIX == "meshcore/rpc/response"
    assert topics.GATEWAY_STATUS == "meshcore/gateway/status"


def test_gateway_native_helpers():
    assert topics.gateway_native_status("meshcore") == "meshcore/status"
    assert topics.gateway_native_direct_msg_filter("foo") == "foo/message/direct/+"
    assert topics.gateway_native_send_msg("bar") == "bar/command/send_msg"
