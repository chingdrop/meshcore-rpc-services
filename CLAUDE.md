# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install for development
pip install -e ".[dev]"

# Run unit tests (no broker required)
pytest -m "not integration"

# Run integration tests (requires MQTT broker)
docker compose up -d mosquitto
pytest -m integration

# Run a single test
pytest tests/test_core_pipeline.py::test_ping_happy_path -v

# Start full stack
docker compose up --build

# CLI
meshcore-rpc-services initdb --config config.yaml
meshcore-rpc-services run --config config.yaml
meshcore-rpc-services purge --config config.yaml
```

## Architecture

**meshcore-rpc-services** is an async Python RPC service that consumes requests over MQTT, routes them to handlers, tracks lifecycle in SQLite, enforces timeouts, and publishes structured responses.

### Request Pipeline (`core.py`)

```
MqttBus → transport/adapter.py → core.process_request()
  1. store.record_received()          — dedup via (node_id, request.id) unique key
  2. router.resolve(request.type)     — map type string → Handler
  3. tracker.run_with_timeout()       — execute handler with TTL from policy
  4. store.record_event()             — append state to event log
  5. emit(node_id, Response)          — publish to meshcore/rpc/response/<node_id>
  6. store.record_completion()        — set final_state (completed_ok | completed_error)
```

`core.py` knows nothing about MQTT — it receives a `Request` and emits via a callback. This keeps handlers fully transport-agnostic.

### MQTT Contract

- **Inbound**: `meshcore/rpc/request` — JSON `{v, id, type, from, ttl, args}`
- **Outbound**: `meshcore/rpc/response/<node_id>` — JSON `{v, id, type, to, status, body|error}`
- Error codes: `bad_request`, `unknown_type`, `duplicate`, `timeout`, `internal`

### Key Modules

| Module | Role |
|---|---|
| `core.py` | Pipeline orchestrator — the main entry point for processing |
| `transport/service.py` | Top-level orchestrator; wires MQTT bus → core pipeline |
| `transport/adapter.py` | JSON parse + Pydantic validation → `Request` |
| `transport/bus.py` | aiomqtt wrapper |
| `handlers/` | One file per RPC type; each has `.type` and async `.handle(request, ctx)` |
| `persistence/sqlite.py` | `Store` — async SQLite via `asyncio.to_thread` |
| `timeouts/` | `TimeoutPolicy` (resolution) + `PendingTracker` (enforcement) |
| `mqtt/topics.py` | Single source of truth for MQTT topic strings |
| `retention.py` | Periodic cleanup asyncio task |
| `config.py` | `AppConfig` via pydantic-settings (YAML + env overrides) |
| `schemas.py` | `Request`/`Response` Pydantic models |
| `router.py` | Maps type strings to `Handler` instances |

### Adding a Handler

1. Create `handlers/my_type.py` with a class having `.type` and `async .handle(request, ctx) -> Response`
2. Append a module-level `handler` instance to `DEFAULT_HANDLERS` in `handlers/__init__.py`
3. Add `tests/test_my_type_handler.py`

### Testing Patterns

Tests use **real classes against a throwaway SQLite file** — no mocks. `conftest.py` provides:
- `store` fixture — fresh `Store` backed by `tmp_path`
- `ctx` fixture — `HandlerContext(store=store, snapshot_fn=snapshot_fn)`

Integration tests in `tests/integration/` require a live broker and are gated with `@pytest.mark.integration`.

### Configuration

YAML config (`config.example.yaml`) with env var overrides using prefix `MESHCORE_RPC_SERVICES_` and `__` as the nested-key delimiter:

```
MESHCORE_RPC_SERVICES_MQTT__HOST=localhost
MESHCORE_RPC_SERVICES_SERVICE__DB_PATH=./data/meshcore_rpc_services.sqlite3
MESHCORE_RPC_SERVICES_SERVICE__TIMEOUTS__DEFAULT_S=30
```

### Database

Four SQLite tables (schema in `persistence/schema.sql`): `requests`, `request_events`, `gateway_snapshots`, `nodes`. The `(node_id, id)` unique key on `requests` enforces deduplication.
