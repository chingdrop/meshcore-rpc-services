-- SQLite schema for the application-layer service.
--
-- Shape is intentionally close to what a future Django models layout would
-- produce (one PK per table, typed columns, explicit indexes) so that a
-- migration to a Django backend is a port-swap, not a data reshape.

-- ---------------------------------------------------------------------------
-- Requests: one row per received request. (from, id) is globally unique and
-- used for duplicate detection.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS requests
(
    -- Surrogate key; kept TEXT for easy future move to UUIDs or natural keys.
    id
        TEXT
        NOT
            NULL,
    node_id
        TEXT
        NOT
            NULL,
    type
        TEXT
        NOT
            NULL,
    ttl_s
        INTEGER
        NOT
            NULL,
    received_at
        REAL
        NOT
            NULL,
    completed_at
        REAL,
    final_state
        TEXT, -- 'completed_ok' | 'completed_error'
    error_code
        TEXT, -- set when final_state='completed_error'
    request_json
        TEXT
        NOT
            NULL,
    response_json
        TEXT,
    -- The composite (node_id, id) is what makes a request unique across the
    -- whole system. SQLite enforces this with the UNIQUE index below and the
    -- code uses INSERT OR IGNORE to detect dupes cheaply.
    PRIMARY
        KEY
        (
         node_id,
         id
            )
);

CREATE INDEX IF NOT EXISTS idx_req_received ON requests (received_at);
CREATE INDEX IF NOT EXISTS idx_req_node ON requests (node_id);
CREATE INDEX IF NOT EXISTS idx_req_final_state ON requests (final_state);
CREATE INDEX IF NOT EXISTS idx_req_completed ON requests (completed_at);

-- ---------------------------------------------------------------------------
-- Append-only event log for state transitions. Useful for operational
-- forensics and will later power a Django admin timeline.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS request_events
(
    id
        INTEGER
        PRIMARY
            KEY
        AUTOINCREMENT,
    request_id
        TEXT
        NOT
            NULL, -- matches requests.id; not FK-enforced
    node_id
        TEXT
        NOT
            NULL, -- denormalized for easy joins / retention
    state
        TEXT
        NOT
            NULL,
    detail
        TEXT,
    ts
        REAL
        NOT
            NULL
);
CREATE INDEX IF NOT EXISTS idx_events_req ON request_events (request_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON request_events (ts);

-- ---------------------------------------------------------------------------
-- Gateway status/health snapshots. The bus writes every time the retained
-- topic changes. We keep a history for debugging.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gateway_snapshots
(
    id
        INTEGER
        PRIMARY
            KEY
        AUTOINCREMENT,
    ts
        REAL
        NOT
            NULL,
    status
        TEXT,
    health
        TEXT
);
CREATE INDEX IF NOT EXISTS idx_gw_snap_ts ON gateway_snapshots (ts);

-- ---------------------------------------------------------------------------
-- Lightweight node registry. Populated whenever a valid request is received.
-- Used by the node.last_seen handler.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nodes
(
    node_id
        TEXT
        PRIMARY
            KEY,
    last_seen
        REAL
        NOT
            NULL
);

-- ---------------------------------------------------------------------------
-- Schema version tracking. One row per applied version.
-- A missing row means the DB predates versioning; Store treats that as v0
-- and stamps it as v1 on first open.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version
(
    version INTEGER NOT NULL
);
