# meshcore-rpc-services

Application-layer RPC services for a personal MeshCore + MQTT network.

The gateway (a separate component, `meshcore-mqtt`) bridges MeshCore ↔ MQTT.
This repo subscribes to RPC requests on MQTT, validates them, routes to
handlers, tracks lifecycle in SQLite, enforces timeouts, and publishes
structured responses. It never talks to LoRa hardware.

## MQTT contract (v1)

| Direction | Topic                                  | Notes                                       |
| --------- | -------------------------------------- | ------------------------------------------- |
| in        | `meshcore/rpc/request`                 | RPC requests                                |
| out       | `meshcore/rpc/response/<node_id>`      | One topic per node                          |
| in        | `meshcore/gateway/status` (retained)   | Cached in memory + persisted                |
| in        | `meshcore/gateway/health` (retained)   | Cached in memory + persisted                |

All topic strings live in `meshcore_rpc_services/mqtt/topics.py`. Don't
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
- **`gateway.status`** — `{gw, hb, pending, ok, err, to}`. Gateway is the source of truth for gw/hb.
- **`node.last_seen`** — `{node, ts, age_s}` for the requester or another node via `args.node`.

Adding a handler: one new file in `handlers/`, append to `DEFAULT_HANDLERS`, add a test.

## Lifecycle

Requests move through these states, logged to the `request_events` table:

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

`transport/adapter.py` is where we translate between wire MQTT and internal `Request`/`Response`. Today the translation is essentially just JSON parse + pydantic validate, but the seam matters: the gateway's own RPC adapter may drift, and this is the one place we'd change to follow it.

## Install and run (local Python)

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml
meshcore-rpc-services initdb --config config.yaml
meshcore-rpc-services run    --config config.yaml
```

Env overrides use `__` between levels:
`MESHCORE_RPC_SERVICES_MQTT__HOST=broker.lan`,
`MESHCORE_RPC_SERVICES_SERVICE__TIMEOUTS__DEFAULT_S=60`.

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

## Retention

30-day default, configurable. The sweeper runs once at startup and then on
`retention.interval_s` (default 1 hour). `Store.purge_before(cutoff)`
deletes request rows whose `completed_at < cutoff`, their events, and
old gateway-snapshot rows. Run a one-shot purge:

```bash
meshcore-rpc-services purge --config config.yaml
meshcore-rpc-services purge --config config.yaml --days 7
```

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
    schema.sql        # requests, request_events, gateway_snapshots, nodes
    sqlite.py         # Store (single class, async surface)
  transport/
    bus.py            # aiomqtt wrapper
    adapter.py        # MQTT ↔ Request/Response
    service.py        # orchestrator
  handlers/
    base.py           # Handler protocol + HandlerContext (concrete Store)
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
```

## If Django ever shows up

When that happens, the refactor is roughly:

1. Create Django models matching `persistence/schema.sql`.
2. Replace `Store` with a Django-backed class with the same method signatures.
3. Turn `meshcore-rpc-services run` into a `manage.py` command.
4. Register the models in admin.

Handlers, `core`, `schemas`, `router`, `lifecycle`, and the adapter don't change. But that's a concrete refactor for when the need is real, not scaffolding built today.

## Out of scope

No HTTP API, no UI, no hiking/weather, no Django, no Celery, no direct
serial access. If it isn't an RPC handler over MQTT, it doesn't belong
here.
