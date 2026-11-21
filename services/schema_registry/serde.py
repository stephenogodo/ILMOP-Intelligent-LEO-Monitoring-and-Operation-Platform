"""
services/schema_registry/serde.py

Serialisation glue: Pydantic model  <->  bytes for Kafka (S8-01).

THE WIRE FORMAT (identical to the Confluent one)
────────────────────────────────────────────────
    byte 0        magic byte, always 0   (lets a consumer reject foreign data)
    bytes 1..4    schema ID, 4-byte big-endian unsigned integer
    bytes 5..     the Avro-encoded payload

So a message is only 5 bytes larger than the raw Avro, yet any consumer can
discover exactly which schema wrote it.

PRODUCER SIDE   serialize(model)
    1. turn the model into a plain dict (datetimes become microsecond ints)
    2. Avro-encode it with the model's own schema
    3. prepend magic byte + the schema ID obtained from the registry

CONSUMER SIDE   deserialize(message)
    1. check the magic byte, read the schema ID
    2. ask the registry for the WRITER schema with that ID
    3. decode the payload, resolving writer -> reader, where the reader is
       THIS consumer's own model schema.  An old message missing a newer
       field gets that field's default; a message from a newer producer with
       an extra field has it ignored.  This is the mechanism S8-02 relies on.
    4. build the Pydantic model (which validates the values once more)

TIME ZONES: a naive datetime is taken to be UTC.  Decoded datetimes are
always timezone-aware UTC.
"""

import struct
from datetime import datetime, timedelta, timezone
from typing import Any, Generic, Tuple, Type, TypeVar

from pydantic import BaseModel

from services.schema_registry.avro_codec import AvroDecodeError, decode, encode
from services.schema_registry.avro_schema import (
    PrimitiveSchema, RecordSchema, Schema, UnionSchema, parse_schema,
)
from services.schema_registry.registry import SchemaRegistry

MAGIC_BYTE = 0
HEADER_LEN = 5                         # 1 magic byte + 4 schema-ID bytes
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MICROSECOND = timedelta(microseconds=1)

M = TypeVar("M", bound=BaseModel)


class WireFormatError(ValueError):
    """The message is not in the registry wire format."""


# ── The 5-byte header ────────────────────────────────────────────────────────

def pack(schema_id: int, payload: bytes) -> bytes:
    """Prepend the magic byte and schema ID to an Avro payload."""
    if not 0 <= schema_id <= 0xFFFFFFFF:
        raise WireFormatError(f"schema id {schema_id} does not fit in 4 bytes")
    return struct.pack(">bI", MAGIC_BYTE, schema_id) + payload


def unpack(message: bytes) -> Tuple[int, bytes]:
    """Split a message into (schema ID, Avro payload), checking the header."""
    if len(message) < HEADER_LEN:
        raise WireFormatError(f"message is {len(message)} byte(s); the header alone is "
                              f"{HEADER_LEN}")
    magic, schema_id = struct.unpack(">bI", message[:HEADER_LEN])
    if magic != MAGIC_BYTE:
        raise WireFormatError(f"bad magic byte {magic}; expected {MAGIC_BYTE}")
    return schema_id, message[HEADER_LEN:]


# ── datetime <-> microseconds ────────────────────────────────────────────────

def datetime_to_micros(value: datetime) -> int:
    """Microseconds since the Unix epoch, using integer arithmetic (no float error)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (value - _EPOCH) // _ONE_MICROSECOND


def micros_to_datetime(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


def _is_timestamp(schema: Schema) -> bool:
    """True for timestamp-micros, also when wrapped in an optional union."""
    if isinstance(schema, UnionSchema):
        return any(_is_timestamp(b) for b in schema.branches)
    return isinstance(schema, PrimitiveSchema) and schema.logical_type == "timestamp-micros"


# ── Model <-> record dict ────────────────────────────────────────────────────

def model_to_record(model: BaseModel, schema: RecordSchema) -> dict:
    """Pick the schema's fields off the model; datetimes become microsecond ints."""
    record = {}
    for f in schema.fields:
        value = getattr(model, f.name)
        if value is not None and _is_timestamp(f.type):
            value = datetime_to_micros(value)
        record[f.name] = value
    return record


def record_to_kwargs(record: dict, schema: RecordSchema) -> dict:
    """Inverse of model_to_record: microsecond ints become aware datetimes."""
    out = {}
    for f in schema.fields:
        value = record[f.name]
        if value is not None and _is_timestamp(f.type):
            try:
                value = micros_to_datetime(value)
            except OverflowError as exc:
                raise AvroDecodeError(f"{f.name}: timestamp out of range") from exc
        out[f.name] = value
    return out


# ── The serialiser / deserialiser pair for one subject ───────────────────────

class AvroSerde(Generic[M]):
    """
    Serialises and deserialises ONE kind of model on ONE subject.

        serde = AvroSerde(registry, "telemetry-value", Telemetry, telemetry_schema_json())
        message = serde.serialize(telemetry)     # bytes for Kafka
        telemetry = serde.deserialize(message)   # back to a Telemetry

    `schema_json` is this application's own (reader and writer) schema.
    With auto_register=True (the default, as in Kafka clients) it is
    registered when the serde is built, so an incompatible change fails at
    start-up instead of corrupting the stream later.  With False, the schema
    must already be in the registry.
    """

    def __init__(self, registry: SchemaRegistry, subject: str, model_class: Type[M],
                 schema_json: Any, auto_register: bool = True):
        parsed = parse_schema(schema_json)
        if not isinstance(parsed, RecordSchema):
            raise TypeError("an AvroSerde needs a record schema")
        self._registry = registry
        self._subject = subject
        self._model_class = model_class
        self._schema: RecordSchema = parsed
        self._schema_id = (registry.register(subject, parsed) if auto_register
                           else registry.find_id(subject, parsed))

    @property
    def schema_id(self) -> int:
        return self._schema_id

    def serialize(self, model: M) -> bytes:
        record = model_to_record(model, self._schema)
        return pack(self._schema_id, encode(self._schema, record))

    def deserialize(self, message: bytes) -> M:
        schema_id, payload = unpack(message)
        writer = self._registry.get_schema(schema_id)      # SchemaNotFoundError if unknown
        record = decode(payload, writer, self._schema)     # resolve writer -> our schema
        return self._model_class(**record_to_kwargs(record, self._schema))
