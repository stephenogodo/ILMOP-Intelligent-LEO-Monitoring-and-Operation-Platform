from services.satellite_simulator.simulator import (
    SatelliteSimulator
)

sim = SatelliteSimulator()

sim.run(
    samples=20,
    interval_seconds=1,
    save_csv=True,
)