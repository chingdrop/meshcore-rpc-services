# meshcore-rpc-services

Application-layer RPC services for a personal MeshCore + MQTT network.

The gateway (a separate component, `meshcore-mqtt`) bridges MeshCore ↔ MQTT.
This repo subscribes to RPC requests on MQTT, validates them, routes to
handlers, tracks lifecycle in SQLite, enforces timeouts, and publishes
structured responses. It never talks to LoRa hardware.

## MQTT contract (v1)

| Direction | Topic                                | Notes                        |
|-----------|--------------------------------------|------------------------------|
| in        | `meshcore/rpc/request`               | RPC requests                 |
| out       | `meshcore/rpc/response/<node_id>`    | One topic per node           |
| in        | `meshcore/gateway/status` (retained) | Cached in memory + persisted |
| in        | `meshcore/gateway/health` (retained) | Cached in memory + persisted |

All topic strings live in `meshcore_rpc_services/mqtt/topics.py`. Don't
hardcode them elsewhere.

## Request / Response schemas

```json
// Request
{
  "v": 1,
  "id": "abc123",
  "type": "ping",
  "from": "node-xyz",
  "ttl": 30,
  "args": {}
}

// Success
{
  "v": 1,
  "id": "abc123",
  "type": "ping",
  "to": "node-xyz",
  "status": "ok",
  "body": {
    ...
  }
}

// Error
{
  "v": 1,
  "id": "abc123",
  "type": "ping",
  "to": "node-xyz",
  "status": "error",
  "error": {
    "code": "timeout",
    "message": "..."
  }
}
```

Error codes: `bad_request`, `unknown_type`, `duplicate`, `timeout`, `internal`.

`ttl` and `args` are optional in requests. `ttl` defaults to the policy
`default_s`; `args` defaults to `{}`.

## v1 handlers

### `ping`

```json
{
  "message": "pong"
}
```

Pass `args.echo` (≤ 64 chars) to get it reflected back in `message`.

### `echo`

```json
{
  "msg": "<args.msg truncated to 180 chars>"
}
```

### `time.now`

```json
{
  "ts": 1714000000.0,
  "iso": "2024-04-25T00:00:00Z"
}
```

### `gateway.status`

```json
{
  "gw": "connected",
  // last retained meshcore/gateway/status value, or "unknown"
  "hb": "ok",
  // last retained meshcore/gateway/health value, or "unknown"
  "snap_age_s": 42,
  // seconds since the cache was last updated; null if never received
  "pending": 1,
  // requests with no final_state yet
  "ok": 18,
  // completed_ok count (all time)
  "err": 2,
  // completed_error count (all time)
  "to": 0
  // timeout count (all time)
}
```

`snap_age_s` is `null` when no retained message has arrived since startup —
the gateway cache is cold and `gw`/`hb` should be treated as stale.

### `node.last_seen`

```json
{
  "node": "node-abc",
  "ts": 1714000000.0,
  "age_s": 120
}
```

`ts` and `age_s` are `null` if the node has never been seen. Defaults to the
requester's own node id; pass `args.node` to query another node.

## Adding a handler

1. Create `handlers/my_type.py`:

```python
from meshcore_rpc_services.handlers.base import Handler, HandlerContext
from meshcore_rpc_services.schemas import Request, Response


class MyTypeHandler:
    type = "my.type"

    async def handle(self, request: Request, ctx: HandlerContext) -> Response:
        return Response.ok(request, {"result": "..."})


handler: Handler = MyTypeHandler()
```

2. Register it in `handlers/__init__.py`:

```python
from meshcore_rpc_services.handlers import my_type

DEFAULT_HANDLERS = [..., my_type.handler]
```

3. Add `tests/test_my_type_handler.py`. Use the `ctx` fixture from `conftest.py` —
   no mocks, no broker required.

Raise `meshcore_rpc_services.errors.RpcError(code, message)` from `handle()`
to return a structured error response. Unhandled exceptions become `internal` errors.

## Lifecycle

Requests move through these states, appended to the `request_events` table:

```
received → validated → handler_started → response_published → completed_ok
                                       ↘ timeout              → completed_error
                    ↘ rejected          → response_published  → completed_error
```

`rejected` covers `bad_request`, `unknown_type`, and `duplicate`. Terminal
values on `requests.final_state` are `completed_ok` and `completed_error`.
See `meshcore_rpc_services/lifecycle.py`.

## Architecture

Flat and concrete. There are no Protocol types, no dependency-injection
ports, no "seams for later framework X." If a second persistence backend
or a second transport is ever needed, add a class with the same public
methods; that's the interface.

```
transport/        MQTT wrapper, adapter (bytes ↔ Request/Response), service loop
core.py           the pipeline: parse -> validate -> route -> run -> persist -> emit
handlers/         business logic, one file per type
persistence/      Store class (async-facing, sqlite3 under a threadpool)
timeouts/         TimeoutPolicy + PendingTracker
lifecycle.py      canonical state strings
mqtt/topics.py    single source of truth for topic strings
retention.py      periodic cleanup task
```

The service wires these together:

```
MqttBus  ──subscribes──>  meshcore/rpc/request
   │                      meshcore/gateway/{status,health}  (retained → cached + persisted)
   │
   ▼
Service._handle_one(msg.payload)
   │
   ├─ transport.adapter.inbound_to_request → Request (or structured error)
   │
   ▼
core.process_request(request, store, router, ctx, emit, tracker, policy)
   │
   └─ store methods + emit(node_id, response) via bus.publish
```

### Transport adapter

`transport/adapter.py` translates between wire MQTT and internal
`Request`/`Response` (JSON parse + pydantic validate). The gateway's own RPC
adapter may drift; this is the one place to follow it.

## Install and run

```bash
# With uv (recommended)
uv sync --extra dev

# With pip
pip install -e ".[dev]"
```

```bash
cp config.example.yaml config.yaml
meshcore-rpc-services initdb --config config.yaml
meshcore-rpc-services run    --config config.yaml
```

Env overrides use `__` between levels:

```
MESHCORE_RPC_SERVICES_MQTT__HOST=broker.lan
MESHCORE_RPC_SERVICES_SERVICE__TIMEOUTS__DEFAULT_S=60
```

## Docker test bench

```bash
docker compose up --build
```

Launches:

- **`mosquitto`** — local MQTT broker on `1883` (anonymous, dev-only).
- **`app`** — the service, connecting to `mosquitto:1883`, persisting SQLite
  to a named volume, 30-day retention.

Send test requests:

```bash
python scripts/send_request.py --type ping --from mynode
python scripts/send_request.py --type echo --from mynode --args '{"msg":"hi"}'
python scripts/send_request.py --type time.now --from mynode
```

Seed retained gateway status so `gateway.status` returns something:

```bash
python scripts/seed_gateway_status.py --status connected --health ok
python scripts/send_request.py --type gateway.status --from mynode
```

## Tests

```bash
# Unit tests (no broker required)
pytest -m "not integration"

# Integration tests (broker required)
docker compose up -d mosquitto
pytest -m integration
```

Unit tests use a real SQLite `Store` backed by `tmp_path`. No mocks, no
port stubs — the interface is the class, so tests exercise the real class.

## Logging

Log lines include a `[request_id node_id rpc_type]` context block:

```
2026-04-25 10:00:00,123 INFO meshcore_rpc_services.core [abc123 node-xyz ping] Handler crashed
2026-04-25 10:00:00,124 INFO meshcore_rpc_services.transport.service [- - -] meshcore-rpc-services starting
```

Fields default to `-` outside request context. Configure `service.log_level`
in `config.yaml` or via `MESHCORE_RPC_SERVICES_SERVICE__LOG_LEVEL=DEBUG`.

## Retention

30-day default, configurable. The sweeper runs once at startup and then on
`retention.interval_s` (default 1 hour). `Store.purge_before(cutoff)`
deletes request rows whose `completed_at < cutoff`, their events, and
old gateway-snapshot rows.

```bash
meshcore-rpc-services purge --config config.yaml
meshcore-rpc-services purge --config config.yaml --days 7
```

## Schema migrations

The database version is tracked in the `schema_version` table. New databases
are stamped as version 1 on first open. To add a schema change:

1. Add an entry to `_MIGRATIONS` in `persistence/sqlite.py`:

```python
_MIGRATIONS: list[tuple[int, str]] = [
    (2, "ALTER TABLE requests ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;"),
]
```

2. Bump the version number (`2`, `3`, …). Never edit or remove existing entries.

`Store.__init__` applies pending migrations on every open, so a deploy is
enough — no manual `ALTER TABLE` needed.

## Layout

```
meshcore_rpc_services/
  config.py           # AppConfig (YAML + env)
  schemas.py          # Request / Response
  errors.py           # error codes + RpcError
  lifecycle.py        # canonical state strings
  core.py             # the pipeline
  router.py           # type → handler
  retention.py        # periodic cleanup
  cli.py              # run / initdb / purge
  mqtt/topics.py      # topic string constants
  timeouts/
    policy.py         # TimeoutPolicy
    tracker.py        # PendingTracker
  persistence/
    schema.sql        # requests, request_events, gateway_snapshots, nodes, schema_version
    sqlite.py         # Store (single class, async surface)
  transport/
    bus.py            # aiomqtt wrapper + gateway snapshot cache
    adapter.py        # MQTT ↔ Request/Response
    service.py        # orchestrator
  handlers/
    base.py           # Handler protocol + HandlerContext
    ping.py / echo.py / time_now.py
    gateway_status.py / node_last_seen.py
scripts/
  send_request.py        # publish a request, print the response
  seed_gateway_status.py # publish retained gateway state
tests/
  conftest.py            # store + ctx fixtures
  test_*.py              # unit tests (no broker)
  integration/
    test_end_to_end.py   # broker-backed, skipped if no broker
Dockerfile
docker-compose.yml
docker/mosquitto.conf
config.example.yaml
.env.example
CHANGELOG.md
```

## Known limitations

- **No rate limiting.** A misbehaving node can flood the request queue. The
  `(node_id, id)` dedup key prevents exact duplicate replays, but a node
  sending many distinct request IDs will consume memory and disk unbounded.
- **Gateway snapshot is in-memory only.** `gateway.status` reflects what the
  service cached since last startup. After a restart the cache is cold until
  the next retained publish arrives; `snap_age_s` will be `null`.
- **SQLite only.** Adding columns requires an entry in `_MIGRATIONS` (see
  above). Other schema changes (rename, drop) need a manual migration.
- **Windows asyncio.** The production CLI uses `SelectorEventLoop` directly
  to work around a paho-mqtt incompatibility with `ProactorEventLoop` (the
  Windows default). The test harness uses `WindowsSelectorEventLoopPolicy`,
  which is deprecated in Python 3.16 — track upstream paho-mqtt for a fix.

## License

This project is licensed under `GPL-3.0-only`. See [`LICENSE`](LICENSE).

If you redistribute binaries, containers, or other non-source forms of this
project, GPL-3.0 requires that you also make the corresponding source
available under the same license terms.
