"""Persistence tests. Use a real Store in tmp_path (via the fixture)."""

import time

import pytest

from meshcore_rpc_services.schemas import Request, Response


def _req(id_="r1", type_="ping", from_="n1"):
    return Request.model_validate(
        {"v": 1, "id": id_, "type": type_, "from": from_, "ttl": 5}
    )


@pytest.mark.asyncio
async def test_record_received_detects_duplicates(store):
    assert await store.record_received(_req(), ttl_s=5) is True
    # Same (from, id) again = duplicate
    assert await store.record_received(_req(), ttl_s=5) is False
    # Different id on same node = fresh
    assert await store.record_received(_req(id_="r2"), ttl_s=5) is True
    # Different node, same id = fresh
    assert await store.record_received(_req(from_="n2"), ttl_s=5) is True


@pytest.mark.asyncio
async def test_counts_group_completions_by_final_state(store):
    await store.record_received(_req("a"), ttl_s=5)
    await store.record_received(_req("b"), ttl_s=5)
    await store.record_received(_req("c"), ttl_s=5)

    resp = Response.ok(_req("a"), {"message": "pong"})
    await store.record_completion(
        "a", "n1", final_state="completed_ok", response=resp
    )
    await store.record_completion(
        "b", "n1", final_state="completed_error", error_code="timeout"
    )

    counts = await store.counts()
    assert counts == {"completed_ok": 1, "completed_error": 1, "pending": 1}


@pytest.mark.asyncio
async def test_purge_before_removes_old_completed_rows(store):
    await store.record_received(_req("old"), ttl_s=5)
    resp = Response.ok(_req("old"), {})
    await store.record_completion(
        "old", "n1", final_state="completed_ok", response=resp
    )
    # Force completed_at far into the past.
    store._conn.execute(
        "UPDATE requests SET completed_at = ? WHERE id = ?", (0.0, "old")
    )
    store._conn.commit()

    deleted = await store.purge_before(cutoff_ts=time.time())
    assert deleted == 1
    counts = await store.counts()
    assert counts.get("pending", 0) == 0


@pytest.mark.asyncio
async def test_node_registry_round_trip(store):
    assert await store.get_last_seen("n1") is None
    await store.mark_node_seen("n1", ts=1000.0)
    assert await store.get_last_seen("n1") == 1000.0
    # Idempotent / keeps max
    await store.mark_node_seen("n1", ts=500.0)
    assert await store.get_last_seen("n1") == 1000.0
    await store.mark_node_seen("n1", ts=2000.0)
    assert await store.get_last_seen("n1") == 2000.0


@pytest.mark.asyncio
async def test_gateway_snapshots_are_recorded(store):
    await store.record_gateway_snapshot(state="connected", detail=None, since=1000.0)
    await store.record_gateway_snapshot(state="disconnected", detail="serial error", since=None)
    cur = store._conn.execute("SELECT COUNT(*) FROM gateway_snapshots")
    assert cur.fetchone()[0] == 2
