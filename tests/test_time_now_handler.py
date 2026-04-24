import re
import time

import pytest

from meshcore_rpc_services.handlers.base import HandlerContext
from meshcore_rpc_services.handlers.time_now import handler as tn
from meshcore_rpc_services.schemas import Request

from tests._fakes import FakeNodeRegistry, FakeRepo, FakeSnapshot


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _ctx():
    return HandlerContext(
        snapshot=FakeSnapshot(), repo=FakeRepo(), nodes=FakeNodeRegistry()
    )


@pytest.mark.asyncio
async def test_time_now_returns_ts_and_iso():
    req = Request.model_validate(
        {"v": 1, "id": "t1", "type": "time.now", "from": "n1"}
    )
    before = time.time()
    resp = await tn.handle(req, _ctx())
    after = time.time()

    assert resp.status == "ok"
    assert resp.body is not None
    assert before <= resp.body["ts"] <= after
    assert _ISO_RE.match(resp.body["iso"])
