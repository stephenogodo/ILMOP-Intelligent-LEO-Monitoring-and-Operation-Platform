"""
services/alarm_sink/sink.py

Alarm sink — consumes alarms.{satellite_id} from Kafka and writes to
the TimescaleDB alarms table.

Mirrors the telemetry sink pattern exactly:
  - Wildcard subscription to all alarms.* topics
  - Manual Kafka offset commit after successful DB write
  - Batched writes with idempotent INSERT ON CONFLICT DO NOTHING
  - Graceful shutdown on KeyboardInterrupt

Usage:
    python -m services.alarm_sink.sink

Run alongside the anomaly detection service:
    Terminal 1: python -m services.anomaly_detection.detector
    Terminal 2: python -m services.alarm_sink.sink
"""

import json
import logging
import psycopg2
import psycopg2.extras
from confluent_kafka import Consumer, KafkaException

from shared.config import settings
from shared.schemas.alarm_schema import Alarm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

# ── Insert SQL ────────────────────────────────────────────────────────────────

INSERT_SQL = """
INSERT INTO alarms (
    timestamp, satellite_id, orbit_type,
    severity, alarm_type, parameter,
    observed_value, expected_min, expected_max,
    anomaly_score, model_version, message,
    from_fault_injection, schema_version
) VALUES (
    %(timestamp)s, %(satellite_id)s, %(orbit_type)s,
    %(severity)s, %(alarm_type)s, %(parameter)s,
    %(observed_value)s, %(expected_min)s, %(expected_max)s,
    %(anomaly_score)s, %(model_version)s, %(message)s,
    %(from_fault_injection)s, %(schema_version)s
)
ON CONFLICT DO NOTHING;
"""

BATCH_SIZE    = 20    # alarms are infrequent — smaller batches
BATCH_TIMEOUT = 5.0   # seconds


# ── Alarm sink ────────────────────────────────────────────────────────────────

class AlarmSink:
    """
    Kafka consumer that persists alarm records to TimescaleDB.

    Subscribes to all alarms.{satellite_id} topics via regex pattern.
    Commits the Kafka offset only after a successful database write,
    guaranteeing no alarm is silently lost on crash and restart.
    """

    def __init__(self):
        self._consumer = Consumer({
            "bootstrap.servers":  settings.kafka_bootstrap_servers,
            "group.id":           "ilmop-alarm-sink",
            "auto.offset.reset":  "earliest",
            "enable.auto.commit": False,
        })

        self._conn   = psycopg2.connect(settings.timescaledb_dsn)
        self._cursor = self._conn.cursor()

        log.info(
            "AlarmSink ready | subscribed to alarms.* | db=%s",
            settings.timescaledb_name,
        )

    def _parse(self, msg) -> dict | None:
        """Deserialise and validate one Kafka alarm message."""
        try:
            data  = json.loads(msg.value().decode("utf-8"))
            alarm = Alarm(**data)
            return {
                "timestamp":           alarm.timestamp,
                "satellite_id":        alarm.satellite_id,
                "orbit_type":          alarm.orbit_type,
                "severity":            alarm.severity,
                "alarm_type":          alarm.alarm_type,
                "parameter":           alarm.parameter,
                "observed_value":      alarm.observed_value,
                "expected_min":        alarm.expected_min,
                "expected_max":        alarm.expected_max,
                "anomaly_score":       alarm.anomaly_score,
                "model_version":       alarm.model_version,
                "message":             alarm.message,
                "from_fault_injection": alarm.from_fault_injection,
                "schema_version":      alarm.schema_version,
            }
        except Exception as exc:
            log.warning("Failed to parse alarm record: %s", exc)
            return None

    def _flush(self, batch: list[dict]) -> bool:
        """Write a batch of alarm records to TimescaleDB."""
        try:
            psycopg2.extras.execute_batch(self._cursor, INSERT_SQL, batch)
            self._conn.commit()
            log.info(
                "Flushed %d alarm(s) to TimescaleDB | "
                "severities=%s",
                len(batch),
                [r["severity"] for r in batch],
            )
            return True
        except Exception as exc:
            self._conn.rollback()
            log.error("DB write failed — batch rolled back | error=%s", exc)
            return False

    def run(self):
        self._consumer.subscribe(
            [f"^{settings.kafka_alarms_topic_prefix}\\..*"]
        )
        log.info("Subscribed to alarms.* — waiting for alarm records …")

        batch = []
        try:
            while True:
                messages = self._consumer.consume(
                    num_messages=BATCH_SIZE,
                    timeout=BATCH_TIMEOUT,
                )

                if not messages:
                    if batch:
                        if self._flush(batch):
                            self._consumer.commit()
                            batch = []
                    continue

                for msg in messages:
                    if msg.error():
                        from confluent_kafka import KafkaError
                        if msg.error().code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                            log.info(
                                "Alarm topics not yet created — detector has "
                                "not published any alarms yet. Retrying in %ss …",
                                BATCH_TIMEOUT,
                            )
                            break   # back to consume() loop — do not raise
                        raise KafkaException(msg.error())
                    record = self._parse(msg)
                    if record:
                        batch.append(record)

                if len(batch) >= BATCH_SIZE:
                    if self._flush(batch):
                        self._consumer.commit()
                        batch = []

        except KeyboardInterrupt:
            log.info("Interrupted — flushing final batch …")
            if batch:
                self._flush(batch)
        finally:
            self._consumer.close()
            self._cursor.close()
            self._conn.close()
            log.info("AlarmSink shut down cleanly.")


if __name__ == "__main__":
    AlarmSink().run()
