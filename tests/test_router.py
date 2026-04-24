import pytest

from meshcore_rpc_services.handlers import DEFAULT_HANDLERS
from meshcore_rpc_services.router import Router


def test_router_resolves_default_handlers():
    r = Router(DEFAULT_HANDLERS)
    for t in ("ping", "echo", "time.now", "gateway.status", "node.last_seen"):
        assert r.resolve(t) is not None
    assert r.resolve("nope") is None
    assert set(r.types()) == {
        "ping", "echo", "time.now", "gateway.status", "node.last_seen"
    }


def test_router_rejects_duplicate():
    class H:
        type = "ping"

        async def handle(self, request, ctx): ...

    with pytest.raises(ValueError):
        Router([H(), H()])
