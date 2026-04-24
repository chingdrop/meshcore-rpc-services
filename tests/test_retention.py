import asyncio
import time

import pytest

from meshcore_rpc_services.retention import RetentionSweeper
from tests._fakes import FakeRepo


@pytest.mark.asyncio
async def test_run_once_calls_purge_with_expected_cutoff():
    repo = FakeRepo()
    sweeper = RetentionSweeper(repo, days=30, interval_s=3600)
    before = time.time()
    await sweeper.run_once()
    after = time.time()
    assert len(repo.purge_calls) == 1
    cutoff = repo.purge_calls[0]
    # Cutoff should be "now - 30 days" within a second.
    expected_low = before - (30 * 86400) - 1
    expected_high = after - (30 * 86400) + 1
    assert expected_low <= cutoff <= expected_high


def test_construct_rejects_bad_args():
    with pytest.raises(ValueError):
        RetentionSweeper(FakeRepo(), days=0, interval_s=60)
    with pytest.raises(ValueError):
        RetentionSweeper(FakeRepo(), days=30, interval_s=0.5)


@pytest.mark.asyncio
async def test_start_stop_cycle_is_clean():
    repo = FakeRepo()
    # Tiny interval so the loop spins.
    sweeper = RetentionSweeper(repo, days=30, interval_s=1)
    sweeper.start()
    # Give the first immediate-run sweep a chance to execute.
    await asyncio.sleep(0.05)
    await sweeper.stop()
    assert len(repo.purge_calls) >= 1
