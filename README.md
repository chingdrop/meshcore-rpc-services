# meshcore-rpc-services

Application-layer RPC services for a personal MeshCore + MQTT network.

The gateway (a separate component, `meshcore-mqtt`) bridges MeshCore ↔ MQTT.
This repo subscribes to RPC requests on MQTT, validates them, routes to
handlers, tracks lifecycle, enforces timeouts, persists everything, and
publishes structured responses. It never talks to LoRa hardware.

## MQTT contract (v1)

| Direction | Topic                                  | Notes                                       |
| --------- | -------------------------------------- | ------------------------------------------- |
| in        | `meshcore/rpc/request`                 | RPC requests                                |
| out       | `meshcore/rpc/response/<node_id>`      | One topic per node                          |
| in        | `meshcore/gateway/status` (retained)   | Cached in memory + persisted                |
| in        | `meshcore/gateway/health` (retained)   | Cached in memory + persisted                |

All topic strings live in `meshcore_rpc_services/mqtt/topics.py`. Do not
hardcode them elsewhere.

## Request / Response schemas

```json
// Request
{ "v": 1, "id": "abc123", "type": "ping", "from": "node-xyz", "ttl": 30, "args": {} }

// Success
{ "v": 1, "id": "abc123", "type": "ping", "to": "node-xyz",
  "status": "ok", "body": {"message": "pong"} }

// Error
{ "v": 1, "id": "abc123", "type": "ping", "to": "node-xyz",
  "status": "error", "error": {"code": "timeout", "message": "..."} }
```

Error codes: `bad_request`, `unknown_type`, `duplicate`, `timeout`, `internal`.

## v1 handlers

- **`ping`** — `{message: "pong"}`, optionally echoes `args.echo` (≤64 chars)
- **`echo`** — `{msg: <truncated args.msg>}` (≤180 chars)
- **`time.now`** — `{ts: <unix>, iso: "<RFC3339Z>"}`
- **`gateway.status`** — `{gw, hb, pending, ok, err, to}`. Gateway is the source of truth.
- **`node.last_seen`** — `{node, ts, age_s}` for the requester's last-seen time, or another node via `args.node`.

Adding a handler is three lines in `handlers/__init__.py` + one new file + a test. Handlers depend only on ports, never on MQTT or SQLite directly.

## Lifecycle states

A request transitions through these explicit states (logged to `request_events`):

```
received → validated → handler_started → response_published → completed_ok
                                       ↘ timeout              → completed_error
                    ↘ rejected          → response_published  → completed_error
```

`rejected` covers `bad_request`, `unknown_type`, and `duplicate`. Terminal
states on the `requests.final_state` column are `completed_ok` and
`completed_error`. See `meshcore_rpc_services/lifecycle.py`.

## Architecture

```
transport/ ──> core, ports, persistence/*, handlers/*, router, schemas
               adapter translates MQTT ↔ internal Request/Response
handlers/  ──> ports, schemas            (no transport, no SQLite)
core       ──> ports, router, timeouts, schemas, lifecycle
```

Three layer rules:

- **Transport-only modules**: `transport/bus.py`, `transport/service.py`, `transport/adapter.py`, `mqtt/topics.py`, scripts.
- **Core is pure**: takes ports, does the lifecycle, returns nothing. No MQTT imports, no SQLite imports.
- **Handlers are pure**: take a `Request` + `HandlerContext` of ports, return a `Response`.

### Transport adapter

`transport/adapter.py` is the **only** place the app-layer translates between wire-level MQTT messages and internal `Request`/`Response` objects. Today the translation is near-identity because the gateway's own `rpc_adapter` already publishes clean JSON on the internal RPC topics. The seam matters for the future: if the gateway ever stops running its adapter, or the wire envelope changes, you change `transport/adapter.py` and nothing else in this repo.

### Migration-friendliness

- **Django later** — swap `SqliteRequestRepository` for a Django-backed implementation of the same `RequestRepository`/`NodeRegistry`/`GatewaySnapshotSink` ports. One line changes in `transport/service.py`. Handlers, core, router, schemas untouched.
- **Celery later** — wrap `core.process_request` in a Celery task; change the consume loop from `asyncio.create_task` to `send_task`. The ports stay the same; a Celery-aware `ResponseEmitter` writes to a "to_publish" table that a small publisher drains.

## Install and run (local Python)

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml
meshcore-rpc-services initdb --config config.yaml
meshcore-rpc-services run    --config config.yaml
```

Env overrides: anything with nested keys uses `__`, e.g.
`MESHCORE_RPC_SERVICES_MQTT__HOST=broker.lan`,
`MESHCORE_RPC_SERVICES_SERVICE__TIMEOUTS__DEFAULT_S=60`.

## Docker test bench

Spin up a broker and the service together:

```bash
docker compose up --build
```

This launches:

- **`mosquitto`** — local MQTT broker on `1883` (anonymous, dev-only).
- **`app`** — the service, auto-connecting to `mosquitto:1883`, persisting
  SQLite to a named volume, retention=30d.

Send a test request from your host:

```bash
python scripts/send_request.py --type ping --from mynode
python scripts/send_request.py --type echo --from mynode --args '{"msg": "hi"}'
python scripts/send_request.py --type time.now --from mynode
```

Seed retained gateway status so `gateway.status` returns something interesting:

```bash
python scripts/seed_gateway_status.py --status connected --health ok
python scripts/send_request.py --type gateway.status --from mynode
```

Watch the service log for the startup summary:

```
============================================================
meshcore-rpc-services starting
  broker         : mosquitto:1883
  client_id      : meshcore-rpc-services
  qos            : 1
  db             : /app/data/meshcore_rpc_services.sqlite3
  log_level      : INFO
  ttl policy     : default=30s min=1s max=300s
  retention      : 30d (sweep every 3600s)
  handlers (5)   :
     - echo
     - gateway.status
     - node.last_seen
     - ping
     - time.now
  subscribe:
     - meshcore/rpc/request
     - meshcore/gateway/status
     - meshcore/gateway/health
  publish:
     - meshcore/rpc/response/<node_id>
============================================================
```

## Tests

```bash
# Unit tests (no broker required)
pytest -m "not integration"

# Integration tests (requires a broker)
docker compose up -d mosquitto
pytest -m integration
# or: MESHCORE_RPC_SERVICES_TEST_MQTT_HOST=broker.lan pytest -m integration
```

Integration tests automatically skip with a clear reason if no broker is reachable. They cover:

- Request → process → response round trip (`ping`)
- Retained `gateway/status` + `gateway/health` ingestion visible via `gateway.status`
- Duplicate `(from, id)` produces a `duplicate` error response
- Unknown type produces an `unknown_type` error response

## Retention

30-day default, configurable. The sweeper runs once at startup and then on
`retention.interval_s` (default 1 hour). `purge_before(cutoff)` deletes
request rows whose `completed_at < cutoff`, their event log entries, and old
gateway-snapshot rows. Run a one-shot purge:

```bash
meshcore-rpc-services purge --config config.yaml
meshcore-rpc-services purge --config config.yaml --days 7   # ad-hoc
```

## Layout

```
meshcore_rpc_services/
  config.py           # AppConfig (YAML + env)
  schemas.py          # Request / Response
  errors.py           # error codes + RpcError
  lifecycle.py        # canonical state strings
  ports.py            # protocols (repo, emitter, snapshot, node registry)
  core.py             # pure request pipeline
  router.py           # type → handler
  retention.py        # periodic cleanup task
  cli.py              # run / initdb / purge
  mqtt/
    topics.py         # single source of truth for topic strings
  timeouts/
    policy.py         # TimeoutPolicy (defaults, per-type, clamp)
    tracker.py        # PendingTracker (run_with_timeout)
  persistence/
    schema.sql        # requests, request_events, gateway_snapshots, nodes
    sqlite.py         # SqliteStore + SqliteRequestRepository
  transport/
    bus.py            # aiomqtt wrapper + snapshot write-through
    adapter.py        # MQTT ↔ Request/Response translation
    service.py        # orchestrator: consume loop, retention, summary
  handlers/
    base.py           # Handler protocol + HandlerContext (ports only)
    ping.py
    echo.py
    time_now.py
    gateway_status.py
    node_last_seen.py
scripts/
  send_request.py        # publish a request, print the response
  seed_gateway_status.py # publish retained gateway state
tests/
  _fakes.py              # shared in-memory port stubs
  test_*.py              # unit tests (no broker)
  integration/
    test_end_to_end.py   # broker-backed, skipped if no broker
Dockerfile
docker-compose.yml
docker/mosquitto.conf
config.example.yaml
.env.example
```

## Deliberately out of scope

No HTTP API, no UI, no hiking/weather, no Django yet, no Celery yet, no
direct serial access. If it isn't an RPC handler over MQTT, it doesn't
belong here.
