"""
services/telemetry_sink/sink.py

TimescaleDB sink — consumes telemetry records from Kafka and persists
them to the TimescaleDB hypertable defined in schema.sql.

Design decisions
────────────────
- Uses psycopg2 (synchronous) rather than asyncpg: the sink is a
  dedicated consumer process with a single blocking consumer loop;
  async adds complexity without benefit here.
- INSERT … ON CONFLICT DO NOTHING: idempotent writes — if a record is
  consumed twice (Kafka at-least-once delivery), the duplicate is
  silently dropped rather than raising an error.
- Batch commits every BATCH_SIZE records or BATCH_TIMEOUT_S seconds,
  whichever comes first, to balance write throughput with data freshness.
- Schema validation via Pydantic before every write: a malformed record
  is logged and skipped; it never reaches the database.
"""

import json
import logging
import time
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from confluent_kafka import Consumer, KafkaError, KafkaException
from pydantic import ValidationError

from shared.config import settings
from shared.schemas.telemetry_schema import Telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────
BATCH_SIZE      = 50     # commit after this many records
BATCH_TIMEOUT_S = 5.0    # or after this many seconds, whichever is first

# ── SQL ───────────────────────────────────────────────────────────────────────
INSERT_SQL = """
INSERT INTO telemetry (
    time, satellite_id,
    latitude_deg, longitude_deg, altitude_km,
    in_eclipse, in_contact,
    orbit_type,
    battery_pct, battery_voltage_v, solar_panel_power_w,
    temperature_c,
    cpu_utilization_pct, memory_utilization_pct,
    downlink_rate_mbps, uplink_rate_mbps,
    safe_mode, anomaly_flag, fault_injected,
    schema_version
) VALUES (
    %(time)s, %(satellite_id)s,
    %(latitude_deg)s, %(longitude_deg)s, %(altitude_km)s,
    %(in_eclipse)s, %(in_contact)s,
    %(orbit_type)s,
    %(battery_pct)s, %(battery_voltage_v)s, %(solar_panel_power_w)s,
    %(temperature_c)s,
    %(cpu_utilization_pct)s, %(memory_utilization_pct)s,
    %(downlink_rate_mbps)s, %(uplink_rate_mbps)s,
    %(safe_mode)s, %(anomaly_flag)s, %(fault_injected)s,
    %(schema_version)s
)
ON CONFLICT DO NOTHING;
"""


class TelemetrySink:
    """
    Kafka consumer that persists validated telemetry records to TimescaleDB.
    """

    def __init__(self):
        pass   # all config via settings; topic pattern in run()

        self._consumer = Consumer({
            "bootstrap.servers":  settings.kafka_bootstrap_servers,
            "group.id":           settings.kafka_consumer_group_sink,
            "auto.offset.reset":  "earliest",
            "enable.auto.commit": False,   # manual commit after DB write
        })

        self._conn   = psycopg2.connect(settings.timescaledb_dsn)
        self._cursor = self._conn.cursor()

        log.info("TelemetrySink ready | subscribed to telemetry.* | db=%s",
                 settings.timescaledb_name)

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self):
        """
        Consume, validate, and persist telemetry records continuously.
        Commits to the database and Kafka in batches.
        """
        self._consumer.subscribe(
            [rf"^{settings.kafka_telemetry_topic_prefix}\..*"]
        )
        log.info("Subscribed to telemetry.* — waiting for records …")

        batch        = []
        batch_start  = time.monotonic()

        try:
            while True:
                msg = self._consumer.poll(timeout=1.0)

                if msg is None:
                    # No message — check if batch timeout has elapsed
                    if batch and (time.monotonic() - batch_start) >= BATCH_TIMEOUT_S:
                        self._flush(batch)
                        batch       = []
                        batch_start = time.monotonic()
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise KafkaException(msg.error())

                record = self._parse(msg)
                if record:
                    batch.append(record)

                if len(batch) >= BATCH_SIZE or \
                   (batch and (time.monotonic() - batch_start) >= BATCH_TIMEOUT_S):
                    self._flush(batch)
                    self._consumer.commit(asynchronous=False)
                    batch       = []
                    batch_start = time.monotonic()

        except KeyboardInterrupt:
            log.info("Interrupted — flushing final batch …")
            if batch:
                self._flush(batch)
        finally:
            self._cursor.close()
            self._conn.close()
            self._consumer.close()
            log.info("Sink shut down cleanly.")

    # ── Private ───────────────────────────────────────────────────────────────

    def _parse(self, msg) -> dict | None:
        """Decode and validate one Kafka message. Returns None on failure."""
        try:
            raw      = json.loads(msg.value().decode("utf-8"))
            tel      = Telemetry(**raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.error("Decode error | offset=%d error=%s", msg.offset(), exc)
            return None
        except ValidationError as exc:
            log.error("Schema error | offset=%d errors=%s",
                      msg.offset(), exc.errors())
            return None

        return {
            "time":                   tel.timestamp,
            "satellite_id":           tel.satellite_id,
            "latitude_deg":           tel.latitude_deg,
            "longitude_deg":          tel.longitude_deg,
            "altitude_km":            tel.altitude_km,
            "in_eclipse":             tel.in_eclipse,
            "in_contact":             tel.in_contact,
            "battery_pct":            tel.battery_pct,
            "battery_voltage_v":      tel.battery_voltage_v,
            "solar_panel_power_w":    tel.solar_panel_power_w,
            "temperature_c":          tel.temperature_c,
            "cpu_utilization_pct":    tel.cpu_utilization_pct,
            "memory_utilization_pct": tel.memory_utilization_pct,
            "downlink_rate_mbps":     tel.downlink_rate_mbps,
            "uplink_rate_mbps":       tel.uplink_rate_mbps,
            "orbit_type":             tel.orbit_type,
            "safe_mode":              tel.safe_mode,
            "anomaly_flag":           tel.anomaly_flag,
            "fault_injected":         tel.fault_injected,
            "schema_version":         "2.1",
        }

    def _flush(self, batch: list[dict]):
        """Write a batch of records to TimescaleDB."""
        try:
            psycopg2.extras.execute_batch(self._cursor, INSERT_SQL, batch)
            self._conn.commit()
            log.info("Flushed %d records to TimescaleDB", len(batch))
        except Exception as exc:
            self._conn.rollback()
            log.error("DB write failed — batch rolled back | error=%s", exc)


if __name__ == "__main__":
    TelemetrySink().run()
