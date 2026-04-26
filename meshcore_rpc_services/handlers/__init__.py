"""Handler registry. Adding a handler is 3 lines: import + append here."""

from __future__ import annotations

from meshcore_rpc_services.handlers.base import Handler
from meshcore_rpc_services.handlers.base_location import handler as base_location_handler
from meshcore_rpc_services.handlers.echo import handler as echo_handler
from meshcore_rpc_services.handlers.gateway_status import (
    handler as gateway_status_handler,
)
from meshcore_rpc_services.handlers.node_last_seen import (
    handler as node_last_seen_handler,
)
from meshcore_rpc_services.handlers.node_location import handler as node_location_handler
from meshcore_rpc_services.handlers.node_location_report import (
    handler as node_location_report_handler,
)
from meshcore_rpc_services.handlers.node_status import handler as node_status_handler
from meshcore_rpc_services.handlers.ping import handler as ping_handler
from meshcore_rpc_services.handlers.return_to_base import handler as return_to_base_handler
from meshcore_rpc_services.handlers.time_now import handler as time_now_handler

DEFAULT_HANDLERS: list[Handler] = [
    ping_handler,
    echo_handler,
    time_now_handler,
    gateway_status_handler,
    node_last_seen_handler,
    node_location_report_handler,
    base_location_handler,
    node_location_handler,
    node_status_handler,
    return_to_base_handler,
]
