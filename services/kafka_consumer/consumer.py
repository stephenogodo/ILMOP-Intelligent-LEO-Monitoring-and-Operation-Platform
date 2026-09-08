"""
services/kafka_consumer/consumer.py

General-purpose Kafka consumer for ILMOP telemetry — useful for debugging
and verifying the pipeline end-to-end.

Subscribes to  telemetry.{satellite_id}, validates every message against
the Telemetry Pydantic schema, and logs the result.  Schema violations are
logged as errors rather than crashing the consumer, so a bad message does
not interrupt the stream.

For production sinks (TimescaleDB, anomaly detection) see the dedicated
services under services/telemetry_sink/ and services/anomaly_detection/.
"""

import json
import logging

from confluent_kafka import Consumer, KafkaError, KafkaException
from pydantic import ValidationError

from shared.config import settings
from shared.schemas.telemetry_schema import Telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)


class TelemetryConsumer:
    """
    Consumes telemetry records from Kafka, validates against the Telemetry
    schema, and logs each record.
    """

    def __init__(self, satellite_id: str | None = None):
        self._satellite_id = satellite_id or settings.simulator_satellite_id
        self._topic        = settings.telemetry_topic(self._satellite_id)
        self._consumer     = Consumer({
            "bootstrap.servers":  settings.kafka_bootstrap_servers,
            "group.id":           settings.kafka_consumer_group_dashboard,
            "auto.offset.reset":  "earliest",
            "enable.auto.commit": True,
        })
        log.info("TelemetryConsumer ready | satellite=%s topic=%s",
                 self._satellite_id, self._topic)

    def run(self):
        """
        Continuously consume and validate telemetry records.
        Blocks until interrupted (KeyboardInterrupt / SIGINT).
        """
        self._consumer.subscribe([self._topic])
        log.info("Subscribed to %s", self._topic)

        try:
            while True:
                msg = self._consumer.poll(timeout=1.0)

                if msg is None:
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        log.debug("End of partition — waiting for new records")
                    else:
                        raise KafkaException(msg.error())
                    continue

                self._handle(msg)

        except KeyboardInterrupt:
            log.info("Interrupted — shutting down consumer …")
        finally:
            self._consumer.close()
            log.info("Consumer closed cleanly.")

    def _handle(self, msg):
        """Decode, validate, and log one Kafka message."""
        try:
            raw      = json.loads(msg.value().decode("utf-8"))
            telemetry = Telemetry(**raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.error("Failed to decode message | offset=%d error=%s",
                      msg.offset(), exc)
            return
        except ValidationError as exc:
            log.error("Schema validation failed | offset=%d errors=%s",
                      msg.offset(), exc.errors())
            return

        log.info(
            "Received | sat=%-8s  ts=%s  eclipse=%-5s  contact=%-5s  "
            "batt=%5.1f%%  temp=%+6.1f°C  solar=%6.1fW  "
            "cpu=%4.1f%%  dl=%.0f Mbps",
            telemetry.satellite_id,
            telemetry.timestamp.strftime("%H:%M:%S"),
            telemetry.in_eclipse,
            telemetry.in_contact,
            telemetry.battery_pct,
            telemetry.temperature_c,
            telemetry.solar_panel_power_w,
            telemetry.cpu_utilization_pct,
            telemetry.downlink_rate_mbps,
        )


if __name__ == "__main__":
    TelemetryConsumer().run()
