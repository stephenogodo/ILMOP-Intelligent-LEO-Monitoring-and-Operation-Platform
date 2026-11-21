"""
services/messaging/project_codecs.py

The ready-made codecs for ILMOP's two Kafka message types, and four small
functions that every service calls:

    encode_telemetry(t) / decode_telemetry(raw)
    encode_alarm(a)     / decode_alarm(raw)

A service does not build codecs or registries itself.  It just calls these,
which keeps each of the five Kafka services down to a one-line change.

THE SHARED REGISTRY (read this before relying on Avro in production)
────────────────────────────────────────────────────────────────────
The registry (services/schema_registry) lives in process memory.  Producer
and consumer are separate processes, so they do NOT share one registry.
What makes Avro work between them is that every process builds the SAME
registry from the SAME committed schema files, in the SAME order, so the
same schema gets the same numeric ID everywhere
(defaults.build_default_registry).

That holds only while all processes run the same version of the code.  A
consumer built before a new schema version existed cannot look up the new
ID and rejects those messages.  Replacing this in-memory registry with a
shared service (the Confluent registry, Sprint 9) removes the limit.

SETTING
───────
KAFKA_SERIALIZATION=json|avro   (default json) decides what the encode_*
functions write.  The decode_* functions always accept both.
"""

from functools import lru_cache
from typing import Optional

from services.messaging.codec import MessageCodec, SerializationFormat
from services.schema_registry.defaults import build_default_registry
from services.schema_registry.generate import (
    ALARM_SUBJECT, TELEMETRY_SUBJECT, alarm_schema_json, telemetry_schema_json,
)
from services.schema_registry.registry import SchemaRegistry
from shared.config import settings
from shared.schemas.alarm_schema import Alarm
from shared.schemas.telemetry_schema import Telemetry


@lru_cache(maxsize=1)
def get_registry() -> SchemaRegistry:
    """This process's registry, built once from the committed schema history."""
    return build_default_registry()


@lru_cache(maxsize=None)
def _telemetry_codec(fmt: SerializationFormat) -> MessageCodec[Telemetry]:
    return MessageCodec(Telemetry, TELEMETRY_SUBJECT, telemetry_schema_json(),
                        get_registry(), fmt)


@lru_cache(maxsize=None)
def _alarm_codec(fmt: SerializationFormat) -> MessageCodec[Alarm]:
    return MessageCodec(Alarm, ALARM_SUBJECT, alarm_schema_json(),
                        get_registry(), fmt)


def _configured_format(fmt: Optional[str] = None) -> SerializationFormat:
    # Read the setting at CALL time (not import time) so it can be changed
    # in tests and so a service reads the value it was started with.
    return SerializationFormat(fmt or settings.kafka_serialization)


def encode_telemetry(telemetry: Telemetry, fmt: Optional[str] = None) -> bytes:
    return _telemetry_codec(_configured_format(fmt)).encode(telemetry)


def decode_telemetry(raw: bytes) -> Telemetry:
    """Accepts JSON or Avro.  Raises MessageDecodeError on anything else."""
    return _telemetry_codec(_configured_format()).decode(raw)


def encode_alarm(alarm: Alarm, fmt: Optional[str] = None) -> bytes:
    return _alarm_codec(_configured_format(fmt)).encode(alarm)


def decode_alarm(raw: bytes) -> Alarm:
    """Accepts JSON or Avro.  Raises MessageDecodeError on anything else."""
    return _alarm_codec(_configured_format()).decode(raw)
