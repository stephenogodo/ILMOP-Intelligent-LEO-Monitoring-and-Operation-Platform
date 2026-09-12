"""
services/satellite_simulator/telemetry.py

Orchestrates all sub-models and produces a validated Telemetry snapshot
each tick.  Sprint 5 additions:
  - orbit_type parameter  — LEO_CIRCULAR or HEO_MOLNIYA (gates ML separation)
  - time_multiplier       — advance simulated time faster than wall-clock
  - fault_injection       — inject synthetic anomalies for ML training
"""

import random
from datetime import datetime, timezone, timedelta
from typing import Literal

from shared.schemas.telemetry_schema import Telemetry
from services.satellite_simulator.satellite import Satellite
from services.satellite_simulator.orbit import OrbitModel
from services.satellite_simulator.battery import BatteryModel
from services.satellite_simulator.thermal import ThermalModel


# ── Supporting models ─────────────────────────────────────────────────────────

class _ComputeModel:
    """
    CPU and memory utilisation random-walk model.

    CPU usage trends higher during contact passes, when the OBC is actively
    managing downlink encoding and scheduling.
    """
    CPU_BASELINE_PCT     = 35.0
    CPU_CONTACT_PCT      = 60.0
    CPU_NOISE_STD        =  1.0
    CPU_LAG              =  0.2
    CPU_MIN, CPU_MAX     =  5.0, 95.0

    MEM_BASELINE_PCT     = 40.0
    MEM_NOISE_STD        =  0.3
    MEM_MIN, MEM_MAX     = 20.0, 85.0

    def __init__(self):
        self.cpu_pct = self.CPU_BASELINE_PCT
        self.mem_pct = self.MEM_BASELINE_PCT

    def update(self, in_contact: bool):
        target = self.CPU_CONTACT_PCT if in_contact else self.CPU_BASELINE_PCT
        self.cpu_pct = max(self.CPU_MIN, min(self.CPU_MAX,
            self.cpu_pct
            + self.CPU_LAG * (target - self.cpu_pct)
            + random.gauss(0.0, self.CPU_NOISE_STD)
        ))
        self.mem_pct = max(self.MEM_MIN, min(self.MEM_MAX,
            self.mem_pct + random.gauss(0.0, self.MEM_NOISE_STD)
        ))
        return self.cpu_pct, self.mem_pct


class _ContactModel:
    """
    Simplified ground-station contact window simulation.

    Alternates between CONTACT and GAP states. Real pass durations for a
    550 km orbit are 5–10 min, with ~90 min between passes at a single
    ground station. The model uses randomised durations within those bounds.

    The dt_s parameter allows the contact state machine to advance at the
    simulation time step (which may be > 1 s when time_multiplier > 1).
    """
    PASS_S_MIN = 300      #  5 min
    PASS_S_MAX = 600      # 10 min
    GAP_S_MIN  = 3_000    # 50 min
    GAP_S_MAX  = 6_000    # ~100 min

    DOWNLINK_MEAN_MBPS = 120.0
    DOWNLINK_STD_MBPS  =   5.0
    UPLINK_MEAN_MBPS   =  20.0
    UPLINK_STD_MBPS    =   1.0

    def __init__(self):
        self.in_contact = False
        self._timer_s   = random.uniform(self.GAP_S_MIN, self.GAP_S_MAX)

    def update(self, dt_s: float = 1.0):
        self._timer_s -= dt_s
        if self._timer_s <= 0:
            self.in_contact = not self.in_contact
            if self.in_contact:
                self._timer_s = random.uniform(self.PASS_S_MIN, self.PASS_S_MAX)
            else:
                self._timer_s = random.uniform(self.GAP_S_MIN, self.GAP_S_MAX)

        if self.in_contact:
            dl = max(0.0, random.gauss(self.DOWNLINK_MEAN_MBPS, self.DOWNLINK_STD_MBPS))
            ul = max(0.0, random.gauss(self.UPLINK_MEAN_MBPS,   self.UPLINK_STD_MBPS))
            return True, dl, ul

        return False, 0.0, 0.0


# ── Fault injection modes ─────────────────────────────────────────────────────

class FaultType:
    """Enumeration of supported fault injection modes."""
    NONE               = "none"
    BATTERY_DEGRADATION = "battery_degradation"   # battery drains faster than expected
    THERMAL_RUNAWAY    = "thermal_runaway"         # temperature rises uncontrollably
    SAFE_MODE_TRIGGER  = "safe_mode_trigger"       # forced safe mode entry


class _FaultInjector:
    """
    Injects synthetic faults into the telemetry stream for anomaly
    detection model training and validation.

    Faults are labelled (fault_injected=True) so they can be excluded
    from normal training data or used as positive examples in supervised
    validation.
    """

    def __init__(self, fault_type: str = FaultType.NONE):
        self.fault_type       = fault_type
        self._degradation_pct = 0.0   # cumulative battery degradation

    def apply(self, battery_pct: float, temperature_c: float, safe_mode: bool):
        """
        Apply the configured fault to the current telemetry values.

        Returns:
            (battery_pct, temperature_c, safe_mode, fault_injected)
        """
        if self.fault_type == FaultType.NONE:
            return battery_pct, temperature_c, safe_mode, False

        elif self.fault_type == FaultType.BATTERY_DEGRADATION:
            # Progressively accelerate battery discharge — mimics cell degradation
            self._degradation_pct = min(self._degradation_pct + 0.05, 20.0)
            degraded = max(0.0, battery_pct - self._degradation_pct)
            return degraded, temperature_c, safe_mode, True

        elif self.fault_type == FaultType.THERMAL_RUNAWAY:
            # Temperature rises by 2°C per tick regardless of eclipse state
            overheated = min(temperature_c + random.gauss(2.0, 0.3), 120.0)
            return battery_pct, overheated, safe_mode, True

        elif self.fault_type == FaultType.SAFE_MODE_TRIGGER:
            # Force safe mode entry — simulates on-board fault detection
            return battery_pct, temperature_c, True, True

        return battery_pct, temperature_c, safe_mode, False


# ── Main generator ────────────────────────────────────────────────────────────

class TelemetryGenerator:
    """
    Orchestrates all sub-models and produces a Telemetry snapshot each tick.

    Sprint 5 additions
    ──────────────────
    orbit_type      — classifies the satellite for ML model routing
    time_multiplier — advances simulated time faster than wall-clock;
                      at 60× one second of wall-clock produces 60 seconds
                      of simulated telemetry (essential for training data
                      generation without waiting hours at 1 Hz)
    fault_type      — injects synthetic anomalies for model training

    Internal data flow
    ──────────────────
    1. OrbitModel       →  lat, lon, alt, in_eclipse
    2. _ContactModel    →  in_contact, downlink_rate, uplink_rate
    3. BatteryModel     →  battery_pct, voltage, solar_panel_power
    4. ThermalModel     →  temperature_c
    5. _ComputeModel    →  cpu_utilization, memory_utilization
    6. _FaultInjector   →  apply fault modifications (Sprint 5)
    7. Snapshot all fields into an immutable Telemetry object
    """

    def __init__(
        self,
        satellite_id:    str   = "SAT-001",
        orbit_type:      str   = "LEO_CIRCULAR",
        time_multiplier: float = 1.0,
        fault_type:      str   = FaultType.NONE,
        raan_deg:        float = 0.0,
        inclination_deg: float = 51.6,
        altitude_km:     float = 550.0,
        mean_anomaly_deg:float = 0.0,
    ):
        self.satellite_id    = satellite_id
        self.orbit_type      = orbit_type
        self.time_multiplier = time_multiplier

        self.satellite = Satellite(satellite_id=satellite_id)

        self._orbit   = OrbitModel(
            raan_deg         = raan_deg,
            inclination_deg  = inclination_deg,
            altitude_km      = altitude_km,
            mean_anomaly_deg = mean_anomaly_deg,
        )
        self._battery = BatteryModel(initial_pct=self.satellite.battery_pct)
        self._thermal = ThermalModel(initial_temp_c=self.satellite.temperature_c)
        self._compute = _ComputeModel()
        self._contact = _ContactModel()
        self._fault   = _FaultInjector(fault_type=fault_type)

        # Simulated time cursor — advances at time_multiplier × wall-clock rate
        self._sim_time = datetime.now(timezone.utc)

    def generate(self) -> Telemetry:
        """
        Advance the simulation by one simulated tick and return a snapshot.

        The tick duration in simulated time = 1 second × time_multiplier.
        The contact model and orbit model both receive the simulated time
        step so physics scale correctly at any speed.
        """
        sat    = self.satellite
        dt_s   = 1.0 * self.time_multiplier   # simulated seconds per tick

        # Advance simulated clock
        self._sim_time += timedelta(seconds=dt_s)

        # 1. Orbit — propagate to current simulated time
        lat, lon, alt, in_eclipse = self._orbit.propagate()
        sat.latitude_deg  = lat
        sat.longitude_deg = lon
        sat.altitude_km   = alt
        sat.in_eclipse    = in_eclipse

        # 2. Contact windows — advance by simulated dt
        in_contact, dl, ul = self._contact.update(dt_s=dt_s)
        sat.in_contact         = in_contact
        sat.downlink_rate_mbps = dl
        sat.uplink_rate_mbps   = ul

        # 3. Power
        batt_pct, voltage, solar_w = self._battery.update(in_eclipse)
        sat.battery_pct         = batt_pct
        sat.battery_voltage_v   = voltage
        sat.solar_panel_power_w = solar_w

        # 4. Thermal
        sat.temperature_c = self._thermal.update(in_eclipse)

        # 5. Compute
        cpu, mem = self._compute.update(in_contact)
        sat.cpu_utilization_pct    = cpu
        sat.memory_utilization_pct = mem

        # 6. Fault injection (Sprint 5)
        batt, temp, safe, fault = self._fault.apply(
            sat.battery_pct,
            sat.temperature_c,
            sat.safe_mode,
        )
        sat.battery_pct   = batt
        sat.temperature_c = temp
        sat.safe_mode     = safe

        # 7. Snapshot → immutable Telemetry record
        return Telemetry(
            satellite_id            = sat.satellite_id,
            timestamp               = self._sim_time,
            latitude_deg            = sat.latitude_deg,
            longitude_deg           = sat.longitude_deg,
            altitude_km             = sat.altitude_km,
            in_eclipse              = sat.in_eclipse,
            in_contact              = sat.in_contact,
            orbit_type              = self.orbit_type,
            battery_pct             = sat.battery_pct,
            battery_voltage_v       = sat.battery_voltage_v,
            solar_panel_power_w     = sat.solar_panel_power_w,
            temperature_c           = sat.temperature_c,
            cpu_utilization_pct     = sat.cpu_utilization_pct,
            memory_utilization_pct  = sat.memory_utilization_pct,
            downlink_rate_mbps      = sat.downlink_rate_mbps,
            uplink_rate_mbps        = sat.uplink_rate_mbps,
            safe_mode               = sat.safe_mode,
            anomaly_flag            = sat.anomaly_flag,
            fault_injected          = fault,
        )
