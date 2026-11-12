"""
services/satellite_simulator/constellation.py

ConstellationManager — loads a constellation from a YAML config file,
instantiates one TelemetryGenerator per satellite, and generates all
telemetry snapshots each tick.

Sprint 5 delivery: enables all four constellation scenarios to run
from a single demo runner command.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass
from typing import Dict, List, Optional

from services.satellite_simulator.telemetry import TelemetryGenerator, FaultType
from shared.schemas.telemetry_schema import Telemetry


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SatelliteConfig:
    """Configuration for one satellite in the constellation."""
    satellite_id:     str
    plane_id:         str
    orbit_type:       str
    raan_deg:         float
    inclination_deg:  float
    altitude_km:      float
    mean_anomaly_deg: float
    eccentricity:     float = 0.001
    arg_perigee_deg:  float = 90.0
    apogee_altitude_km: Optional[float] = None   # set for eccentric (HEO) orbits

    @property
    def orbit_class(self) -> str:
        """'HEO' for Molniya satellites, otherwise 'LEO' (used for ISL range limits)."""
        return "HEO" if self.orbit_type == "HEO_MOLNIYA" else "LEO"


@dataclass
class ConstellationConfig:
    """Full constellation configuration loaded from a YAML file."""
    name:        str
    scenario:    int
    orbit_type:  str
    description: str
    satellites:  List[SatelliteConfig]

    @classmethod
    def from_yaml(cls, path: str) -> "ConstellationConfig":
        """
        Load and expand a constellation YAML file into individual
        SatelliteConfig objects.

        Each plane entry is expanded into N satellites spaced evenly
        in mean anomaly (360° / N spacing), shifted by the plane's optional
        ``mean_anomaly_offset_deg`` (needed for 1-satellite planes, which would
        otherwise all start at mean anomaly 0). A plane may set its own
        ``orbit_type`` (hybrid constellations); otherwise it inherits the
        file-level ``orbit_type``.
        """
        with open(path) as f:
            doc = yaml.safe_load(f)

        satellites = []
        constellation_orbit_type = doc.get("orbit_type", "LEO_CIRCULAR")

        for plane in doc["planes"]:
            n       = plane["satellites"]
            spacing = 360.0 / n

            for slot in range(n):
                sat_id = f"SAT-{plane['plane_id']}{slot + 1}"
                satellites.append(SatelliteConfig(
                    satellite_id     = sat_id,
                    plane_id         = plane["plane_id"],
                    orbit_type       = plane.get("orbit_type", constellation_orbit_type),
                    raan_deg         = plane["raan_deg"],
                    inclination_deg  = plane["inclination_deg"],
                    altitude_km      = plane.get("altitude_km",
                                        plane.get("perigee_altitude_km", 550.0)),
                    mean_anomaly_deg = (plane.get("mean_anomaly_offset_deg", 0.0)
                                        + slot * spacing) % 360.0,
                    eccentricity     = plane.get("eccentricity", 0.001),
                    arg_perigee_deg  = plane.get("arg_perigee_deg", 90.0),
                    apogee_altitude_km = plane.get("apogee_altitude_km"),
                ))

        return cls(
            name        = doc["name"],
            scenario    = doc["scenario"],
            orbit_type  = constellation_orbit_type,
            description = doc.get("description", ""),
            satellites  = satellites,
        )

    @property
    def satellite_count(self) -> int:
        return len(self.satellites)

    def orbit_classes(self) -> Dict[str, str]:
        """satellite_id -> 'LEO' | 'HEO', for compute_isl_topology(orbit_classes=...)."""
        return {s.satellite_id: s.orbit_class for s in self.satellites}


# ── Constellation manager ─────────────────────────────────────────────────────

class ConstellationManager:
    """
    Owns all TelemetryGenerator instances for a constellation.

    Usage:
        config  = ConstellationConfig.from_yaml("config/constellations/scenario_3_multi_orbit.yaml")
        manager = ConstellationManager(config, time_multiplier=60.0)

        while True:
            snapshots = manager.generate_all()
            for sat_id, telemetry in snapshots.items():
                publish_to_kafka(sat_id, telemetry)
            time.sleep(1.0 / config.satellite_count)
    """

    def __init__(
        self,
        config:          ConstellationConfig,
        time_multiplier: float = 1.0,
        fault_type:      str   = FaultType.NONE,
    ):
        self.config          = config
        self.time_multiplier = time_multiplier
        self.fault_type      = fault_type

        self._generators: Dict[str, TelemetryGenerator] = {
            sat.satellite_id: TelemetryGenerator(
                satellite_id     = sat.satellite_id,
                orbit_type       = sat.orbit_type,
                time_multiplier  = time_multiplier,
                fault_type       = fault_type,
                raan_deg         = sat.raan_deg,
                inclination_deg  = sat.inclination_deg,
                altitude_km      = sat.altitude_km,
                mean_anomaly_deg = sat.mean_anomaly_deg,
                arg_perigee_deg  = sat.arg_perigee_deg,
                apogee_altitude_km = sat.apogee_altitude_km,
            )
            for sat in config.satellites
        }

    def generate_all(self) -> Dict[str, Telemetry]:
        """
        Advance all satellites by one simulated tick.

        Returns a dict mapping satellite_id → Telemetry snapshot.
        All snapshots share the same simulated timestamp.
        """
        return {
            sat_id: gen.generate()
            for sat_id, gen in self._generators.items()
        }

    @property
    def satellite_ids(self) -> List[str]:
        return list(self._generators.keys())

    @property
    def satellite_count(self) -> int:
        return len(self._generators)

    def __repr__(self) -> str:
        return (
            f"ConstellationManager("
            f"scenario={self.config.scenario}, "
            f"satellites={self.satellite_count}, "
            f"orbit_type={self.config.orbit_type}, "
            f"speed={self.time_multiplier}×)"
        )
