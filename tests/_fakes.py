"""Shared in-memory port fakes for tests."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from meshcore_rpc_services.schemas import Request, Response


class FakeRepo:
    def __init__(self) -> None:
        # key: (node_id, request_id) -> data
        self._seen: Dict[Tuple[str, str], dict] = {}
        self.events: List[Tuple[str, str, Optional[str]]] = []
        self.completions: List[Tuple[str, str, Optional[str]]] = []
        self.purge_calls: List[float] = []

    async def record_received(self, request: Request, ttl_s: int) -> bool:
        key = (request.from_, request.id)
        if key in self._seen:
            return False
        self._seen[key] = {"ttl": ttl_s, "request": request, "final_state": None}
        return True

    async def record_event(
        self, request_id: str, state: str, detail: Optional[str] = None
    ) -> None:
        self.events.append((request_id, state, detail))

    async def record_completion(
        self,
        request_id: str,
        final_state: str,
        response: Optional[Response] = None,
        error_code: Optional[str] = None,
    ) -> None:
        self.completions.append((request_id, final_state, error_code))

    async def counts(self) -> Mapping[str, int]:
        return {}

    async def purge_before(self, cutoff_ts: float) -> int:
        self.purge_calls.append(cutoff_ts)
        return 0


class FakeSnapshot:
    def __init__(self, status: Optional[str] = "connected", health: Optional[str] = "ok"):
        self._snap = {"status": status, "health": health}

    async def get_snapshot(self) -> Mapping[str, Any]:
        return self._snap


class FakeNodeRegistry:
    def __init__(self) -> None:
        self._seen: Dict[str, float] = {}

    async def mark_seen(self, node_id: str, ts: float) -> None:
        self._seen[node_id] = ts

    async def get_last_seen(self, node_id: str) -> Optional[float]:
        return self._seen.get(node_id)

    def preset(self, node_id: str, ts: float) -> None:
        self._seen[node_id] = ts


class FakeEmitter:
    def __init__(self) -> None:
        self.sent: List[Tuple[str, Response]] = []

    async def emit(self, node_id: str, response: Response) -> None:
        self.sent.append((node_id, response))
