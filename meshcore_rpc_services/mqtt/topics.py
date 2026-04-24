"""Central definition of every MQTT topic this repo speaks.

Two groups live here:

1. **Internal RPC contract** — the topics this service speaks publicly. These
   are the long-term, locked topics. Everything in the app layer references
   these constants; nobody hardcodes strings.

2. **Gateway-native topics** — the topics the gateway emits/consumes today.
   We subscribe to these when we want raw visibility into gateway state. The
   transport adapter is responsible for translating between gateway-native
   messages and the internal RPC contract.

If the gateway's topic shape ever changes, only :mod:`transport.adapter` and
this file need to move. The rest of the codebase is insulated.
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# Internal RPC contract (long-term, locked)
# -----------------------------------------------------------------------------

RPC_REQUEST = "meshcore/rpc/request"
RPC_RESPONSE_PREFIX = "meshcore/rpc/response"

GATEWAY_STATUS = "meshcore/gateway/status"
GATEWAY_HEALTH = "meshcore/gateway/health"


def rpc_response_topic(node_id: str) -> str:
    """Build the per-node response topic. Do not concatenate by hand."""
    if not node_id:
        raise ValueError("node_id must be non-empty")
    return f"{RPC_RESPONSE_PREFIX}/{node_id}"


# -----------------------------------------------------------------------------
# Gateway-native topics (may drift; kept in one place)
# -----------------------------------------------------------------------------
#
# The meshcore-mqtt gateway's default topic prefix is "meshcore". The adapter
# configures the full strings at startup based on the configured prefix so
# these constants are just suffixes/templates.

GATEWAY_NATIVE_STATUS_SUFFIX = "status"
GATEWAY_NATIVE_DIRECT_MSG_FILTER_SUFFIX = "message/direct/+"
GATEWAY_NATIVE_SEND_MSG_SUFFIX = "command/send_msg"


def gateway_native_status(prefix: str) -> str:
    return f"{prefix}/{GATEWAY_NATIVE_STATUS_SUFFIX}"


def gateway_native_direct_msg_filter(prefix: str) -> str:
    return f"{prefix}/{GATEWAY_NATIVE_DIRECT_MSG_FILTER_SUFFIX}"


def gateway_native_send_msg(prefix: str) -> str:
    return f"{prefix}/{GATEWAY_NATIVE_SEND_MSG_SUFFIX}"


__all__ = [
    "RPC_REQUEST",
    "RPC_RESPONSE_PREFIX",
    "GATEWAY_STATUS",
    "GATEWAY_HEALTH",
    "rpc_response_topic",
    "gateway_native_status",
    "gateway_native_direct_msg_filter",
    "gateway_native_send_msg",
]
