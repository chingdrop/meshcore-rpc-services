"""Request router: maps request.type → Handler."""

from __future__ import annotations

from typing import Iterable, Optional

from meshcore_app.handlers.base import Handler


class Router:
    def __init__(self, handlers: Optional[Iterable[Handler]] = None) -> None:
        self._handlers: dict[str, Handler] = {}
        if handlers:
            for h in handlers:
                self.register(h)

    def register(self, handler: Handler) -> None:
        if handler.type in self._handlers:
            raise ValueError(f"Duplicate handler for type: {handler.type}")
        self._handlers[handler.type] = handler

    def resolve(self, request_type: str) -> Optional[Handler]:
        return self._handlers.get(request_type)

    def types(self) -> list[str]:
        return sorted(self._handlers.keys())
