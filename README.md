# meshcore-rpc-services

Application-layer RPC services for a personal MeshCore + MQTT network.

The gateway (a separate component, `meshcore-mqtt`) bridges MeshCore ↔ MQTT.
This repo subscribes to RPC requests on MQTT, validates them, routes to
handlers, tracks lifecycle in SQLite, enforces timeouts, and publishes
structured responses. It never talks to LoRa hardware.

## MQTT contract (v1)

| Direction | Topic                             | Notes                        |
|-----------|-----------------------------------|------------------------------|
| in        | `mc/rpc/req`                      | RPC requests                 |
| out       | `mc/rpc/resp/<node_id>`           | One topic per node           |
| in        | `mc/gateway/status` (retained)    | Cached in memory + persisted |
| out       | `mc/svc/health` (retained)        | Service liveness             |
| out       | `mc/node/<id>/location` (retained)| Per-node GPS fix             |
| out       | `mc/node/<id>/battery` (retained) | Per-node battery             |
| out       | `mc/node/<id>/state` (retained)   | Per-node online/seen summary |
| out       | `mc/base/location` (retained)     | Base station GPS fix         |

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

Error codes: `bad_request`, `unknown_type`, `duplicate`, `timeout`, `internal`, `stale`, `unavailable`.

`ttl` and `args` are optional in requests. `ttl` defaults to the policy
`default_s`; `args` defaults to `{}`.

## v1 handlers

### `ping`

```json
{ "msg": "pong" }
```

Pass `args.echo` (≤ 64 chars) to get it reflected back alongside `msg`:
`{ "msg": "pong", "echo": "your text" }`.

### `echo`

```json
{ "msg": "<args.msg truncated to 180 chars>" }
```

### `time.now`

```json
{ "ts": 1714000000.0, "iso": "2024-04-25T00:00:00Z" }
```

### `gateway.status`

```json
{ "state": "connected", "detail": null, "since": 1714000000.0, "snap_age_s": 42, "pending": 1, "ok": 18, "err": 2, "to": 0 }
```

| Field        | Type       | Description                                                            |
|--------------|------------|------------------------------------------------------------------------|
| `state`      | string     | Last retained `mc/gateway/status` state field, or `"unknown"`          |
| `detail`     | string/null| Optional detail string from the gateway status message                 |
| `since`      | float/null | Unix timestamp when the gateway entered this state                     |
| `snap_age_s` | int / null | Seconds since cache was last updated; `null` if no message received yet |
| `pending`    | int        | Requests with no `final_state` yet                                     |
| `ok`         | int        | `completed_ok` count (all time)                                        |
| `err`        | int        | `completed_error` count (all time)                                     |
| `to`         | int        | Timeout count (all time)                                               |

When `snap_age_s` is `null` the gateway cache is cold — `state` should
be treated as unknown regardless of its value.

### `node.last_seen`

```json
{ "node": "node-abc", "ts": 1714000000.0, "age_s": 120 }
```

`ts` and `age_s` are `null` if the node has never been seen. Defaults to the
requester's own node id; pass `args.node` to query another node.

### `node.location.report`

Report the caller's current GPS fix. The service persists it and publishes
retained state to `mc/node/<id>/location` and `mc/node/<id>/state`.

```json
// args
{ "lat": 27.94, "lon": -82.29, "ts": 1714000000.0, "alt": 15.5, "acc": 3.0, "fix": 3 }

// response
{ "ack": true, "ts": 1714000000.0 }
```

`lat` and `lon` are required. `ts` defaults to server time if absent. All other
fields are optional. `lat` must be in `[-90, 90]`; `lon` in `[-180, 180]`.

### `node.location`

Query the last known location for a node.

```json
// args (optional)
{ "node": "node-abc" }

// response
{ "node": "node-abc", "lat": 27.94, "lon": -82.29, "ts": 1714000000.0, "age_s": 42 }
```

Defaults to the requester. Returns `unavailable` if the node has never reported
a location.

### `node.status`

Query aggregated status for a node.

```json
// args (optional)
{ "node": "node-abc" }

// response
{ "id": "node-abc", "online": true, "last_seen_age_s": 42, "bat_pct": 85 }
```

`online` is `true` if the node was seen within the last 5 minutes. `bat_pct`
is omitted if battery has never been reported. Returns `unavailable` if the
node has never been seen.

### `base.location`

Query the base station's last GPS fix.

```json
{ "lat": 27.77, "lon": -82.64, "ts": 1714000000.0, "age_s": 12, "fix": 3 }
```

Returns `unavailable` if no fix has been set, or `stale` if the fix is older
than 10 minutes. Configure a static base position in `config.yaml`:

```yaml
service:
  base:
    source: static
    static_lat: 27.77
    static_lon: -82.64
```

### `return_to_base`

Compute bearing and distance from the caller's position to the base.

```json
// args (optional — omit to use last reported position)
{ "lat": 27.94, "lon": -82.29 }

// response
{
  "bearing": 218,
  "dist_m":  26700,
  "base": { "lat": 27.77, "lon": -82.64, "age_s": 12 },
  "from": { "lat": 27.94, "lon": -82.29, "age_s": 0  }
}
```

`bearing` is the initial compass bearing toward the base (0–359°). `dist_m` is
the great-circle distance in metres. Returns `unavailable` if either position
is unknown, or `stale` if either fix is older than 10 minutes. Passing explicit
`lat`/`lon` in args bypasses the staleness check on the caller's stored fix.

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
MqttBus  ──subscribes──>  mc/rpc/req
   │                      mc/gateway/status  (retained → cached + persisted)
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
python scripts/seed_gateway_status.py --state connected
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
  state.py            # StateAggregator — node/base location, battery, online flag
  geo.py              # haversine + bearing (pure functions)
  cli.py              # run / initdb / purge
  mqtt/topics.py      # topic string constants
  timeouts/
    policy.py         # TimeoutPolicy
    tracker.py        # PendingTracker
  persistence/
    schema.sql        # requests, request_events, gateway_snapshots, nodes,
                      # node_locations, node_battery, base_state, schema_version
    sqlite.py         # Store (single class, async surface)
  transport/
    bus.py            # aiomqtt wrapper + gateway snapshot cache
    adapter.py        # MQTT ↔ Request/Response
    service.py        # orchestrator + event routing
  handlers/
    base.py           # Handler protocol + HandlerContext
    ping.py / echo.py / time_now.py
    gateway_status.py / node_last_seen.py
    node_location_report.py / node_location.py / node_status.py
    base_location.py / return_to_base.py
scripts/
  send_request.py        # publish a request, print the response
  seed_gateway_status.py # publish retained gateway state
tests/
  conftest.py            # store + state + ctx fixtures
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

## Deployment on the Pi

Full walkthrough (OS packages, user setup, systemd units, udev rule for
the gateway's USB serial port, GPSD, troubleshooting): see
[`deploy/DEPLOY.md`](deploy/DEPLOY.md).

If you want to skip the prose:

```bash
cd ~/meshcore-rpc-services
sudo bash deploy/install.sh ~/meshcore-mqtt
```

Then edit `/etc/meshcore-rpc-services/config.yaml`,
`/etc/meshcore-mqtt/config.yaml`, and the udev rule template at
`/etc/udev/rules.d/99-meshcore-gateway.rules`, and start the units.

The unit files, install script, and udev rule live under
[`deploy/`](deploy/) in this repo. The gateway repo is referenced by
path during install — the install script handles both repos. Override
the database path for system deployment:

```yaml
service:
  db_path: /var/lib/meshcore-rpc-services/state.sqlite3
```

### TAK / ATAK bridge

A separate process — `meshcore-tak-bridge` — translates the retained
`mc/node/+/{location,state}` and `mc/base/location` topics into
Cursor-on-Target events and streams them over TCP to a TAK Server on the
LAN. It ships in this repo (under `meshcore_rpc_services/tak/`) but runs
as its own console_script with its own systemd unit. It does not touch
the radio, the gateway, or this service's database; it is a read-only
consumer of the same MQTT contract everything else is built on.

Topology:

```
mc/node/+/location  ─┐
mc/node/+/state     ─┼─→  meshcore-tak-bridge  ─→  CoT-over-TCP  ─→  TAK Server
mc/base/location    ─┘
```

#### Why it's in this repo

Same dependencies (aiomqtt, pydantic, click), same runtime model
(asyncio), same deploy target (Pi or sibling box on the home LAN),
shared MQTT topic constants. Splitting them into separate repos meant
versioning, releasing, and coordinating two things that always move
together. They run as separate processes — that's the boundary that
matters — but they live in one source tree.

#### Configuration

The bridge reads the same `config.yaml` as the RPC service. The `tak:`
section is bridge-specific; the service ignores it. See
`config.example.yaml` for the full set of knobs.

```yaml
tak:
  server:
    host: "192.168.1.50"   # LAN, OR a WireGuard IP for remote TAK Servers
    port: 8087
  callsign_template: "MC-{id}"
  publish_interval_s: 10.0
  stale_after_s: 300
```

`tak.server.host` can be any IP the Pi can reach. If your TAK Server is
on a home network and the Pi is in the field, set up a WireGuard tunnel
on the Pi and point this at the WG-side address. The bridge's reconnect
logic (with stale-queue-drain on reconnect) is designed for the kind of
flapping you get over Starlink/cellular tunnels. See
[`deploy/DEPLOY.md`](deploy/DEPLOY.md) for the full WireGuard setup.

#### Running

```bash
meshcore-tak-bridge --config /etc/meshcore-rpc-services/config.yaml
```

#### Systemd

The unit file is installed by `deploy/install.sh`. See
[`deploy/DEPLOY.md`](deploy/DEPLOY.md) for the full deployment walk-through;
the unit file itself is at
[`deploy/systemd/meshcore-tak-bridge.service`](deploy/systemd/meshcore-tak-bridge.service).

#### CoT details

* `uid` = `meshcore.<id>` — namespaced so we don't collide with other
  CoT sources on the same TAK server.
* `type` = `a-f-G-U-C` (friendly, ground, unit, combat) for field nodes,
  `a-f-G-U-C-I` for the base. Configurable.
* `how` = `m-g` — machine, GPS-derived. Correct when the field node sent
  us its own GPS.
* `point/hae`, `ce`, `le` — populated when known; sentinel `9999999.0`
  per CoT convention when not.
* `detail/contact/@callsign` — what shows on the marker in ATAK.
* `detail/track/@speed,@course` — when the field node reported them.
* `detail/remarks` — free-text. We pack `battery=N%; rssi=-X; snr=Y`
  here; ATAK surfaces it in the marker detail popup.

#### Out of scope (v1)

* TLS to the TAK Server. Plain TCP only. Add `ssl_context` in
  `takserver._session` when needed.
* TAK → mesh inbound. The bridge is one-way. Adding a CoT input source
  would go in this same package; the gateway and RPC service would not
  need changes.
* Per-role CoT type mapping. Every field node currently gets the same
  type. Adding a per-id override map in `tak:` is straightforward when
  you want different markers for different roles.

### Service health

On startup the service publishes `{"state": "running", "ts": <unix>}` to
`mc/svc/health` (retained). A heartbeat republishes every 30 seconds. On
graceful shutdown it publishes `{"state": "stopped", "ts": <unix>}` before
disconnecting. A stale `ts` (older than ~60 s) indicates the service is not
running or the broker lost the connection.

### Offline operation

Nothing in this stack reaches the public internet. The service runs correctly
with no upstream connectivity — it only needs the local MQTT broker.

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
