import random
from datetime import datetime, timezone

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
    CPU_LAG              =  0.2    # fraction of gap closed per tick
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

    Alternates between CONTACT and GAP states.  Real pass durations for a
    550 km orbit are typically 5–10 min, with ~90 min between passes at a
    single ground station.  This model uses randomised durations within
    those realistic bounds.

    During contact, downlink and uplink rates are sampled from Gaussian
    distributions representing a stable X-band link.
    """
    PASS_S_MIN = 300     #  5 min
    PASS_S_MAX = 600     # 10 min
    GAP_S_MIN  = 3_000   # 50 min
    GAP_S_MAX  = 6_000   # ~100 min

    DOWNLINK_MEAN_MBPS = 120.0
    DOWNLINK_STD_MBPS  =   5.0
    UPLINK_MEAN_MBPS   =  20.0
    UPLINK_STD_MBPS    =   1.0

    def __init__(self):
        self.in_contact = False
        # Start mid-gap so the first contact feels natural
        self._timer_s   = random.uniform(self.GAP_S_MIN, self.GAP_S_MAX)

    def update(self, dt_s: float = 1.0):
        """
        Advance the contact state machine by dt_s seconds.

        Returns:
            (in_contact, downlink_rate_mbps, uplink_rate_mbps)
        """
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


# ── Main generator ────────────────────────────────────────────────────────────

class TelemetryGenerator:
    """
    Orchestrates all sub-models and produces a Telemetry snapshot each tick.

    Internal data flow
    ──────────────────
    1. OrbitModel       →  lat, lon, alt, in_eclipse
    2. _ContactModel    →  in_contact, downlink_rate, uplink_rate
    3. BatteryModel     →  battery_pct, voltage, solar_panel_power
    4. ThermalModel     →  temperature_c
    5. _ComputeModel    →  cpu_utilization, memory_utilization
    6. Snapshot all fields into an immutable Telemetry object
    """

    def __init__(self, satellite_id: str = "SAT-001"):
        self.satellite = Satellite(satellite_id=satellite_id)

        self._orbit   = OrbitModel()
        self._battery = BatteryModel(initial_pct=self.satellite.battery_pct)
        self._thermal = ThermalModel(initial_temp_c=self.satellite.temperature_c)
        self._compute = _ComputeModel()
        self._contact = _ContactModel()

    def generate(self) -> Telemetry:
        """Advance the simulation by one tick and return a Telemetry snapshot."""
        sat = self.satellite

        # 1. Orbit
        lat, lon, alt, in_eclipse = self._orbit.propagate()
        sat.latitude_deg  = lat
        sat.longitude_deg = lon
        sat.altitude_km   = alt
        sat.in_eclipse    = in_eclipse

        # 2. Contact windows
        in_contact, dl, ul = self._contact.update()
        sat.in_contact         = in_contact
        sat.downlink_rate_mbps = dl
        sat.uplink_rate_mbps   = ul

        # 3. Power — battery and solar depend on eclipse state
        batt_pct, voltage, solar_w = self._battery.update(in_eclipse)
        sat.battery_pct          = batt_pct
        sat.battery_voltage_v    = voltage
        sat.solar_panel_power_w  = solar_w

        # 4. Thermal — equilibrium target depends on eclipse state
        sat.temperature_c = self._thermal.update(in_eclipse)

        # 5. Compute resources — CPU spikes during contact
        cpu, mem = self._compute.update(in_contact)
        sat.cpu_utilization_pct    = cpu
        sat.memory_utilization_pct = mem

        # 6. Snapshot → immutable Telemetry record
        return Telemetry(
            satellite_id            = sat.satellite_id,
            timestamp               = datetime.now(timezone.utc),
            latitude_deg            = sat.latitude_deg,
            longitude_deg           = sat.longitude_deg,
            altitude_km             = sat.altitude_km,
            in_eclipse              = sat.in_eclipse,
            in_contact              = sat.in_contact,
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
        )
