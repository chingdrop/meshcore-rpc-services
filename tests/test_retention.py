import asyncio
import time

import pytest

from meshcore_rpc_services.retention import RetentionSweeper


@pytest.mark.asyncio
async def test_run_once_purges_nothing_on_empty_store(store):
    sweeper = RetentionSweeper(store, days=30, interval_s=3600)
    assert await sweeper.run_once() == 0


@pytest.mark.asyncio
async def test_run_once_purges_old_completed_requests(store):
    from meshcore_rpc_services.schemas import Request, Response
    from meshcore_rpc_services.lifecycle import COMPLETED_OK

    fake = Request.model_validate(
        {"v": 1, "id": "old", "type": "ping", "from": "n1"}
    )
    await store.record_received(fake, ttl_s=5)
    await store.record_completion(
        "old", "n1", final_state=COMPLETED_OK, response=Response.ok(fake, {})
    )
    store._conn.execute(
        "UPDATE requests SET completed_at = ? WHERE id = ?", (0.0, "old")
    )
    store._conn.commit()

    sweeper = RetentionSweeper(store, days=30, interval_s=3600)
    assert await sweeper.run_once() == 1


def test_construct_rejects_bad_args(store):
    with pytest.raises(ValueError):
        RetentionSweeper(store, days=0, interval_s=60)
    with pytest.raises(ValueError):
        RetentionSweeper(store, days=30, interval_s=0.5)


@pytest.mark.asyncio
async def test_start_stop_cycle_is_clean(store):
    sweeper = RetentionSweeper(store, days=30, interval_s=1)
    sweeper.start()
    await asyncio.sleep(0.05)  # give the first immediate sweep a chance
    await sweeper.stop()
