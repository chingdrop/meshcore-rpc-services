# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-04-25

Initial release.

### Added

- MQTT RPC pipeline: receive → validate → route → execute → persist → emit
- Five built-in handlers: `ping`, `echo`, `time.now`, `gateway.status`, `node.last_seen`
- SQLite persistence with full request lifecycle and event log (`requests`, `request_events`, `gateway_snapshots`, `nodes`)
- Schema versioning via `schema_version` table with forward-migration framework
- `TimeoutPolicy` + `PendingTracker` for per-request TTL enforcement
- `RetentionSweeper` for periodic cleanup (default 30-day retention)
- CLI: `run`, `initdb`, `purge` subcommands with actionable config/DB error messages
- Structured log format with per-request `request_id`, `node_id`, `rpc_type` fields
- `gateway.status` response now includes `snap_age_s` (seconds since last retained message; null if cache is cold)
- Docker Compose test bench with Mosquitto broker
- Integration test suite gated behind `@pytest.mark.integration`
- Windows asyncio compatibility: `SelectorEventLoop` policy applied in test harness (required by paho-mqtt; `WindowsSelectorEventLoopPolicy` deprecated in Python 3.16 — track upstream fix)