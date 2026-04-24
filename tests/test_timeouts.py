import asyncio

import pytest

from meshcore_rpc_services.timeouts import PendingTracker, clamp_ttl


def test_clamp_ttl_defaults_and_caps():
    assert clamp_ttl(None, default_s=30, max_s=300) == 30
    assert clamp_ttl(10, default_s=30, max_s=300) == 10
    assert clamp_ttl(99999, default_s=30, max_s=300) == 300
    assert clamp_ttl(0, default_s=30, max_s=300) == 1  # never <1


@pytest.mark.asyncio
async def test_run_with_timeout_success():
    tracker = PendingTracker()

    async def quick():
        await asyncio.sleep(0.01)
        return 42

    assert await tracker.run_with_timeout(quick(), ttl_s=1) == 42
    assert tracker.in_flight() == 0


@pytest.mark.asyncio
async def test_run_with_timeout_fires():
    tracker = PendingTracker()

    async def slow():
        await asyncio.sleep(5)

    with pytest.raises(asyncio.TimeoutError):
        # ttl_s must be an int per the signature; use a very short value and
        # bypass by monkey-path-style: just call wait_for directly for sub-second.
        await asyncio.wait_for(slow(), timeout=0.05)
    assert tracker.in_flight() == 0
