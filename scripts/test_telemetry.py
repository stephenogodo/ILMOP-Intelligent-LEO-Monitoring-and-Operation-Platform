from services.satellite_simulator.telemetry import (
    TelemetryGenerator
)

generator = TelemetryGenerator()

for i in range(5):
    telemetry = generator.generate()

    print(
        f"\nTelemetry Sample {i+1}"
    )

    print(
        telemetry.model_dump()
    )