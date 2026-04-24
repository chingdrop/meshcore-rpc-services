"""SQLite persistence for request lifecycle.

Single sync connection, wrapped in asyncio.to_thread for the async service.
Keeps the dep surface tiny — no aiosqlite needed.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Optional

from meshcore_rpc_services.schemas import Request, Response

# Lifecycle state names. Kept as module-level constants so handlers and tests
# don't need to guess strings.
RECEIVED = "received"
VALIDATED = "validated"
HANDLER_STARTED = "handler_started"
RESPONSE_PUBLISHED = "response_published"
TIMEOUT = "timeout"
ERROR = "error"

_SCHEMA = """
          CREATE TABLE IF NOT EXISTS requests
          (
              id
              TEXT
              PRIMARY
              KEY,
              node_id
              TEXT
              NOT
              NULL,
              type
              TEXT
              NOT
              NULL,
              ttl_s
              INTEGER
              NOT
              NULL,
              received_at
              REAL
              NOT
              NULL,
              completed_at
              REAL,
              final_state
              TEXT,
              error_code
              TEXT,
              request_json
              TEXT
              NOT
              NULL,
              response_json
              TEXT
          );

          CREATE TABLE IF NOT EXISTS request_events
          (
              id
              INTEGER
              PRIMARY
              KEY
              AUTOINCREMENT,
              request_id
              TEXT
              NOT
              NULL,
              state
              TEXT
              NOT
              NULL,
              detail
              TEXT,
              ts
              REAL
              NOT
              NULL
          );

          CREATE INDEX IF NOT EXISTS idx_events_req ON request_events(request_id);
          CREATE INDEX IF NOT EXISTS idx_req_received ON requests(received_at); \
          """


class Store:
    """Sync SQLite wrapper. Use via the async helpers on AsyncStore."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        # Ensure parent dir exists when db_path is nested.
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- writes ---

    def record_received(self, request: Request, ttl_s: int) -> None:
        now = time.time()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO requests "
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
            self._conn.execute(
                "INSERT INTO request_events (request_id, state, ts) VALUES (?, ?, ?)",
                (request.id, RECEIVED, now),
            )

    def record_event(
            self, request_id: str, state: str, detail: Optional[str] = None
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO request_events (request_id, state, detail, ts) "
                "VALUES (?, ?, ?, ?)",
                (request_id, state, detail, time.time()),
            )

    def record_completion(
            self,
            request_id: str,
            final_state: str,
            response: Optional[Response] = None,
            error_code: Optional[str] = None,
    ) -> None:
        now = time.time()
        response_json = response.to_json() if response else None
        with self._conn:
            self._conn.execute(
                "UPDATE requests SET completed_at = ?, final_state = ?, "
                "error_code = ?, response_json = ? WHERE id = ?",
                (now, final_state, error_code, response_json, request_id),
            )

    # --- reads (for diagnostics / gateway.status) ---

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


class AsyncStore:
    """Thin async facade so the service can `await` persistence without blocking."""

    def __init__(self, store: Store) -> None:
        self._store = store

    async def record_received(self, request: Request, ttl_s: int) -> None:
        await asyncio.to_thread(self._store.record_received, request, ttl_s)

    async def record_event(
            self, request_id: str, state: str, detail: Optional[str] = None
    ) -> None:
        await asyncio.to_thread(self._store.record_event, request_id, state, detail)

    async def record_completion(
            self,
            request_id: str,
            final_state: str,
            response: Optional[Response] = None,
            error_code: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(
            self._store.record_completion,
            request_id,
            final_state,
            response,
            error_code,
        )

    async def counts(self) -> dict[str, int]:
        by_state = await asyncio.to_thread(self._store.count_by_final_state)
        pending = await asyncio.to_thread(self._store.count_pending)
        return {**by_state, "pending": pending}

    def close(self) -> None:
        self._store.close()
