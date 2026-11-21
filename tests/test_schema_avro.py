"""
tests/test_schema_avro.py

Tests for Avro schema parsing (avro_schema.py) and binary encoding
(avro_codec.py).

The codec is hand-written, so besides the usual tests it is checked three
independent ways:
  1. known-answer vectors taken from the Avro specification,
  2. round trips of awkward values (extremes, non-ASCII, NaN),
  3. byte-for-byte comparison with the `fastavro` library, when installed.
"""

import math

import pytest

from services.schema_registry import avro_codec as codec
from services.schema_registry.avro_schema import (
    EnumSchema, PrimitiveSchema, RecordSchema, SchemaError, UnionSchema,
    canonical_json, fingerprint, parse_schema, to_json,
)

POINT = {
    "type": "record", "name": "Point", "namespace": "demo",
    "fields": [
        {"name": "label", "type": "string"},
        {"name": "x", "type": "double"},
        {"name": "n", "type": "long", "default": 0},
        {"name": "ok", "type": "boolean", "default": False},
        {"name": "kind", "type": {"type": "enum", "name": "Kind", "symbols": ["A", "B", "C"]},
         "default": "A"},
        {"name": "note", "type": ["null", "string"], "default": None},
        {"name": "at", "type": {"type": "long", "logicalType": "timestamp-micros"},
         "default": 0},
    ],
}


def _point(**over):
    base = {"label": "p", "x": 1.5, "n": 7, "ok": True, "kind": "B", "note": "hi", "at": 12345}
    base.update(over)
    return base


# ── Parsing ───────────────────────────────────────────────────────────────────

class TestParsing:

    def test_parses_a_record(self):
        s = parse_schema(POINT)
        assert isinstance(s, RecordSchema) and s.full_name == "demo.Point"
        assert [f.name for f in s.fields][:2] == ["label", "x"]

    def test_accepts_a_json_string(self):
        import json
        assert isinstance(parse_schema(json.dumps(POINT)), RecordSchema)

    def test_bare_primitive_name(self):
        assert parse_schema("string") == PrimitiveSchema("string")
        assert parse_schema('"double"') == PrimitiveSchema("double")

    def test_dotted_name_carries_the_namespace(self):
        s = parse_schema({"type": "record", "name": "a.b.R",
                          "fields": [{"name": "x", "type": "long"}]})
        assert (s.namespace, s.name) == ("a.b", "R")

    def test_optional_union_either_order(self):
        assert isinstance(parse_schema(["null", "string"]), UnionSchema)
        assert isinstance(parse_schema(["string", "null"]), UnionSchema)

    def test_enum_default_is_kept(self):
        s = parse_schema({"type": "enum", "name": "E", "symbols": ["X", "Y"], "default": "X"})
        assert isinstance(s, EnumSchema) and s.default == "X"

    @pytest.mark.parametrize("bad", [
        "float", "bytes", "int",                                        # unsupported primitives
        {"type": "array", "items": "long"}, {"type": "map", "values": "long"},
        ["string", "long"], ["null", "string", "long"], [["null", "string"], "null"],
        {"type": "long", "logicalType": "date"},                        # unsupported logical type
        {"type": "string", "logicalType": "timestamp-micros"},          # logical type on wrong base
        {"type": "enum", "name": "E", "symbols": []},
        {"type": "enum", "name": "E", "symbols": ["A", "A"]},
        {"type": "enum", "name": "E", "symbols": ["A-B"]},
        {"type": "enum", "name": "E", "symbols": ["A"], "default": "Z"},
        {"type": "record", "name": "R", "fields": []},
        {"type": "record", "name": "1bad", "fields": [{"name": "x", "type": "long"}]},
        {"type": "record", "name": "R", "fields": [{"name": "x", "type": "long"},
                                                   {"name": "x", "type": "long"}]},
        {"type": "record", "name": "R", "fields": [{"name": "x", "type": "long", "default": "s"}]},
        {"type": "record", "name": "R", "fields": [{"name": "x", "type": "boolean",
                                                    "default": 1}]},
        {"type": "record", "name": "R", "fields": [{"name": "x", "type": ["null", "string"],
                                                    "default": "s_not_first_branch"}]},
        {"type": "record", "name": "R", "fields": [{"name": "i", "type": {
            "type": "record", "name": "In", "fields": [{"name": "y", "type": "long"}]}}]},
        {"type": "nonsense"}, 42, {"name": "no-type"},
    ])
    def test_rejects_malformed_or_unsupported(self, bad):
        with pytest.raises(SchemaError):
            parse_schema(bad)

    def test_default_must_match_first_union_branch(self):
        # ["null","string"] -> default must be null; ["string","null"] -> a string.
        parse_schema({"type": "record", "name": "R", "fields": [
            {"name": "x", "type": ["string", "null"], "default": "ok"}]})


class TestCanonicalForm:

    def test_whitespace_key_order_and_doc_do_not_change_identity(self):
        a = parse_schema(POINT)
        reordered = dict(reversed(list(POINT.items())))
        reordered["doc"] = "documentation text"
        assert fingerprint(parse_schema(reordered)) == fingerprint(a)

    def test_default_change_changes_identity(self):
        changed = {**POINT, "fields": [dict(f) for f in POINT["fields"]]}
        changed["fields"][2]["default"] = 1
        assert fingerprint(parse_schema(changed)) != fingerprint(parse_schema(POINT))

    def test_field_order_change_changes_identity(self):
        swapped = {**POINT, "fields": list(reversed(POINT["fields"]))}
        assert fingerprint(parse_schema(swapped)) != fingerprint(parse_schema(POINT))

    def test_to_json_round_trips(self):
        s = parse_schema(POINT)
        assert parse_schema(to_json(s)) == s
        assert canonical_json(parse_schema(to_json(s))) == canonical_json(s)


# ── Known-answer vectors from the Avro specification ─────────────────────────

class TestVarintVectors:

    @pytest.mark.parametrize("value, hexbytes", [
        (0, "00"), (-1, "01"), (1, "02"), (-2, "03"), (2, "04"),
        (63, "7e"), (-64, "7f"), (64, "8001"), (-65, "8101"),
        (8192, "808001"),
    ])
    def test_long_encoding(self, value, hexbytes):
        assert codec.encode(PrimitiveSchema("long"), value).hex() == hexbytes
        assert codec.decode(bytes.fromhex(hexbytes), PrimitiveSchema("long")) == value

    @pytest.mark.parametrize("value", [2**63 - 1, -2**63, 2**31, -2**31, 2**53 + 1])
    def test_long_extremes_round_trip(self, value):
        data = codec.encode(PrimitiveSchema("long"), value)
        assert codec.decode(data, PrimitiveSchema("long")) == value

    @pytest.mark.parametrize("value", [2**63, -2**63 - 1])
    def test_long_out_of_range_is_rejected(self, value):
        with pytest.raises(codec.AvroEncodeError):
            codec.encode(PrimitiveSchema("long"), value)

    def test_string_known_answer(self):
        # length 3 (zig-zag 06) then the UTF-8 bytes
        assert codec.encode(PrimitiveSchema("string"), "foo").hex() == "06666f6f"

    def test_double_is_little_endian(self):
        assert codec.encode(PrimitiveSchema("double"), 1.0).hex() == "000000000000f03f"

    def test_boolean_bytes(self):
        assert codec.encode(PrimitiveSchema("boolean"), True) == b"\x01"
        assert codec.encode(PrimitiveSchema("boolean"), False) == b"\x00"


# ── Round trips ───────────────────────────────────────────────────────────────

class TestRoundTrip:
    schema = parse_schema(POINT)

    def test_full_record(self):
        assert codec.decode(codec.encode(self.schema, _point()), self.schema) == _point()

    def test_optional_null_and_value(self):
        for note in (None, "x", ""):
            v = _point(note=note)
            assert codec.decode(codec.encode(self.schema, v), self.schema)["note"] == note

    def test_non_ascii_strings(self):
        v = _point(label="Ångström – 衛星 🛰")
        assert codec.decode(codec.encode(self.schema, v), self.schema)["label"] == v["label"]

    @pytest.mark.parametrize("x", [0.0, -0.0, 1e308, -1e308, 5e-324, math.inf, -math.inf])
    def test_double_extremes(self, x):
        out = codec.decode(codec.encode(self.schema, _point(x=x)), self.schema)["x"]
        assert out == x and math.copysign(1, out) == math.copysign(1, x)

    def test_nan_round_trips(self):
        out = codec.decode(codec.encode(self.schema, _point(x=math.nan)), self.schema)["x"]
        assert math.isnan(out)

    def test_defaults_fill_missing_fields_on_encode(self):
        v = {"label": "p", "x": 1.0}
        out = codec.decode(codec.encode(self.schema, v), self.schema)
        assert (out["n"], out["ok"], out["kind"], out["note"]) == (0, False, "A", None)

    def test_int_accepted_for_double(self):
        assert codec.decode(codec.encode(self.schema, _point(x=3)), self.schema)["x"] == 3.0


class TestEncodeErrors:
    schema = parse_schema(POINT)

    @pytest.mark.parametrize("over", [
        {"label": 5}, {"x": "1.0"}, {"x": True}, {"n": 1.5}, {"n": True},
        {"ok": 1}, {"kind": "Z"}, {"kind": 0}, {"note": 5}, {"label": "\ud800"},
    ])
    def test_wrong_type_is_rejected(self, over):
        with pytest.raises(codec.AvroEncodeError):
            codec.encode(self.schema, _point(**over))

    def test_missing_required_field(self):
        with pytest.raises(codec.AvroEncodeError, match="label"):
            codec.encode(self.schema, {"x": 1.0})

    def test_unknown_field_is_rejected(self):
        with pytest.raises(codec.AvroEncodeError, match="unknown"):
            codec.encode(self.schema, _point(typo=1))

    def test_not_a_dict(self):
        with pytest.raises(codec.AvroEncodeError):
            codec.encode(self.schema, [1, 2])

    def test_error_names_the_path(self):
        with pytest.raises(codec.AvroEncodeError, match=r"\$\.x"):
            codec.encode(self.schema, _point(x="bad"))


class TestDecodeErrors:
    schema = parse_schema(POINT)

    def test_every_truncation_is_detected(self):
        data = codec.encode(self.schema, _point())
        for cut in range(len(data)):
            with pytest.raises(codec.AvroDecodeError):
                codec.decode(data[:cut], self.schema)

    def test_trailing_bytes_are_an_error(self):
        with pytest.raises(codec.AvroDecodeError, match="trailing"):
            codec.decode(codec.encode(self.schema, _point()) + b"\x00", self.schema)

    def test_trailing_bytes_allowed_on_request(self):
        data = codec.encode(self.schema, _point()) + b"\x00"
        assert codec.decode(data, self.schema, allow_trailing=True) == _point()

    def test_bad_boolean_byte(self):
        with pytest.raises(codec.AvroDecodeError):
            codec.decode(b"\x02", PrimitiveSchema("boolean"))

    def test_enum_index_out_of_range(self):
        enum = parse_schema({"type": "enum", "name": "E", "symbols": ["A", "B"]})
        with pytest.raises(codec.AvroDecodeError):
            codec.decode(b"\x06", enum)                    # zig-zag 3 -> index 3

    def test_union_index_out_of_range(self):
        with pytest.raises(codec.AvroDecodeError):
            codec.decode(b"\x08", parse_schema(["null", "string"]))

    def test_negative_string_length(self):
        with pytest.raises(codec.AvroDecodeError):
            codec.decode(b"\x01", PrimitiveSchema("string"))   # zig-zag 1 -> -1

    def test_invalid_utf8(self):
        with pytest.raises(codec.AvroDecodeError, match="UTF-8"):
            codec.decode(b"\x02\xff", PrimitiveSchema("string"))

    def test_overlong_varint(self):
        with pytest.raises(codec.AvroDecodeError):
            codec.decode(b"\x80" * 11, PrimitiveSchema("long"))

    def test_varint_beyond_64_bits(self):
        with pytest.raises(codec.AvroDecodeError):
            codec.decode(b"\xff" * 9 + b"\x7f", PrimitiveSchema("long"))

    def test_random_garbage_never_raises_anything_else(self):
        import random
        rng = random.Random(7)
        for _ in range(2000):
            junk = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
            try:
                codec.decode(junk, self.schema)
            except codec.AvroDecodeError:
                pass                                      # the only acceptable failure


# ── Independent cross-check against fastavro ─────────────────────────────────

class TestAgainstFastavro:
    """
    Compares our bytes with the widely used fastavro library.  This is the
    protection against a subtle bug in the hand-written encoder.  Skipped if
    fastavro is not installed (it is NOT a project dependency).
    """

    @pytest.fixture(autouse=True)
    def _need_fastavro(self):
        pytest.importorskip("fastavro")

    def _fast(self, schema_json):
        import fastavro
        return fastavro.parse_schema(schema_json)

    def test_encoded_bytes_are_identical(self):
        import io, fastavro
        schema = parse_schema(POINT)
        fast = self._fast(POINT)
        for v in (_point(), _point(note=None, x=-2.25, n=-5000000000, kind="C"),
                  _point(label="é衛", at=-1)):
            buf = io.BytesIO()
            fastavro.schemaless_writer(buf, fast, v)
            assert codec.encode(schema, v) == buf.getvalue()

    def test_we_can_decode_what_fastavro_encodes(self):
        import io, fastavro
        buf = io.BytesIO()
        fastavro.schemaless_writer(buf, self._fast(POINT), _point(n=2**40))
        out = codec.decode(buf.getvalue(), parse_schema(POINT))
        # timestamp-micros: our codec returns the raw int, fastavro a datetime.
        assert out["n"] == 2**40 and out["label"] == "p"

    def test_fastavro_can_decode_what_we_encode(self):
        import io, fastavro
        data = codec.encode(parse_schema(POINT), _point(at=0))
        out = fastavro.schemaless_reader(io.BytesIO(data), self._fast(POINT))
        assert out["label"] == "p" and out["x"] == 1.5 and out["kind"] == "B"
