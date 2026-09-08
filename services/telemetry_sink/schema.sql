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

    -- Schema version — ties every row to the Telemetry schema it was
    -- written from; essential for Sprint 5 MLflow model versioning
    schema_version          TEXT            DEFAULT '2.0'
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
