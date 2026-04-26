import asyncio
import os
import sys

import pytest

# Make the package importable when running `pytest` from the repo root.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# paho-mqtt (used by aiomqtt) requires add_reader/add_writer, which only
# SelectorEventLoop supports. ProactorEventLoop is the Windows default in
# Python 3.8+ and does not implement those methods.
# NOTE: WindowsSelectorEventLoopPolicy is deprecated in Python 3.16. When
# pytest-asyncio gains a loop_factory fixture that works on Windows, switch to
# that. For now this is the least-invasive way to make all async tests work.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
def store(tmp_path):
    """A fresh Store backed by a throwaway SQLite file."""
    from meshcore_rpc_services.persistence import Store
    s = Store(str(tmp_path / "test.sqlite3"))
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def snapshot_fn():
    """A callable returning a mutable gateway-snapshot dict."""
    state = {"state": "connected", "detail": None, "since": None, "snapped_at": None}

    async def _get():
        return dict(state)

    # Expose mutation hook on the function for tests that need to tweak.
    _get.state = state  # type: ignore[attr-defined]
    return _get


@pytest.fixture
def ctx(store, snapshot_fn):
    from meshcore_rpc_services.handlers.base import HandlerContext
    return HandlerContext(store=store, gateway_snapshot=snapshot_fn)
