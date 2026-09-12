-- schema.sql — TimescaleDB schema for ILMOP telemetry
--
-- Run once against the ilmop database to initialise the schema.
-- The CREATE TABLE and create_hypertable() calls are idempotent
-- (IF NOT EXISTS) so this script is safe to re-run.

-- Enable TimescaleDB extension (already present in the Docker image)
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── Main telemetry table ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS telemetry (

    -- Time and identity
    time                    TIMESTAMPTZ     NOT NULL,
    satellite_id            TEXT            NOT NULL,

    -- Orbital state
    latitude_deg            DOUBLE PRECISION,
    longitude_deg           DOUBLE PRECISION,
    altitude_km             DOUBLE PRECISION,
    in_eclipse              BOOLEAN,
    in_contact              BOOLEAN,

    -- Power subsystem
    battery_pct             DOUBLE PRECISION,
    battery_voltage_v       DOUBLE PRECISION,
    solar_panel_power_w     DOUBLE PRECISION,

    -- Thermal subsystem
    temperature_c           DOUBLE PRECISION,

    -- Compute subsystem
    cpu_utilization_pct     DOUBLE PRECISION,
    memory_utilization_pct  DOUBLE PRECISION,

    -- Communications
    downlink_rate_mbps      DOUBLE PRECISION,
    uplink_rate_mbps        DOUBLE PRECISION,

    -- Status flags
    safe_mode               BOOLEAN         DEFAULT FALSE,
    anomaly_flag            BOOLEAN         DEFAULT FALSE,
    fault_injected          BOOLEAN         DEFAULT FALSE,

    -- Orbit classification — gates ML model routing and training data separation
    -- Values: 'LEO_CIRCULAR' (Scenarios 1-3) | 'HEO_MOLNIYA' (Scenario 4)
    orbit_type              TEXT            DEFAULT 'LEO_CIRCULAR',

    -- Schema version — ties every row to the Telemetry schema it was
    -- written from; essential for Sprint 5 MLflow model versioning
    schema_version          TEXT            DEFAULT '2.1'
);

-- Convert to a TimescaleDB hypertable partitioned on time
-- (partitions data into chunks automatically — no manual partition management)
SELECT create_hypertable(
    'telemetry', 'time',
    if_not_exists => TRUE
);

-- Primary access pattern: all records for satellite X in a time range
CREATE INDEX IF NOT EXISTS idx_telemetry_sat_time
    ON telemetry (satellite_id, time DESC);

-- Secondary access pattern: all eclipse periods across the fleet
CREATE INDEX IF NOT EXISTS idx_telemetry_eclipse
    ON telemetry (in_eclipse, time DESC)
    WHERE in_eclipse = TRUE;

-- Secondary access pattern: all contact windows across the fleet
CREATE INDEX IF NOT EXISTS idx_telemetry_contact
    ON telemetry (in_contact, time DESC)
    WHERE in_contact = TRUE;

-- ML training data access pattern: filter by orbit type for clean datasets
CREATE INDEX IF NOT EXISTS idx_telemetry_orbit_type
    ON telemetry (orbit_type, time DESC);

-- ── Continuous aggregate: 5-minute telemetry summary ─────────────────────────
-- Pre-computes per-satellite averages over 5-minute buckets.
-- Used by the Sprint 4 dashboard trending charts.
CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_5min
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('5 minutes', time)  AS bucket,
    satellite_id,
    AVG(battery_pct)                AS avg_battery_pct,
    AVG(battery_voltage_v)          AS avg_battery_voltage_v,
    AVG(solar_panel_power_w)        AS avg_solar_power_w,
    AVG(temperature_c)              AS avg_temperature_c,
    AVG(cpu_utilization_pct)        AS avg_cpu_pct,
    AVG(memory_utilization_pct)     AS avg_memory_pct,
    AVG(altitude_km)                AS avg_altitude_km,
    BOOL_OR(in_eclipse)             AS any_eclipse,
    BOOL_OR(in_contact)             AS any_contact,
    COUNT(*)                        AS sample_count
FROM telemetry
GROUP BY bucket, satellite_id
WITH NO DATA;

-- Refresh policy: keep the 5-min aggregate up to date automatically
SELECT add_continuous_aggregate_policy(
    'telemetry_5min',
    start_offset  => INTERVAL '1 hour',
    end_offset    => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);


-- ── Alarms table (Sprint 5) ──────────────────────────────────────────────────
-- Stores anomaly detection alarms produced by the Isolation Forest service.
CREATE TABLE IF NOT EXISTS alarms (
    timestamp            TIMESTAMPTZ     NOT NULL,
    satellite_id         TEXT            NOT NULL,
    orbit_type           TEXT            DEFAULT 'LEO_CIRCULAR',
    severity             TEXT            NOT NULL,
    alarm_type           TEXT            NOT NULL,
    parameter            TEXT,
    observed_value       DOUBLE PRECISION,
    expected_min         DOUBLE PRECISION,
    expected_max         DOUBLE PRECISION,
    anomaly_score        DOUBLE PRECISION,
    model_version        TEXT,
    message              TEXT,
    from_fault_injection BOOLEAN         DEFAULT FALSE,
    schema_version       TEXT            DEFAULT '1.0'
);

SELECT create_hypertable(
    'alarms', 'timestamp',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_alarms_sat_time
    ON alarms (satellite_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_alarms_severity
    ON alarms (severity, timestamp DESC);

-- ── Storage management ────────────────────────────────────────────────────────
-- Prevents unbounded disk growth during high-speed training data generation.
-- Run automatically by TimescaleDB on a background schedule.

-- Retention policies: drop chunks older than 7 days automatically
SELECT add_retention_policy('telemetry', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('alarms',    INTERVAL '7 days', if_not_exists => TRUE);

-- Compression: reduce storage by 90-95% for chunks older than 2 hours
ALTER TABLE telemetry SET (
    timescaledb.compress,
    timescaledb.compress_orderby   = 'time DESC',
    timescaledb.compress_segmentby = 'satellite_id, orbit_type'
);
SELECT add_compression_policy('telemetry', INTERVAL '2 hours', if_not_exists => TRUE);