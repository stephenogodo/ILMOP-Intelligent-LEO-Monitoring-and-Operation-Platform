from services.kafka_consumer.consumer import (
    TelemetryConsumer,
)

consumer = TelemetryConsumer()

consumer.consume()