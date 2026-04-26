import pytest

from meshcore_rpc_services.mqtt import topics


def test_rpc_response_topic_builds_correctly():
    assert topics.rpc_response_topic("node-1") == "mc/rpc/resp/node-1"


def test_rpc_response_topic_rejects_empty():
    with pytest.raises(ValueError):
        topics.rpc_response_topic("")


def test_service_contract_constants_are_stable():
    assert topics.RPC_REQUEST == "mc/rpc/req"
    assert topics.RPC_RESPONSE_PREFIX == "mc/rpc/resp"
    assert topics.GATEWAY_STATUS == "mc/gateway/status"
    assert topics.SVC_HEALTH == "mc/svc/health"
    assert topics.BASE_LOCATION == "mc/base/location"


def test_node_topic_builders():
    assert topics.node_location_topic("abc") == "mc/node/abc/location"
    assert topics.node_battery_topic("abc") == "mc/node/abc/battery"
    assert topics.node_state_topic("abc") == "mc/node/abc/state"


def test_gateway_native_helpers():
    assert topics.gateway_native_status("meshcore") == "meshcore/status"
    assert topics.gateway_native_direct_msg_filter("foo") == "foo/message/direct/+"
    assert topics.gateway_native_send_msg("bar") == "bar/command/send_msg"
