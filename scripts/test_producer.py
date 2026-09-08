from services.kafka_producer.producer import (
    TelemetryProducer,
)

from services.satellite_simulator.telemetry import (
    TelemetryGenerator,
)


generator = TelemetryGenerator()

producer = TelemetryProducer()

telemetry = generator.generate()

producer.publish(
    telemetry.model_dump(mode="json")
)