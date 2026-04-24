"""Handler registry. Keep this file short — adding a handler is 3 lines."""

from __future__ import annotations

from meshcore_app.handlers.base import Handler
from meshcore_app.handlers.gateway_status import handler as gateway_status_handler
from meshcore_app.handlers.ping import handler as ping_handler

DEFAULT_HANDLERS: list[Handler] = [
    ping_handler,
    gateway_status_handler,
]
