"""SQLite persistence for the application layer.

One class, :class:`Store`, with async methods that delegate to a sync
sqlite3 connection via ``asyncio.to_thread``. This avoids an ``aiosqlite``
dependency while keeping the event loop free.

The class covers the full persistence surface:

* request lifecycle writes (received, events, completion)
* read-side counts for the gateway.status handler
* gateway status/health snapshot history
* node registry (last-seen times)
* retention cleanup

If a second backend is ever needed, copy this class's public method
signatures into the new backend. That's the interface — no separate
Protocol needed.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Optional

from meshcore_rpc_services.lifecycle import RECEIVED
from meshcore_rpc_services.schemas import Request, Response

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Each entry is (target_version, sql). Applied in order when the stored
# schema version is below target_version. Add new entries here as the schema
# evolves; never edit or remove existing ones.
_MIGRATIONS: list[tuple[int, str]] = [
    (2, """
        ALTER TABLE gateway_snapshots ADD COLUMN state TEXT;
        ALTER TABLE gateway_snapshots ADD COLUMN detail TEXT;
        ALTER TABLE gateway_snapshots ADD COLUMN since_ts REAL;
    """),
]


class Store:
    """SQLite-backed persistence. Call the async methods from the pipeline."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        with _SCHEMA_PATH.open() as f:
            self._conn.executescript(f.read())
        self._apply_migrations()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _apply_migrations(self) -> None:
        cur = self._conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        )
        row = cur.fetchone()
        current = row[0] if row else 0
        if current == 0:
            # New DB or first open after versioning was introduced: stamp as v1.
            with self._conn:
                self._conn.execute("INSERT INTO schema_version (version) VALUES (1)")
            current = 1
        for version, sql in _MIGRATIONS:
            if version > current:
                self._conn.executescript(sql)
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO schema_version (version) VALUES (?)", (version,)
                    )
                current = version

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    async def record_received(self, request: Request, ttl_s: int) -> bool:
        """Insert the request if new. Return True on fresh, False on duplicate.

        Duplicate detection uses ``INSERT OR IGNORE`` keyed on ``(node_id, id)``.
        """
        return await asyncio.to_thread(
            self._sync_record_received, request, ttl_s
        )

    async def record_event(
            self,
            request_id: str,
            node_id: str,
            state: str,
            detail: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(
            self._sync_record_event, request_id, node_id, state, detail
        )

    async def record_completion(
            self,
            request_id: str,
            node_id: str,
            final_state: str,
            response: Optional[Response] = None,
            error_code: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(
            self._sync_record_completion,
            request_id, node_id, final_state, response, error_code,
        )

    async def counts(self) -> dict[str, int]:
        by_state = await asyncio.to_thread(self._sync_count_by_final_state)
        pending = await asyncio.to_thread(self._sync_count_pending)
        return {**by_state, "pending": pending}

    # ------------------------------------------------------------------
    # Node registry
    # ------------------------------------------------------------------

    async def mark_node_seen(self, node_id: str, ts: float) -> None:
        await asyncio.to_thread(self._sync_mark_node_seen, node_id, ts)

    async def get_last_seen(self, node_id: str) -> Optional[float]:
        return await asyncio.to_thread(self._sync_get_last_seen, node_id)

    # ------------------------------------------------------------------
    # Gateway snapshots
    # ------------------------------------------------------------------

    async def record_gateway_snapshot(
            self, *, state: Optional[str], detail: Optional[str],
            since: Optional[float],
    ) -> None:
        await asyncio.to_thread(
            self._sync_record_gateway_snapshot, state, detail, since
        )

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    async def purge_before(self, cutoff_ts: float) -> int:
        return await asyncio.to_thread(self._sync_purge_before, cutoff_ts)

    # ------------------------------------------------------------------
    # Sync implementations. Called from the threadpool.
    # ------------------------------------------------------------------

    def _sync_record_received(self, request: Request, ttl_s: int) -> bool:
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
            if cur.rowcount != 1:
                return False
            self._conn.execute(
                "INSERT INTO request_events "
                "(request_id, node_id, state, ts) VALUES (?, ?, ?, ?)",
                (request.id, request.from_, RECEIVED, now),
            )
            return True

    def _sync_record_event(
            self,
            request_id: str,
            node_id: str,
            state: str,
            detail: Optional[str],
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO request_events "
                "(request_id, node_id, state, detail, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (request_id, node_id, state, detail, time.time()),
            )

    def _sync_record_completion(
            self,
            request_id: str,
            node_id: str,
            final_state: str,
            response: Optional[Response],
            error_code: Optional[str],
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

    def _sync_mark_node_seen(self, node_id: str, ts: float) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO nodes (node_id, last_seen) VALUES (?, ?) "
                "ON CONFLICT(node_id) DO UPDATE SET "
                "last_seen = MAX(last_seen, excluded.last_seen)",
                (node_id, ts),
            )

    def _sync_get_last_seen(self, node_id: str) -> Optional[float]:
        cur = self._conn.execute(
            "SELECT last_seen FROM nodes WHERE node_id = ?", (node_id,)
        )
        row = cur.fetchone()
        return float(row[0]) if row else None

    def _sync_record_gateway_snapshot(
            self, state: Optional[str], detail: Optional[str],
            since: Optional[float],
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO gateway_snapshots "
                "(ts, status, health, state, detail, since_ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), state, None, state, detail, since),
            )

    def _sync_count_by_final_state(self) -> dict[str, int]:
        cur = self._conn.execute(
            "SELECT final_state, COUNT(*) FROM requests "
            "WHERE final_state IS NOT NULL GROUP BY final_state"
        )
        return {row[0]: row[1] for row in cur.fetchall()}

    def _sync_count_pending(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM requests WHERE final_state IS NULL"
        )
        return int(cur.fetchone()[0])

    def _sync_purge_before(self, cutoff_ts: float) -> int:
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
