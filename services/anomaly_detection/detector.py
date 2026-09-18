"""
services/anomaly_detection/detector.py

Real-time anomaly detection service.

Consumes telemetry.{satellite_id} from Kafka, scores each record using
the appropriate Isolation Forest model (routed by orbit_type), and
publishes Alarm records to alarms.{satellite_id} when anomalies are
detected.

The model router enforces the LEO/HEO separation: LEO_CIRCULAR records
are scored by the LEO model; HEO_MOLNIYA records by the HEO model.
Mixing them would contaminate the scoring baseline (ADR-016).

Usage (after training a model):
    python -m services.anomaly_detection.detector
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import mlflow
import mlflow.sklearn
import numpy as np
from confluent_kafka import Consumer, Producer

from services.anomaly_detection.features import extract_features
from shared.config import settings
from shared.schemas.alarm_schema import Alarm
from shared.schemas.telemetry_schema import Telemetry

log = logging.getLogger(__name__)
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

# Anomaly score thresholds (Isolation Forest decision_function)
# Scores < threshold trigger alarms; more negative = more anomalous
THRESHOLD_WARNING  = -0.05   # mildly anomalous
THRESHOLD_CRITICAL = -0.15   # severely anomalous

MODEL_VERSION = "latest"


class ModelRouter:
    """
    Loads and caches one Isolation Forest model per orbit_type.

    Models are loaded lazily on first request.  If no trained model
    exists for an orbit_type, scoring falls back to a warning log
    and no alarm is published.
    """

    def __init__(self):
        self._models: dict = {}
        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)

    def score(self, telemetry: Telemetry) -> Optional[float]:
        """
        Score a telemetry record using the appropriate model.

        Returns the anomaly score (float), or None if no model is
        available for this orbit_type.
        """
        model = self._get_model(telemetry.orbit_type)
        if model is None:
            return None

        features = extract_features(telemetry).reshape(1, -1)
        score    = model.decision_function(features)[0]
        return float(score)

    def _get_model(self, orbit_type: str):
        if orbit_type in self._models:
            return self._models[orbit_type]

        model_name = f"ilmop-anomaly-{orbit_type.lower()}"
        try:
            model = mlflow.sklearn.load_model(f"models:/{model_name}/{MODEL_VERSION}")
            self._models[orbit_type] = model
            log.info("Model loaded | orbit_type=%s model=%s", orbit_type, model_name)
            return model
        except Exception as e:
            log.warning(
                "No model available for orbit_type=%s — anomaly detection disabled "
                "for this orbit type. Train a model first: "
                "python -m services.anomaly_detection.train --orbit-type %s | %s",
                orbit_type, orbit_type, e,
            )
            self._models[orbit_type] = None
            return None


def score_to_severity(score: float) -> Optional[str]:
    """Map an anomaly score to a severity label."""
    if score < THRESHOLD_CRITICAL:
        return "CRITICAL"
    elif score < THRESHOLD_WARNING:
        return "WARNING"
    return None   # normal — no alarm


def build_alarm(
    telemetry: Telemetry,
    score:     float,
    severity:  str,
) -> Alarm:
    """Construct an Alarm record from a scored Telemetry record."""

    # Identify the most anomalous parameter (heuristic: largest deviation
    # from nominal — a full per-feature analysis is a Sprint 6 enhancement)
    if telemetry.battery_pct < 20.0:
        parameter, observed = "battery_pct", telemetry.battery_pct
        expected_min, expected_max = 20.0, 100.0
    elif telemetry.temperature_c > 65.0 or telemetry.temperature_c < -35.0:
        parameter, observed = "temperature_c", telemetry.temperature_c
        expected_min, expected_max = -30.0, 60.0
    else:
        parameter, observed = "anomaly_score", score
        expected_min, expected_max = THRESHOLD_WARNING, 1.0

    return Alarm(
        satellite_id         = telemetry.satellite_id,
        timestamp            = telemetry.timestamp,
        orbit_type           = telemetry.orbit_type,
        severity             = severity,
        alarm_type           = "ANOMALY_SCORE",
        parameter            = parameter,
        observed_value       = observed,
        expected_min         = expected_min,
        expected_max         = expected_max,
        anomaly_score        = score,
        model_version        = MODEL_VERSION,
        message              = (
            f"{severity}: {parameter}={observed:.2f} "
            f"(score={score:.4f}) on {telemetry.satellite_id}"
        ),
        from_fault_injection = telemetry.fault_injected,
    )


class AnomalyDetectionService:
    """
    Kafka consumer that scores every telemetry record for anomalies.

    Subscribes to all telemetry topics via pattern subscription.
    Routes each record to the correct Isolation Forest model by orbit_type.
    Publishes Alarm records to alarms.{satellite_id} on anomaly detection.
    """

    def __init__(self):
        self._router = ModelRouter()
        # ── Eager preload — load known models on startup so the detector
        # is ready to score the very first record without a lazy-load delay.
        # Without this the model only loads when the first record of each
        # orbit_type arrives, creating a gap where records are consumed but
        # not scored.
        for orbit_type in ("LEO_CIRCULAR", "HEO_MOLNIYA"):
            self._router._get_model(orbit_type)

        self._consumer = Consumer({
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id":          "ilmop-anomaly-detection",
            "auto.offset.reset": "latest",
        })
        # Subscribe to all telemetry topics via regex pattern
        self._consumer.subscribe(
            [f"^{settings.kafka_telemetry_topic_prefix}\\..*"]
        )

        self._producer = Producer({
            "bootstrap.servers": settings.kafka_bootstrap_servers,
        })

        log.info(
            "AnomalyDetectionService ready | "
            "subscribed to telemetry.* | broker=%s",
            settings.kafka_bootstrap_servers,
        )

    def _parse(self, raw_bytes: bytes) -> Optional[Telemetry]:
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
            return Telemetry(**data)
        except Exception as e:
            log.warning("Failed to parse telemetry record: %s", e)
            return None

    def _publish_alarm(self, alarm: Alarm):
        topic   = settings.alarms_topic(alarm.satellite_id)
        payload = json.dumps(alarm.model_dump(mode="json")).encode("utf-8")
        self._producer.produce(
            topic = topic,
            key   = alarm.satellite_id.encode("utf-8"),
            value = payload,
        )
        self._producer.poll(0)
        log.warning(
            "ALARM [%s] | sat=%s param=%s val=%.2f score=%.4f fault=%s",
            alarm.severity, alarm.satellite_id,
            alarm.parameter, alarm.observed_value,
            alarm.anomaly_score, alarm.from_fault_injection,
        )

    def run(self):
        log.info("Starting anomaly detection loop …")
        try:
            while True:
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    log.error("Consumer error: %s", msg.error())
                    continue

                telemetry = self._parse(msg.value())
                if telemetry is None:
                    continue

                # Skip alarm generation for fault-injected records
                # (they are anomalous by design, not by model detection)
                if telemetry.fault_injected:
                    continue

                score = self._router.score(telemetry)
                if score is None:
                    continue   # no model available

                severity = score_to_severity(score)
                if severity:
                    alarm = build_alarm(telemetry, score, severity)
                    self._publish_alarm(alarm)

        except KeyboardInterrupt:
            log.info("Shutting down anomaly detection service …")
        finally:
            self._consumer.close()
            self._producer.flush(timeout=5)


if __name__ == "__main__":
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    service = AnomalyDetectionService()
    service.run()
