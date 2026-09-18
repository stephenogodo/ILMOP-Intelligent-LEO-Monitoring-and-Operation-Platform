"""
services/anomaly_detection/train.py

Train Isolation Forest anomaly detection models for ILMOP telemetry.

One model per orbit_type is trained and logged to MLflow.  The training
pipeline enforces the LEO/HEO separation that prevents training data
contamination (ADR-016).

Usage:
    # Generate training data first (run for several minutes):
    python run_demo.py --scenario 1 --speed 60

    # Then train:
    python -m services.anomaly_detection.train --orbit-type LEO_CIRCULAR
    python -m services.anomaly_detection.train --orbit-type HEO_MOLNIYA
"""

import argparse
import logging
import os

import mlflow
import mlflow.sklearn
import numpy as np
import psycopg2
import psycopg2.extras
from sklearn.ensemble import IsolationForest
from sklearn.model_selection import train_test_split

from services.anomaly_detection.features import extract_features_batch, FEATURE_NAMES
from shared.config import settings
from shared.schemas.telemetry_schema import Telemetry

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Training SQL — filters by orbit_type for clean separation ─────────────────

TRAINING_SQL = """
    SELECT
        satellite_id, time AS timestamp,
        latitude_deg, longitude_deg, altitude_km,
        in_eclipse, in_contact, orbit_type,
        battery_pct, battery_voltage_v, solar_panel_power_w,
        temperature_c, cpu_utilization_pct, memory_utilization_pct,
        downlink_rate_mbps, uplink_rate_mbps,
        safe_mode, anomaly_flag, fault_injected
    FROM telemetry
    WHERE orbit_type = %s
      AND fault_injected = FALSE
      AND safe_mode = FALSE
    ORDER BY time ASC
"""


def load_training_data(orbit_type: str) -> list[Telemetry]:
    """Load normal (non-faulted) telemetry for a given orbit type."""
    conn = psycopg2.connect(settings.timescaledb_url)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(TRAINING_SQL, (orbit_type,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    log.info("Loaded %d training records for orbit_type=%s", len(rows), orbit_type)

    records = []
    for row in rows:
        data = dict(row)
        data["timestamp"] = data["timestamp"].isoformat() \
            if hasattr(data["timestamp"], "isoformat") else data["timestamp"]
        try:
            records.append(Telemetry(**data))
        except Exception as e:
            log.warning("Skipping invalid record: %s", e)

    return records


def train(orbit_type: str, contamination: float = 0.01):
    """
    Train an Isolation Forest model for the given orbit type.

    contamination — expected fraction of anomalies in the training data.
    Set to 0.05 (5%) to account for edge-case physics that might look
    unusual but are not true anomalies.
    """
    mlflow.set_experiment(f"ilmop-anomaly-detection-{orbit_type.lower()}")

    with mlflow.start_run():
        # Log all key parameters for reproducibility
        mlflow.set_tags({
            "orbit_type":     orbit_type,
            "schema_version": "2.1",
            "model_type":     "IsolationForest",
        })
        mlflow.log_params({
            "contamination":     contamination,
            "n_estimators":      100,
            "max_samples":       "auto",
            "random_state":      42,
            "feature_count":     len(FEATURE_NAMES),
            "features":          ",".join(FEATURE_NAMES),
        })

        # Load and validate training data
        records = load_training_data(orbit_type)
        if len(records) < 100:
            log.error(
                "Insufficient training data: %d records (need ≥ 100). "
                "Run the simulator longer before training.",
                len(records),
            )
            return None

        mlflow.log_metric("training_records", len(records))

        # Extract features
        X = extract_features_batch(records)
        log.info("Feature matrix shape: %s", X.shape)

        # Split for held-out evaluation
        X_train, X_val = train_test_split(X, test_size=0.2, random_state=42)

        # Train Isolation Forest
        model = IsolationForest(
            n_estimators  = 100,
            contamination = contamination,
            max_samples   = "auto",
            random_state  = 42,
            n_jobs        = -1,
        )
        model.fit(X_train)
        log.info("Model trained on %d samples", len(X_train))

        # Evaluate on validation set
        val_scores  = model.decision_function(X_val)
        val_preds   = model.predict(X_val)
        anomaly_pct = (val_preds == -1).sum() / len(val_preds) * 100

        mlflow.log_metrics({
            "val_anomaly_pct":       round(anomaly_pct, 2),
            "val_score_mean":        round(float(val_scores.mean()), 4),
            "val_score_std":         round(float(val_scores.std()), 4),
            "val_score_min":         round(float(val_scores.min()), 4),
            "training_samples":      len(X_train),
            "validation_samples":    len(X_val),
        })

        log.info(
            "Validation: %.1f%% flagged as anomalies (target: ~%.0f%%)",
            anomaly_pct, contamination * 100,
        )

        # Log model artifact to MLflow
        mlflow.sklearn.log_model(
            model,
            artifact_path = "model",
            registered_model_name = f"ilmop-anomaly-{orbit_type.lower()}",
        )

        run_id = mlflow.active_run().info.run_id
        log.info("Model logged to MLflow | run_id=%s", run_id)
        return model


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--orbit-type",
        choices=["LEO_CIRCULAR", "HEO_MOLNIYA"],
        default="LEO_CIRCULAR",
    )
    parser.add_argument("--contamination", type=float, default=0.01)
    args = parser.parse_args()

    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    train(args.orbit_type, args.contamination)
