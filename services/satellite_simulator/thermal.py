import random


class ThermalModel:
    """
    Eclipse-aware spacecraft thermal model.

    Physics
    ───────
    LEO satellites experience a ~35–55 °C thermal swing across each orbit as
    they alternate between direct solar illumination and Earth's shadow.

    This model uses a first-order (exponential) thermal lag toward a target:

        ΔT = α × (T_target − T_current) + noise

    where α is a dimensionless gain per tick (controls how quickly the satellite
    reaches equilibrium).

    Targets
    ───────
    Sunlight  → +35 °C  (solar flux + albedo heating)
    Eclipse   → −20 °C  (radiative cooling to deep space)

    Hard limits clamp the temperature to the spacecraft's survival range.
    """

    SUNLIGHT_TARGET_C = 35.0
    ECLIPSE_TARGET_C  = -20.0

    THERMAL_GAIN      = 0.008    # fraction of (T_target − T) per tick
    NOISE_STD_C       = 0.15     # Gaussian jitter (°C)

    TEMP_MIN_C        = -30.0    # hard lower limit (survival temperature)
    TEMP_MAX_C        = 120.0    # hard upper limit — catastrophic thermal runaway ceiling

    def __init__(self, initial_temp_c: float = 20.0):
        self.temperature_c = float(initial_temp_c)

    def update(self, in_eclipse: bool) -> float:
        """
        Advance the thermal state by one tick.

        Args:
            in_eclipse: True when the satellite is in Earth's shadow.

        Returns:
            temperature_c — current spacecraft temperature (°C).
        """
        target = self.ECLIPSE_TARGET_C if in_eclipse else self.SUNLIGHT_TARGET_C
        trend  = self.THERMAL_GAIN * (target - self.temperature_c)
        noise  = random.gauss(0.0, self.NOISE_STD_C)

        self.temperature_c = max(
            self.TEMP_MIN_C,
            min(self.TEMP_MAX_C, self.temperature_c + trend + noise),
        )
        return self.temperature_c
