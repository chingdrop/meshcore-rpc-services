from meshcore_app.persistence import Store
from meshcore_app.schemas import Request, Response


def _req(id_="r1", type_="ping", from_="n1"):
    return Request.model_validate(
        {"v": 1, "id": id_, "type": type_, "from": from_, "ttl": 5}
    )


def test_record_received_and_event(tmp_path):
    store = Store(str(tmp_path / "t.sqlite3"))
    try:
        store.record_received(_req(), ttl_s=5)
        store.record_event("r1", "validated")
        assert store.count_pending() == 1
        assert store.count_by_final_state() == {}
    finally:
        store.close()


def test_record_completion_counts(tmp_path):
    store = Store(str(tmp_path / "t.sqlite3"))
    try:
        store.record_received(_req("a"), ttl_s=5)
        store.record_received(_req("b"), ttl_s=5)
        store.record_received(_req("c"), ttl_s=5)

        resp = Response.ok(_req("a"), {"message": "pong"})
        store.record_completion("a", final_state="ok", response=resp)
        store.record_completion("b", final_state="timeout", error_code="timeout")

        counts = store.count_by_final_state()
        assert counts == {"ok": 1, "timeout": 1}
        assert store.count_pending() == 1  # "c" never completed
    finally:
        store.close()
