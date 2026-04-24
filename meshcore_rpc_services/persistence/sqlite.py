"""SQLite implementation of the persistence ports.

Sync ``sqlite3`` under a threadpool via ``asyncio.to_thread``. One connection,
serialised writes. That's plenty for a personal mesh; and when it isn't, the
port split means we can swap in a Django-backed implementation without
touching ``core`` or the handlers.

This module implements three ports:

* :class:`RequestRepository`
* :class:`GatewaySnapshotSink`
* :class:`NodeRegistry`

The first wraps a :class:`SqliteStore` (sync); the other two live directly
on the same store for simplicity.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Mapping, Optional

from meshcore_rpc_services.lifecycle import RECEIVED
from meshcore_rpc_services.schemas import Request, Response

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class SqliteStore:
    """Sync SQLite wrapper. One connection, executed on a worker thread
    by the async facades below.
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        with _SCHEMA_PATH.open() as f:
            self._conn.executescript(f.read())
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -----------------------------------------------------------------
    # Request lifecycle writes
    # -----------------------------------------------------------------

    def record_received(self, request: Request, ttl_s: int) -> bool:
        """Insert the request if new. Return True on fresh, False on duplicate.

        Duplicate detection uses ``INSERT OR IGNORE`` keyed on
        ``(node_id, id)``.
        """
        now = time.time()
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO requests "
                "(id, node_id, type, ttl_s, received_at, request_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    request.id,
                    request.from_,
                    request.type,
                    ttl_s,
                    now,
                    request.model_dump_json(by_alias=True),
                ),
            )
            fresh = cur.rowcount == 1
            if fresh:
                self._conn.execute(
                    "INSERT INTO request_events "
                    "(request_id, node_id, state, ts) VALUES (?, ?, ?, ?)",
                    (request.id, request.from_, RECEIVED, now),
                )
            return fresh

    def record_event(
        self,
        request_id: str,
        node_id: str,
        state: str,
        detail: Optional[str] = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO request_events "
                "(request_id, node_id, state, detail, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (request_id, node_id, state, detail, time.time()),
            )

    def record_completion(
        self,
        request_id: str,
        node_id: str,
        final_state: str,
        response: Optional[Response] = None,
        error_code: Optional[str] = None,
    ) -> None:
        now = time.time()
        response_json = response.to_json() if response else None
        with self._conn:
            self._conn.execute(
                "UPDATE requests "
                "SET completed_at = ?, final_state = ?, "
                "    error_code = ?, response_json = ? "
                "WHERE id = ? AND node_id = ?",
                (now, final_state, error_code, response_json,
                 request_id, node_id),
            )

    # -----------------------------------------------------------------
    # Node registry
    # -----------------------------------------------------------------

    def mark_node_seen(self, node_id: str, ts: float) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO nodes (node_id, last_seen) VALUES (?, ?) "
                "ON CONFLICT(node_id) DO UPDATE SET "
                "last_seen = MAX(last_seen, excluded.last_seen)",
                (node_id, ts),
            )

    def get_last_seen(self, node_id: str) -> Optional[float]:
        cur = self._conn.execute(
            "SELECT last_seen FROM nodes WHERE node_id = ?", (node_id,)
        )
        row = cur.fetchone()
        return float(row[0]) if row else None

    # -----------------------------------------------------------------
    # Gateway snapshots
    # -----------------------------------------------------------------

    def record_gateway_snapshot(
        self, status: Optional[str], health: Optional[str]
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO gateway_snapshots (ts, status, health) "
                "VALUES (?, ?, ?)",
                (time.time(), status, health),
            )

    # -----------------------------------------------------------------
    # Retention / reads
    # -----------------------------------------------------------------

    def purge_before(self, cutoff_ts: float) -> int:
        """Delete request rows completed before ``cutoff_ts`` (plus their
        events), and old snapshot rows. Returns request rows deleted.
        """
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM requests "
                "WHERE completed_at IS NOT NULL AND completed_at < ?",
                (cutoff_ts,),
            )
            deleted = cur.rowcount
            self._conn.execute(
                "DELETE FROM request_events "
                "WHERE ts < ? AND request_id NOT IN (SELECT id FROM requests)",
                (cutoff_ts,),
            )
            self._conn.execute(
                "DELETE FROM gateway_snapshots WHERE ts < ?",
                (cutoff_ts,),
            )
        return deleted

    def count_by_final_state(self) -> dict[str, int]:
        cur = self._conn.execute(
            "SELECT final_state, COUNT(*) FROM requests "
            "WHERE final_state IS NOT NULL GROUP BY final_state"
        )
        return {row[0]: row[1] for row in cur.fetchall()}

    def count_pending(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM requests WHERE final_state IS NULL"
        )
        return int(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# Async facades implementing the ports
# ---------------------------------------------------------------------------


class SqliteRequestRepository:
    """Implements :class:`ports.RequestRepository`,
    :class:`ports.GatewaySnapshotSink`, and :class:`ports.NodeRegistry`.

    The three ports collapse onto the same store for simplicity; a future
    Django backend might split them across multiple models, which is fine —
    the ports already define the boundary.
    """

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    # RequestRepository
    async def record_received(self, request: Request, ttl_s: int) -> bool:
        return await asyncio.to_thread(
            self._store.record_received, request, ttl_s
        )

    async def record_event(
        self, request_id: str, state: str, detail: Optional[str] = None
    ) -> None:
        # We don't have node_id here; store it alongside for retention.
        # The core pipeline passes it via keyword form that includes node_id
        # when available. For simpler callers we pass empty string.
        await asyncio.to_thread(
            self._store.record_event, request_id, "", state, detail
        )

    async def record_event_for_node(
        self,
        request_id: str,
        node_id: str,
        state: str,
        detail: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(
            self._store.record_event, request_id, node_id, state, detail
        )

    async def record_completion(
        self,
        request_id: str,
        final_state: str,
        response: Optional[Response] = None,
        error_code: Optional[str] = None,
    ) -> None:
        # node_id is recoverable from the response.to; falls back to empty
        # string if response is None (shouldn't happen in practice).
        node_id = response.to if response else ""
        await asyncio.to_thread(
            self._store.record_completion,
            request_id, node_id, final_state, response, error_code,
        )

    async def counts(self) -> Mapping[str, int]:
        by_state = await asyncio.to_thread(self._store.count_by_final_state)
        pending = await asyncio.to_thread(self._store.count_pending)
        return {**by_state, "pending": pending}

    async def purge_before(self, cutoff_ts: float) -> int:
        return await asyncio.to_thread(self._store.purge_before, cutoff_ts)

    # GatewaySnapshotSink
    async def record_snapshot(
        self, *, status: Optional[str], health: Optional[str]
    ) -> None:
        await asyncio.to_thread(
            self._store.record_gateway_snapshot, status, health
        )

    # NodeRegistry
    async def mark_seen(self, node_id: str, ts: float) -> None:
        await asyncio.to_thread(self._store.mark_node_seen, node_id, ts)

    async def get_last_seen(self, node_id: str) -> Optional[float]:
        return await asyncio.to_thread(self._store.get_last_seen, node_id)

    # Housekeeping
    def close(self) -> None:
        self._store.close()
