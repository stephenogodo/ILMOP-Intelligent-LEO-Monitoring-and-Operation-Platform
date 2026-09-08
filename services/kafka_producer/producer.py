"""
services/kafka_producer/producer.py

Kafka producer for ILMOP telemetry.

Reads telemetry from TelemetryGenerator, serialises each record to JSON,
and publishes to the topic  telemetry.{satellite_id}  using the satellite_id
as the message key.  Using satellite_id as the key guarantees that all
records for a given satellite land in the same partition and are processed
in order by any consumer of that partition.
"""

import json
import logging
import time

from confluent_kafka import Producer, KafkaException

from shared.config import settings
from services.satellite_simulator.telemetry import TelemetryGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)


def _delivery_report(err, msg):
    """Called by librdkafka once per message after delivery confirmation."""
    if err:
        log.error("Delivery failed | topic=%s key=%s error=%s",
                  msg.topic(), msg.key(), err)
    else:
        log.debug("Delivered | topic=%s partition=%d offset=%d key=%s",
                  msg.topic(), msg.partition(), msg.offset(), msg.key())


class TelemetryProducer:
    """
    Wraps a confluent_kafka.Producer and a TelemetryGenerator.
    Publishes one telemetry record per tick to the correct Kafka topic.
    """

    def __init__(self, satellite_id: str | None = None):
        self._satellite_id = satellite_id or settings.simulator_satellite_id
        self._topic        = settings.telemetry_topic(self._satellite_id)
        self._generator    = TelemetryGenerator(satellite_id=self._satellite_id)
        self._producer     = Producer({
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "acks": "all",           # wait for broker acknowledgement
            "retries": 3,
        })
        log.info("TelemetryProducer ready | satellite=%s topic=%s broker=%s",
                 self._satellite_id, self._topic, settings.kafka_bootstrap_servers)

    def run(self, interval_s: float | None = None):
        """
        Continuously generate and publish telemetry records.
        Blocks until interrupted (KeyboardInterrupt / SIGINT).
        """
        tick_s = interval_s or settings.simulator_tick_interval_s
        log.info("Starting telemetry stream | interval=%.1fs", tick_s)

        try:
            while True:
                telemetry = self._generator.generate()
                payload   = json.dumps(
                    telemetry.model_dump(mode="json")
                ).encode("utf-8")

                self._producer.produce(
                    topic    = self._topic,
                    key      = self._satellite_id.encode("utf-8"),
                    value    = payload,
                    callback = _delivery_report,
                )
                # Poll to trigger delivery callbacks without blocking
                self._producer.poll(0)

                log.info(
                    "Published | sat=%s eclipse=%s contact=%s batt=%.1f%%",
                    self._satellite_id,
                    telemetry.in_eclipse,
                    telemetry.in_contact,
                    telemetry.battery_pct,
                )
                time.sleep(tick_s)

        except KeyboardInterrupt:
            log.info("Interrupted — flushing remaining messages …")
        finally:
            self._producer.flush(timeout=10)
            log.info("Producer shut down cleanly.")


if __name__ == "__main__":
    TelemetryProducer().run()
