import pytest

from meshcore_rpc_services.handlers import DEFAULT_HANDLERS
from meshcore_rpc_services.router import Router


def test_router_resolves_default_handlers():
    r = Router(DEFAULT_HANDLERS)
    for t in (
            "ping", "echo", "time.now", "gateway.status", "node.last_seen",
            "node.location.report", "base.location", "node.location", "node.status",
            "return_to_base",
    ):
        assert r.resolve(t) is not None
    assert r.resolve("nope") is None


def test_router_rejects_duplicate():
    class H:
        type = "ping"

        async def handle(self, request, ctx): ...

    with pytest.raises(ValueError):
        Router([H(), H()])
