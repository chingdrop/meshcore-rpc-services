import time

from meshcore_rpc_services.persistence import SqliteRequestRepository, SqliteStore
from meshcore_rpc_services.schemas import Request, Response


def _req(id_="r1", type_="ping", from_="n1"):
    return Request.model_validate(
        {"v": 1, "id": id_, "type": type_, "from": from_, "ttl": 5}
    )


def test_record_received_returns_true_on_fresh_and_false_on_duplicate(tmp_path):
    store = SqliteStore(str(tmp_path / "t.sqlite3"))
    try:
        assert store.record_received(_req(), ttl_s=5) is True
        assert store.record_received(_req(), ttl_s=5) is False  # dup (n1, r1)
        assert store.record_received(_req(id_="r2"), ttl_s=5) is True
        # Different node, same id: fresh
        assert store.record_received(_req(from_="n2"), ttl_s=5) is True
    finally:
        store.close()


def test_completion_counts_by_final_state(tmp_path):
    store = SqliteStore(str(tmp_path / "t.sqlite3"))
    try:
        store.record_received(_req("a"), ttl_s=5)
        store.record_received(_req("b"), ttl_s=5)
        store.record_received(_req("c"), ttl_s=5)

        # OK completion needs a Response object.
        resp = Response.ok(_req("a"), {"message": "pong"})
        store.record_completion("a", "n1", final_state="completed_ok", response=resp)
        store.record_completion("b", "n1", final_state="completed_error", error_code="timeout")

        counts = store.count_by_final_state()
        assert counts == {"completed_ok": 1, "completed_error": 1}
        assert store.count_pending() == 1  # "c" never completed
    finally:
        store.close()


def test_purge_before_removes_old_completed_rows(tmp_path):
    store = SqliteStore(str(tmp_path / "t.sqlite3"))
    try:
        store.record_received(_req("old"), ttl_s=5)
        resp = Response.ok(_req("old"), {})
        store.record_completion("old", "n1", final_state="completed_ok", response=resp)
        # Force the completed_at far into the past.
        store._conn.execute(
            "UPDATE requests SET completed_at = ? WHERE id = ?", (0.0, "old")
        )
        store._conn.commit()

        deleted = store.purge_before(cutoff_ts=time.time())
        assert deleted == 1
        assert store.count_pending() == 0
    finally:
        store.close()


def test_node_registry_round_trip(tmp_path):
    store = SqliteStore(str(tmp_path / "t.sqlite3"))
    try:
        assert store.get_last_seen("n1") is None
        store.mark_node_seen("n1", ts=1000.0)
        assert store.get_last_seen("n1") == 1000.0
        # Idempotent / keeps max
        store.mark_node_seen("n1", ts=500.0)
        assert store.get_last_seen("n1") == 1000.0
        store.mark_node_seen("n1", ts=2000.0)
        assert store.get_last_seen("n1") == 2000.0
    finally:
        store.close()


def test_gateway_snapshots_are_recorded(tmp_path):
    store = SqliteStore(str(tmp_path / "t.sqlite3"))
    try:
        store.record_gateway_snapshot("connected", "ok")
        store.record_gateway_snapshot("disconnected", None)
        cur = store._conn.execute("SELECT COUNT(*) FROM gateway_snapshots")
        assert cur.fetchone()[0] == 2
    finally:
        store.close()
