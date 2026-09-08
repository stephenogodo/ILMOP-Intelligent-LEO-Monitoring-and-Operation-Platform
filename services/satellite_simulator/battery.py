import random


class BatteryModel:
    """
    Eclipse-aware battery model.

    Physics
    ───────
    In eclipse the spacecraft runs entirely on battery (solar panels produce
    no power), so the battery discharges.  In sunlight the solar panels supply
    more power than the bus consumes, so the battery charges.

    Rates are expressed per simulator tick (default 1-second cadence).

    Battery SoC → terminal voltage mapping (linear approximation):
        V = 26.0 + (SoC% / 100) × 2.0   →   range 26.0 V (empty) – 28.0 V (full)

    Solar panel output:
        Eclipse   →   0 W
        Sunlight  →   Gaussian(μ=1300 W, σ=20 W), clipped to ≥ 0
    """

    DISCHARGE_RATE_PCT  = 0.25   # %/tick in eclipse   (net: consumption > generation)
    CHARGE_RATE_PCT     = 0.15   # %/tick in sunlight  (net: generation > consumption)
    NOISE_STD_PCT       = 0.05   # Gaussian jitter (%)

    VOLTAGE_MIN_V       = 26.0
    VOLTAGE_RANGE_V     =  2.0

    SOLAR_POWER_MEAN_W  = 1300.0
    SOLAR_POWER_STD_W   =   20.0

    def __init__(self, initial_pct: float = 95.0):
        self.battery_pct = float(initial_pct)

    def update(self, in_eclipse: bool):
        """
        Advance the battery state by one tick.

        Args:
            in_eclipse: True when the satellite is in Earth's shadow.

        Returns:
            (battery_pct, battery_voltage_v, solar_panel_power_w)
        """
        trend = -self.DISCHARGE_RATE_PCT if in_eclipse else self.CHARGE_RATE_PCT
        noise = random.gauss(0.0, self.NOISE_STD_PCT)

        self.battery_pct = max(0.0, min(100.0, self.battery_pct + trend + noise))

        voltage      = self.VOLTAGE_MIN_V + (self.battery_pct / 100.0) * self.VOLTAGE_RANGE_V
        solar_power  = (
            0.0 if in_eclipse
            else max(0.0, random.gauss(self.SOLAR_POWER_MEAN_W, self.SOLAR_POWER_STD_W))
        )

        return self.battery_pct, voltage, solar_power
