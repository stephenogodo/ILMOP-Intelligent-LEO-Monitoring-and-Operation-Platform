"""
services/schema_registry/avro_codec.py

Avro binary encoding and decoding in pure Python (S8-01), with schema
resolution on decode (S8-02).

HOW AVRO LAYS OUT BYTES
───────────────────────
There are no field names, no type tags and no separators.  A record is its
fields' values written one after another, in the schema's field order.

    null      zero bytes
    boolean   one byte: 0 or 1
    long      zig-zag, then variable-length: see _write_long below
    double    8 bytes, IEEE-754, LITTLE-endian
    string    long length (in bytes), then the UTF-8 bytes
    enum      long: index of the symbol in the schema's symbols list
    union     long: index of the branch used, then that branch's value
    timestamp-micros   a long: microseconds since 1970-01-01 UTC

VARIABLE-LENGTH LONGS (the one non-obvious part)
────────────────────────────────────────────────
Small numbers should take few bytes, and negative numbers must not take ten.
So: first "zig-zag" the signed value into an unsigned one that interleaves
positives and negatives (0,-1,1,-2,2 become 0,1,2,3,4), then write it 7 bits
at a time, lowest bits first, with the top bit of each byte meaning "more
bytes follow".  Known answers from the Avro spec: 0 -> 00, -1 -> 01,
1 -> 02, 64 -> 80 01.

DECODING WITH TWO SCHEMAS
─────────────────────────
decode(data, writer, reader) reads the bytes using the WRITER's layout (the
only way to know where each value starts) and produces a value shaped like
the READER's schema: fields the reader dropped are skipped, fields the
reader added get their defaults.  Whether that is allowed at all is decided
up front by compatibility.can_read; if it fails we raise before touching a
single byte, with the full list of reasons.

Values in and out are plain Python: dict for a record, str, bool, float,
int (including timestamp-micros as an int), None for null.  Converting
datetimes and Pydantic models is serde.py's job.
"""

import copy
import math
import struct
from typing import Any, List, Optional

from services.schema_registry.avro_schema import (
    EnumSchema, PrimitiveSchema, RecordSchema, Schema, UnionSchema, describe,
)
from services.schema_registry.compatibility import can_read

_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1


class AvroEncodeError(ValueError):
    """The value does not match the schema."""


class AvroDecodeError(ValueError):
    """The bytes are damaged, truncated or do not match the schema."""


class SchemaResolutionError(AvroDecodeError):
    """The reader schema cannot read data written with the writer schema."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__("reader schema cannot read writer schema: " + "; ".join(problems))


# ══════════════════════════════════════════════════════════════════════════════
# Encoding
# ══════════════════════════════════════════════════════════════════════════════

def encode(schema: Schema, value: Any) -> bytes:
    """Encode `value` with `schema`.  Raises AvroEncodeError on a mismatch."""
    out = bytearray()
    _encode(out, schema, value, "$")
    return bytes(out)


def _write_long(out: bytearray, n: int, path: str) -> None:
    """Zig-zag + variable-length encoding of a signed 64-bit integer."""
    if not _INT64_MIN <= n <= _INT64_MAX:
        raise AvroEncodeError(f"{path}: {n} does not fit in a 64-bit long")
    zz = (n << 1) ^ (n >> 63)           # zig-zag: sign bit moves to the bottom
    while zz > 0x7F:
        out.append((zz & 0x7F) | 0x80)  # low 7 bits, "more follows" flag set
        zz >>= 7
    out.append(zz)                      # final byte, flag clear


def _encode(out: bytearray, schema: Schema, value: Any, path: str) -> None:
    if isinstance(schema, PrimitiveSchema):
        _encode_primitive(out, schema, value, path)

    elif isinstance(schema, EnumSchema):
        if not isinstance(value, str) or value not in schema.symbols:
            raise AvroEncodeError(
                f"{path}: {value!r} is not a symbol of enum {schema.name} {list(schema.symbols)}")
        _write_long(out, schema.symbols.index(value), path)

    elif isinstance(schema, UnionSchema):
        # Our unions are [null, T] or [T, null]: None picks the null branch.
        for index, branch in enumerate(schema.branches):
            branch_is_null = isinstance(branch, PrimitiveSchema) and branch.name == "null"
            if (value is None) == branch_is_null:
                _write_long(out, index, path)
                _encode(out, branch, value, path)
                return
        raise AvroEncodeError(f"{path}: no union branch for {value!r}")  # pragma: no cover

    elif isinstance(schema, RecordSchema):
        if not isinstance(value, dict):
            raise AvroEncodeError(f"{path}: expected a dict for record {schema.name}, "
                                  f"got {type(value).__name__}")
        extras = set(value) - {f.name for f in schema.fields}
        if extras:                       # strict: a typo must not vanish silently
            raise AvroEncodeError(f"{path}: unknown field(s) {sorted(extras)} "
                                  f"for record {schema.name}")
        for f in schema.fields:
            if f.name in value:
                _encode(out, f.type, value[f.name], f"{path}.{f.name}")
            elif f.has_default:
                _encode(out, f.type, f.default, f"{path}.{f.name}")
            else:
                raise AvroEncodeError(f"{path}.{f.name}: required field is missing")


def _encode_primitive(out: bytearray, schema: PrimitiveSchema, value: Any, path: str) -> None:
    name = schema.name
    if name == "null":
        if value is not None:
            raise AvroEncodeError(f"{path}: expected None, got {value!r}")
    elif name == "boolean":
        if not isinstance(value, bool):
            raise AvroEncodeError(f"{path}: expected bool, got {type(value).__name__}")
        out.append(1 if value else 0)
    elif name == "long":
        # bool is a subclass of int in Python: reject it explicitly.
        if not isinstance(value, int) or isinstance(value, bool):
            raise AvroEncodeError(f"{path}: expected int, got {type(value).__name__}")
        _write_long(out, value, path)
    elif name == "double":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise AvroEncodeError(f"{path}: expected float, got {type(value).__name__}")
        out += struct.pack("<d", float(value))      # little-endian per the Avro spec
    elif name == "string":
        if not isinstance(value, str):
            raise AvroEncodeError(f"{path}: expected str, got {type(value).__name__}")
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError as exc:           # e.g. a lone surrogate
            raise AvroEncodeError(f"{path}: string is not valid UTF-8: {exc}") from exc
        _write_long(out, len(raw), path)
        out += raw


# ══════════════════════════════════════════════════════════════════════════════
# Decoding
# ══════════════════════════════════════════════════════════════════════════════

class _Reader:
    """A cursor over the input bytes with bounds-checked reads."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise AvroDecodeError(f"data is truncated: need {n} byte(s) at offset "
                                  f"{self.pos}, only {len(self.data) - self.pos} left")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def read_long(self) -> int:
        """Inverse of _write_long."""
        shift = 0
        zz = 0
        for _ in range(10):             # a 64-bit value needs at most 10 bytes
            byte = self.read(1)[0]
            zz |= (byte & 0x7F) << shift
            if not byte & 0x80:         # flag clear: this was the last byte
                n = (zz >> 1) ^ -(zz & 1)   # undo the zig-zag
                if not _INT64_MIN <= n <= _INT64_MAX:
                    raise AvroDecodeError("variable-length integer exceeds 64 bits")
                return n
            shift += 7
        raise AvroDecodeError("variable-length integer is longer than 10 bytes")


def decode(data: bytes, writer: Schema, reader: Optional[Schema] = None,
           allow_trailing: bool = False) -> Any:
    """
    Decode `data`, which was written with `writer`.

    reader=None   return the value exactly as the writer wrote it.
    reader=...    return it reshaped for `reader` (schema resolution).

    Unless allow_trailing is True, leftover bytes after the value are an
    error: they almost always mean the wrong schema was used.
    """
    if reader is not None:
        problems = can_read(writer, reader)
        if problems:
            raise SchemaResolutionError(problems)

    cursor = _Reader(data)
    value = _decode(cursor, writer, reader)
    if not allow_trailing and cursor.pos != len(data):
        raise AvroDecodeError(f"{len(data) - cursor.pos} unexpected trailing byte(s)")
    return value


def _decode(cur: _Reader, w: Schema, r: Optional[Schema]) -> Any:
    """
    Decode one value of writer type `w`; `r` is the reader type or None
    ("shape it like the writer").  Resolution was already proven possible.
    """
    # The writer chose a union branch: read which one, then decode that.
    if isinstance(w, UnionSchema):
        index = cur.read_long()
        if not 0 <= index < len(w.branches):
            raise AvroDecodeError(f"union branch index {index} out of range")
        return _decode(cur, w.branches[index], r)

    # Reader is a union, writer is not: use the first reader branch that fits.
    if isinstance(r, UnionSchema):
        r = next(b for b in r.branches if not can_read(w, b))

    if isinstance(w, PrimitiveSchema):
        return _decode_primitive(cur, w, r)

    if isinstance(w, EnumSchema):
        index = cur.read_long()
        if not 0 <= index < len(w.symbols):
            raise AvroDecodeError(f"enum index {index} out of range for {w.name}")
        symbol = w.symbols[index]
        if r is not None and symbol not in r.symbols:
            return r.default             # can_read guaranteed a default exists
        return symbol

    # Record
    values = {}
    for wf in w.fields:
        rf = r.field(wf.name) if r is not None else wf
        if rf is None:
            _decode(cur, wf.type, None)  # reader dropped this field: read and discard
        else:
            values[wf.name] = _decode(cur, wf.type, rf.type if r is not None else None)
    if r is None:
        return values
    # Build the result in the READER's field order, filling defaults for
    # fields the writer did not have.
    result = {}
    for rf in r.fields:
        result[rf.name] = values[rf.name] if rf.name in values else copy.deepcopy(rf.default)
    return result


def _decode_primitive(cur: _Reader, w: PrimitiveSchema, r: Optional[Schema]) -> Any:
    name = w.name
    if name == "null":
        return None
    if name == "boolean":
        byte = cur.read(1)[0]
        if byte not in (0, 1):
            raise AvroDecodeError(f"invalid boolean byte {byte}")
        return byte == 1
    if name == "long":
        n = cur.read_long()
        if isinstance(r, PrimitiveSchema) and r.name == "double":
            return float(n)              # the long -> double promotion
        return n
    if name == "double":
        value = struct.unpack("<d", cur.read(8))[0]
        return value
    if name == "string":
        length = cur.read_long()
        if length < 0:
            raise AvroDecodeError(f"negative string length {length}")
        try:
            return cur.read(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AvroDecodeError(f"string is not valid UTF-8: {exc}") from exc
    raise AvroDecodeError(f"unsupported type {describe(w)}")  # pragma: no cover
