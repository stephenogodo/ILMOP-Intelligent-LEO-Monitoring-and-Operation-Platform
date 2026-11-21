"""
services/schema_registry/avro_schema.py

Parsing, validation and canonical form of Avro schemas (S8-01).

WHAT AN AVRO SCHEMA IS
──────────────────────
An Avro schema is a small JSON document that says what a message contains.
For example, a record with two fields:

    {"type": "record", "name": "Point",
     "fields": [{"name": "x", "type": "double"},
                {"name": "y", "type": "double", "default": 0.0}]}

The BYTES of an Avro message contain no field names and no type tags, only
the values, one after another.  So bytes are meaningless without the schema
that wrote them.  That is why a registry exists: it hands out the schema for
a given ID so a consumer can interpret the bytes.

WHAT THIS FILE DOES
───────────────────
It turns the JSON document into small, immutable Python objects
(PrimitiveSchema, EnumSchema, UnionSchema, RecordSchema), rejects anything
malformed, and can turn those objects back into a canonical JSON string with
a SHA-256 fingerprint.  The codec (avro_codec.py) and the compatibility
checker (compatibility.py) both work on these objects, never on raw JSON.

THE SUBSET WE SUPPORT (enough for Telemetry and Alarm)
──────────────────────────────────────────────────────
    null, boolean, long, double, string
    long with logicalType "timestamp-micros"   (a datetime)
    enum                                        (a Literal[...] of strings)
    union of exactly [null, T] or [T, null]     (an optional value)
    record (flat: fields may not be records themselves)

Not supported, and rejected with a clear error rather than half-handled:
float, int, bytes, fixed, array, map, nested records, general unions,
aliases, other logical types.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

# Avro names must start with a letter or underscore and contain only letters,
# digits and underscores.  (Enum symbols follow the same rule.)
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_PRIMITIVES = ("null", "boolean", "long", "double", "string")
_SUPPORTED_LOGICAL_TYPES = ("timestamp-micros",)


class SchemaError(ValueError):
    """The schema JSON is malformed or uses something we do not support."""


# ── The schema objects ────────────────────────────────────────────────────────
# All are frozen dataclasses: a parsed schema can never change, so it is safe
# to share between threads and to use as a dictionary key.

@dataclass(frozen=True)
class PrimitiveSchema:
    """null / boolean / long / double / string, optionally with a logical type."""
    name: str
    logical_type: Optional[str] = None      # only "timestamp-micros", on "long"


@dataclass(frozen=True)
class EnumSchema:
    """
    A fixed list of allowed string symbols.

    `default` is Avro's *enum default*: the symbol a READER substitutes when
    the writer sent a symbol the reader has never heard of.  We deliberately
    leave it unset for orbit_type (see generate.py): silently reading an
    unknown orbit as LEO would route a new orbit class to the wrong ML model.
    """
    name: str
    symbols: Tuple[str, ...]
    default: Optional[str] = None


@dataclass(frozen=True)
class UnionSchema:
    """An optional value: exactly one null branch and one other branch."""
    branches: Tuple["Schema", ...]


@dataclass(frozen=True)
class FieldSchema:
    """One field of a record."""
    name: str
    type: "Schema"
    has_default: bool = False
    # Only meaningful when has_default is True.  We keep a separate flag
    # because None is itself a legitimate default (for an optional field).
    default: Any = None
    doc: Optional[str] = None


@dataclass(frozen=True)
class RecordSchema:
    """An ordered list of named fields."""
    name: str
    fields: Tuple[FieldSchema, ...]
    namespace: Optional[str] = None
    doc: Optional[str] = None

    def field(self, name: str) -> Optional[FieldSchema]:
        """Look a field up by name; None if there is no such field."""
        for f in self.fields:
            if f.name == name:
                return f
        return None

    @property
    def full_name(self) -> str:
        return f"{self.namespace}.{self.name}" if self.namespace else self.name


Schema = Union[PrimitiveSchema, EnumSchema, UnionSchema, RecordSchema]


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_schema(source: Union[str, dict, list]) -> Schema:
    """
    Parse an Avro schema given as a JSON string or an already-loaded
    dict/list, and return the validated schema object.

    Raises SchemaError for anything malformed or unsupported.
    """
    if isinstance(source, str):
        # A bare primitive such as "string" is valid Avro but is not valid
        # JSON text, so try JSON first and fall back to the bare name.
        try:
            source = json.loads(source)
        except json.JSONDecodeError:
            pass
    return _parse(source, seen_names=set(), inside_record=False)


def _check_name(name: Any, what: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise SchemaError(f"invalid {what} name: {name!r}")
    return name


def _parse(node: Any, seen_names: set, inside_record: bool) -> Schema:
    """Recursive worker.  `seen_names` catches duplicate record/enum names."""

    # A bare string is a primitive type name: "string", "double", ...
    if isinstance(node, str):
        if node not in _PRIMITIVES:
            raise SchemaError(f"unknown or unsupported type name: {node!r}")
        return PrimitiveSchema(node)

    # A JSON list is a union.
    if isinstance(node, list):
        return _parse_union(node, seen_names, inside_record)

    if not isinstance(node, dict) or "type" not in node:
        raise SchemaError(f"schema must be a name, a list or an object with 'type': {node!r}")

    kind = node["type"]

    if kind == "record":
        if inside_record:
            raise SchemaError("nested records are not supported")
        return _parse_record(node, seen_names)
    if kind == "enum":
        return _parse_enum(node, seen_names)
    if kind in _PRIMITIVES:
        return _parse_primitive_object(node, kind)
    if isinstance(kind, (list, dict)):
        # e.g. {"type": ["null", "string"]} -- unwrap one level.
        return _parse(kind, seen_names, inside_record)
    raise SchemaError(f"unknown or unsupported type: {kind!r}")


def _parse_primitive_object(node: dict, kind: str) -> PrimitiveSchema:
    """A primitive written in object form, possibly with a logicalType."""
    logical = node.get("logicalType")
    if logical is None:
        return PrimitiveSchema(kind)
    if logical not in _SUPPORTED_LOGICAL_TYPES or kind != "long":
        raise SchemaError(f"unsupported logicalType {logical!r} on {kind!r}")
    return PrimitiveSchema(kind, logical)


def _parse_enum(node: dict, seen_names: set) -> EnumSchema:
    name = _check_name(node.get("name"), "enum")
    if name in seen_names:
        raise SchemaError(f"duplicate type name: {name}")
    seen_names.add(name)

    symbols = node.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise SchemaError(f"enum {name} needs a non-empty 'symbols' list")
    for s in symbols:
        _check_name(s, f"enum {name} symbol")
    if len(set(symbols)) != len(symbols):
        raise SchemaError(f"enum {name} has duplicate symbols")

    default = node.get("default")
    if default is not None and default not in symbols:
        raise SchemaError(f"enum {name} default {default!r} is not one of its symbols")
    return EnumSchema(name, tuple(symbols), default)


def _parse_union(branches: list, seen_names: set, inside_record: bool) -> UnionSchema:
    parsed = tuple(_parse(b, seen_names, inside_record) for b in branches)
    # We only support the "optional value" pattern.  General unions would need
    # tagged-branch matching rules we do not need and have not tested.
    is_null = [isinstance(p, PrimitiveSchema) and p.name == "null" for p in parsed]
    if len(parsed) != 2 or sum(is_null) != 1:
        raise SchemaError("only unions of exactly [null, T] or [T, null] are supported")
    other = parsed[1] if is_null[0] else parsed[0]
    if isinstance(other, UnionSchema):
        raise SchemaError("unions may not be nested")
    return UnionSchema(parsed)


def _parse_record(node: dict, seen_names: set) -> RecordSchema:
    raw_name = node.get("name")
    if not isinstance(raw_name, str):
        raise SchemaError(f"invalid record name: {raw_name!r}")
    # A dotted name like "ilmop.telemetry.Telemetry" carries its namespace.
    namespace = node.get("namespace")
    if "." in raw_name:
        namespace, _, raw_name = raw_name.rpartition(".")
    name = _check_name(raw_name, "record")
    if namespace is not None:
        for part in str(namespace).split("."):
            _check_name(part, "namespace")
    if name in seen_names:
        raise SchemaError(f"duplicate type name: {name}")
    seen_names.add(name)

    raw_fields = node.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise SchemaError(f"record {name} needs a non-empty 'fields' list")

    fields: List[FieldSchema] = []
    field_names = set()
    for rf in raw_fields:
        if not isinstance(rf, dict) or "type" not in rf:
            raise SchemaError(f"record {name}: each field needs 'name' and 'type'")
        fname = _check_name(rf.get("name"), f"record {name} field")
        if fname in field_names:
            raise SchemaError(f"record {name} has duplicate field {fname}")
        field_names.add(fname)

        ftype = _parse(rf["type"], seen_names, inside_record=True)

        has_default = "default" in rf
        default = rf.get("default")
        if has_default and not _default_is_valid(ftype, default):
            raise SchemaError(
                f"record {name} field {fname}: default {default!r} does not match its type")
        fields.append(FieldSchema(fname, ftype, has_default, default, rf.get("doc")))

    return RecordSchema(name, tuple(fields), namespace, node.get("doc"))


def _default_is_valid(schema: Schema, value: Any) -> bool:
    """Does a field's default value fit the field's type?"""
    if isinstance(schema, PrimitiveSchema):
        if schema.name == "null":
            return value is None
        if schema.name == "boolean":
            return isinstance(value, bool)
        if schema.name == "long":
            return isinstance(value, int) and not isinstance(value, bool)
        if schema.name == "double":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if schema.name == "string":
            return isinstance(value, str)
    if isinstance(schema, EnumSchema):
        return isinstance(value, str) and value in schema.symbols
    if isinstance(schema, UnionSchema):
        # Avro rule: a union's default must match its FIRST branch.
        return _default_is_valid(schema.branches[0], value)
    return False


# ── Back to JSON, canonical form and fingerprint ─────────────────────────────

def to_json(schema: Schema, include_doc: bool = True) -> Any:
    """Turn a schema object back into plain JSON-compatible Python data."""
    if isinstance(schema, PrimitiveSchema):
        if schema.logical_type:
            return {"type": schema.name, "logicalType": schema.logical_type}
        return schema.name
    if isinstance(schema, EnumSchema):
        out: Dict[str, Any] = {"type": "enum", "name": schema.name,
                               "symbols": list(schema.symbols)}
        if schema.default is not None:
            out["default"] = schema.default
        return out
    if isinstance(schema, UnionSchema):
        return [to_json(b, include_doc) for b in schema.branches]
    # RecordSchema
    out = {"type": "record", "name": schema.name}
    if schema.namespace:
        out["namespace"] = schema.namespace
    if include_doc and schema.doc:
        out["doc"] = schema.doc
    fields = []
    for f in schema.fields:
        fo: Dict[str, Any] = {"name": f.name, "type": to_json(f.type, include_doc)}
        if f.has_default:
            fo["default"] = f.default
        if include_doc and f.doc:
            fo["doc"] = f.doc
        fields.append(fo)
    out["fields"] = fields
    return out


def canonical_json(schema: Schema) -> str:
    """
    A single, whitespace-free, key-sorted JSON string for the schema.

    Two schemas that mean the same thing but were written with different
    spacing, key order or documentation text give the SAME canonical string.
    Unlike Avro's official "Parsing Canonical Form", this keeps field
    DEFAULTS (they decide compatibility, so a change to one is a real change)
    and drops only documentation.  Field ORDER is preserved, because it
    determines the byte layout.
    """
    return json.dumps(to_json(schema, include_doc=False),
                      sort_keys=True, separators=(",", ":"))


def fingerprint(schema: Schema) -> str:
    """SHA-256 (hex) of the canonical form: the schema's identity."""
    return hashlib.sha256(canonical_json(schema).encode("utf-8")).hexdigest()


def describe(schema: Schema) -> str:
    """Short human-readable name of a schema, for error messages."""
    if isinstance(schema, PrimitiveSchema):
        return f"{schema.name}({schema.logical_type})" if schema.logical_type else schema.name
    if isinstance(schema, EnumSchema):
        return f"enum {schema.name}"
    if isinstance(schema, UnionSchema):
        return "union[" + ", ".join(describe(b) for b in schema.branches) + "]"
    return f"record {schema.name}"
