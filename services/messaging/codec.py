"""
services/messaging/codec.py

Turns a Pydantic model into Kafka message bytes and back, in either of two
formats: JSON (what ILMOP used until Sprint 8) or Avro (S8-01).

WHY THERE ARE TWO FORMATS AT ALL
────────────────────────────────
A pipeline cannot switch format in one instant: producers and consumers are
different processes that are restarted at different times.  If a producer
starts sending Avro while one consumer still only understands JSON, that
consumer fails on every message.

The safe order is the BACKWARD rule from ADR-022, applied to the format
itself:

    1. First upgrade every CONSUMER so it reads BOTH formats.
    2. Only then switch the PRODUCERS to Avro.

This file makes step 1 automatic: decode() looks at the first byte of the
message and picks the right reader, so no configuration is needed on the
consuming side.

HOW decode() TELLS THE FORMATS APART
────────────────────────────────────
An Avro message in the registry wire format starts with the magic byte 0
(serde.py).  A JSON document starts with "{" (or white space), never with a
zero byte.  So the first byte is a reliable, free signal.

WHAT encode() DOES
──────────────────
It writes the format the codec was configured with.  The default
configuration is JSON, so nothing changes until someone switches it.

ERRORS
──────
decode() raises ONE exception type, MessageDecodeError, whatever went wrong
(bad bytes, bad JSON, bad Avro, unknown schema ID, a value Pydantic rejects).
Consumers need only one `except` clause, and the original error is kept as
the exception's __cause__ for the log.
"""

import json
from enum import Enum
from typing import Any, Generic, Type, TypeVar

from pydantic import BaseModel

from services.schema_registry.registry import SchemaRegistry
from services.schema_registry.serde import MAGIC_BYTE, AvroSerde

M = TypeVar("M", bound=BaseModel)


class SerializationFormat(str, Enum):
    JSON = "json"
    AVRO = "avro"


class MessageDecodeError(ValueError):
    """A Kafka message could not be turned into the expected model."""


class MessageCodec(Generic[M]):
    """
    Encodes and decodes ONE kind of model (for example Telemetry).

        codec = MessageCodec(Telemetry, "telemetry-value", schema_json, registry,
                             SerializationFormat.AVRO)
        raw = codec.encode(telemetry)      # bytes in the configured format
        telemetry = codec.decode(raw)      # accepts JSON or Avro, whichever it is
    """

    def __init__(self, model_class: Type[M], subject: str, schema_json: Any,
                 registry: SchemaRegistry, fmt: SerializationFormat) -> None:
        self._model_class = model_class
        self._fmt = SerializationFormat(fmt)
        # The Avro serde is always built, even when we only write JSON,
        # because decode() must be able to read Avro at any time (dual-read).
        # Building it also registers this application's schema, so an
        # incompatible model change fails here at start-up.
        self._avro = AvroSerde(registry, subject, model_class, schema_json)

    @property
    def fmt(self) -> SerializationFormat:
        return self._fmt

    # ── Writing ──────────────────────────────────────────────────────────────

    def encode(self, model: M) -> bytes:
        """Serialise a model in the configured format."""
        if self._fmt == SerializationFormat.AVRO:
            return self._avro.serialize(model)
        # JSON: exactly what the producer and detector did before Sprint 9.
        return json.dumps(model.model_dump(mode="json")).encode("utf-8")

    # ── Reading ──────────────────────────────────────────────────────────────

    def decode(self, raw: bytes) -> M:
        """
        Deserialise a message, detecting its format from the first byte.
        Raises MessageDecodeError for anything that is not a valid message.
        """
        if not isinstance(raw, (bytes, bytearray)) or len(raw) == 0:
            raise MessageDecodeError("empty or non-bytes message")
        try:
            if raw[0] == MAGIC_BYTE:
                return self._avro.deserialize(bytes(raw))      # Avro wire format
            return self._model_class(**json.loads(bytes(raw).decode("utf-8")))
        except Exception as exc:       # deliberately broad: one error type out
            raise MessageDecodeError(f"{type(exc).__name__}: {exc}") from exc
