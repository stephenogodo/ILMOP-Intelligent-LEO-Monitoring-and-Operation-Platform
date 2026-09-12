"""
services/anomaly_detection/features.py

Feature engineering for the Isolation Forest anomaly detection model.

Features are selected to capture the eclipse-correlated physics that
makes ILMOP telemetry learnable. A model trained on random-walk data
learns nothing; a model trained on eclipse-correlated data learns that:
  - battery should charge in sunlight (solar_panel_power_w > 0)
  - battery should discharge in eclipse (solar_panel_power_w = 0)
  - temperature should follow eclipse state with thermal lag

The orbit_type field is used for model routing only — it is not a
feature, because including it would allow the model to trivially separate
LEO and HEO records rather than learning physics-based anomalies.
"""

import numpy as np
from shared.schemas.telemetry_schema import Telemetry


FEATURE_NAMES = [
    "battery_pct",
    "battery_voltage_v",
    "solar_panel_power_w",
    "temperature_c",
    "cpu_utilization_pct",
    "memory_utilization_pct",
    "in_eclipse_int",        # bool → 0/1
    "in_contact_int",        # bool → 0/1
    "battery_solar_product", # battery_pct * solar_panel_power_w (eclipse correlation)
    "temp_eclipse_product",  # temperature_c * in_eclipse_int
]


def extract_features(telemetry: Telemetry) -> np.ndarray:
    """
    Extract the feature vector for one Telemetry record.

    Returns a 1D numpy array of shape (len(FEATURE_NAMES),).
    """
    eclipse_int = 1 if telemetry.in_eclipse else 0
    contact_int = 1 if telemetry.in_contact else 0

    return np.array([
        telemetry.battery_pct,
        telemetry.battery_voltage_v,
        telemetry.solar_panel_power_w,
        telemetry.temperature_c,
        telemetry.cpu_utilization_pct,
        telemetry.memory_utilization_pct,
        eclipse_int,
        contact_int,
        telemetry.battery_pct * telemetry.solar_panel_power_w,
        telemetry.temperature_c * eclipse_int,
    ], dtype=np.float32)


def extract_features_batch(records: list[Telemetry]) -> np.ndarray:
    """
    Extract features for a list of Telemetry records.

    Returns a 2D numpy array of shape (N, len(FEATURE_NAMES)).
    """
    return np.vstack([extract_features(r) for r in records])
