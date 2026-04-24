# meshcore-rpc-services

Application-layer RPC services for a personal MeshCore + MQTT network.

This repo owns:

- subscribing to MQTT RPC requests
- validating request payloads
- routing to handlers
- enforcing timeouts
- tracking request lifecycle in SQLite
- publishing structured responses

It does **not** talk to MeshCore hardware. All I/O is via MQTT.

## MQTT contract (v1)

| Direction | Topic                                  | Notes                               |
| --------- | -------------------------------------- | ----------------------------------- |
| in        | `meshcore/rpc/request`                 | RPC requests from the gateway       |
| out       | `meshcore/rpc/response/<node_id>`      | Responses, one topic per node       |
| in        | `meshcore/gateway/status` (retained)   | Cached for `gateway.status` handler |
| in        | `meshcore/gateway/health` (retained)   | Cached for `gateway.status` handler |

> Note: the current `meshcore-mqtt` gateway uses `<prefix>/command/<type>` inbound and
> does not yet frame RPC. A thin RPC adapter must be added to the gateway so
> its outbound mesh replies land on `meshcore/rpc/request` and its mesh
> delivery consumes `meshcore/rpc/response/<node_id>`. This repo builds to the
> locked app-layer contract; the gateway-side bridge is out of scope here.

## Request / Response schemas

```json
// Request
{ "v": 1, "id": "abc123", "type": "ping", "from": "node-xyz", "ttl": 30, "args": {} }

// Success response
{ "v": 1, "id": "abc123", "type": "ping", "to": "node-xyz",
  "status": "ok", "body": {"message": "pong"} }

// Error response
{ "v": 1, "id": "abc123", "type": "ping", "to": "node-xyz",
  "status": "error", "error": {"code": "timeout", "message": "..."} }
```

Error codes: `bad_request`, `unknown_type`, `timeout`, `internal`.

## v1 handlers

- `ping` — returns `{"message": "pong"}`, optionally echoes `args.echo` (capped at 64 chars).
- `gateway.status` — returns a compact summary of gateway status/health (cached from retained topics) plus app-layer request counts (`pending`, `ok`, `err`, `to`).

## Install and run

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml  # edit as needed
python -m meshcore_rpc_services initdb --config config.yaml
python -m meshcore_rpc_services run    --config config.yaml
```

Env overrides: every setting is overridable via `MESHCORE_RPC_SERVICES_*`, e.g.
`MESHCORE_RPC_SERVICES_MQTT__HOST=broker.lan`.

## Tests

```bash
pytest
```

Tests cover schemas, persistence, router, timeouts, and both handlers. No
broker required — the MQTT bus is integration-tested separately (TODO).

## Layout

```
meshcore_rpc_services/
  config.py       # settings, YAML + env
  schemas.py      # Request / Response pydantic models
  errors.py       # error codes + RpcError
  persistence.py  # SQLite Store + AsyncStore
  mqtt_bus.py     # aiomqtt wrapper
  router.py       # type → handler
  timeouts.py     # pending tracker + TTL clamp
  service.py      # orchestrator — owns the lifecycle
  cli.py          # click: initdb, run
  handlers/
    base.py
    ping.py
    gateway_status.py
```

## Adding a handler

1. Create `meshcore_rpc_services/handlers/<name>.py` with a class exposing `type` and `async def handle(request, ctx)`, plus a module-level `handler` instance.
2. Import it in `meshcore_rpc_services/handlers/__init__.py` and append to `DEFAULT_HANDLERS`.
3. Add a test.

That's it. No framework changes needed.

## Deliberately out of scope

No HTTP API, no UI, no hiking features, no weather, no Celery, no direct
serial access. Don't add them here. If it isn't an RPC handler over MQTT, it
doesn't belong in this repo.
