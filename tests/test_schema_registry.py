"""
tests/test_schema_registry.py

Tests for schema resolution, compatibility modes (S8-02) and the registry
(S8-01).

A small base schema "v1" is evolved in different ways.  Each way is either
allowed or forbidden under each mode; the tests state that table explicitly.
"""

import copy

import pytest

from services.schema_registry import avro_codec as codec
from services.schema_registry.avro_schema import parse_schema
from services.schema_registry.compatibility import (
    CompatibilityMode as M, can_read, check_compatibility,
)
from services.schema_registry.registry import (
    IncompatibleSchemaError, SchemaNotFoundError, SchemaRegistry, SubjectNotFoundError,
)

# ── A base schema and helpers to evolve it ────────────────────────────────────

V1 = {
    "type": "record", "name": "Rec", "namespace": "t",
    "fields": [
        {"name": "id", "type": "string"},
        {"name": "level", "type": "double"},
        {"name": "mode", "type": {"type": "enum", "name": "Mode", "symbols": ["A", "B"]}},
    ],
}


def evolve(add=None, remove=None, change=None, mode_symbols=None, mode_default=None):
    """Return a modified copy of V1.  add: field dicts; remove: names; change: {name: type}."""
    s = copy.deepcopy(V1)
    if remove:
        s["fields"] = [f for f in s["fields"] if f["name"] not in remove]
    for f in s["fields"]:
        if change and f["name"] in change:
            f["type"] = change[f["name"]]
        if f["name"] == "mode" and mode_symbols is not None:
            f["type"] = {"type": "enum", "name": "Mode", "symbols": mode_symbols,
                         **({"default": mode_default} if mode_default else {})}
    s["fields"] += add or []
    return s


def problems(old, new, mode):
    return check_compatibility(parse_schema(new), [parse_schema(old)], mode)


NEW_WITH_DEFAULT = {"name": "extra", "type": "long", "default": 0}
NEW_NO_DEFAULT = {"name": "extra", "type": "long"}


# ── can_read: the resolution rules ────────────────────────────────────────────

class TestResolutionRules:

    def test_identical_schemas(self):
        s = parse_schema(V1)
        assert can_read(s, s) == []

    def test_reader_field_with_default_is_fine(self):
        assert can_read(parse_schema(V1), parse_schema(evolve(add=[NEW_WITH_DEFAULT]))) == []

    def test_reader_field_without_default_is_a_problem(self):
        out = can_read(parse_schema(V1), parse_schema(evolve(add=[NEW_NO_DEFAULT])))
        assert len(out) == 1 and "extra" in out[0] and "default" in out[0]

    def test_writer_extra_field_is_ignored(self):
        assert can_read(parse_schema(evolve(add=[NEW_NO_DEFAULT])), parse_schema(V1)) == []

    def test_field_order_does_not_matter(self):
        reordered = copy.deepcopy(V1)
        reordered["fields"].reverse()
        assert can_read(parse_schema(V1), parse_schema(reordered)) == []

    def test_type_change_is_a_problem(self):
        out = can_read(parse_schema(V1), parse_schema(evolve(change={"level": "string"})))
        assert out and "level" in out[0]

    def test_long_to_double_promotion_is_allowed(self):
        writer = parse_schema(evolve(change={"level": "long"}))
        assert can_read(writer, parse_schema(V1)) == []

    def test_double_to_long_is_not(self):
        reader = parse_schema(evolve(change={"level": "long"}))
        assert can_read(parse_schema(V1), reader) != []

    def test_logical_type_must_agree(self):
        ts = {"type": "long", "logicalType": "timestamp-micros"}
        assert can_read(parse_schema(evolve(change={"level": "long"})),
                        parse_schema(evolve(change={"level": ts}))) != []

    def test_new_enum_symbol_unreadable_without_enum_default(self):
        out = can_read(parse_schema(evolve(mode_symbols=["A", "B", "C"])), parse_schema(V1))
        assert out and "'C'" in out[0]

    def test_new_enum_symbol_readable_with_enum_default(self):
        old_reader = evolve(mode_symbols=["A", "B"], mode_default="A")
        new_writer = evolve(mode_symbols=["A", "B", "C"], mode_default="A")
        assert can_read(parse_schema(new_writer), parse_schema(old_reader)) == []

    def test_removed_enum_symbol_is_fine_for_a_reader(self):
        # Reader knows MORE symbols than the writer used.
        assert can_read(parse_schema(V1), parse_schema(evolve(mode_symbols=["A", "B", "C"]))) == []

    def test_record_name_must_match(self):
        other = {**V1, "name": "Other"}
        assert can_read(parse_schema(V1), parse_schema(other)) != []

    def test_union_resolution(self):
        opt = evolve(change={"level": ["null", "double"]})
        # writer non-optional -> reader optional: fine.  The reverse can meet a null: not fine.
        assert can_read(parse_schema(V1), parse_schema(opt)) == []
        assert can_read(parse_schema(opt), parse_schema(V1)) != []

    def test_all_problems_are_reported_not_just_the_first(self):
        new = evolve(add=[NEW_NO_DEFAULT], change={"level": "string"})
        assert len(can_read(parse_schema(V1), parse_schema(new))) == 2


# ── Decoding with resolution (the runtime half of S8-02) ─────────────────────

class TestDecodeWithResolution:

    def test_old_data_new_reader_gets_default(self):
        old, new = parse_schema(V1), parse_schema(evolve(add=[NEW_WITH_DEFAULT]))
        data = codec.encode(old, {"id": "x", "level": 1.5, "mode": "B"})
        assert codec.decode(data, old, new) == {"id": "x", "level": 1.5, "mode": "B", "extra": 0}

    def test_new_data_old_reader_skips_new_field(self):
        old, new = parse_schema(V1), parse_schema(evolve(add=[NEW_WITH_DEFAULT]))
        data = codec.encode(new, {"id": "x", "level": 1.5, "mode": "B", "extra": 99})
        assert codec.decode(data, new, old) == {"id": "x", "level": 1.5, "mode": "B"}

    def test_skipping_a_field_in_the_middle_keeps_later_fields_aligned(self):
        # 'level' is removed from the reader; 'mode' after it must still decode right.
        w, r = parse_schema(V1), parse_schema(evolve(remove=["level"]))
        data = codec.encode(w, {"id": "abc", "level": 123456.789, "mode": "B"})
        assert codec.decode(data, w, r) == {"id": "abc", "mode": "B"}

    def test_reader_field_order_is_used(self):
        w = parse_schema(V1)
        rev = copy.deepcopy(V1)
        rev["fields"].reverse()
        data = codec.encode(w, {"id": "x", "level": 2.0, "mode": "A"})
        assert list(codec.decode(data, w, parse_schema(rev))) == ["mode", "level", "id"]

    def test_long_promoted_to_double(self):
        w = parse_schema(evolve(change={"level": "long"}))
        data = codec.encode(w, {"id": "x", "level": 7, "mode": "A"})
        out = codec.decode(data, w, parse_schema(V1))
        assert out["level"] == 7.0 and isinstance(out["level"], float)

    def test_unknown_enum_symbol_becomes_enum_default(self):
        w = parse_schema(evolve(mode_symbols=["A", "B", "C"], mode_default="A"))
        r = parse_schema(evolve(mode_symbols=["A", "B"], mode_default="A"))
        data = codec.encode(w, {"id": "x", "level": 1.0, "mode": "C"})
        assert codec.decode(data, w, r)["mode"] == "A"

    def test_incompatible_reader_fails_before_reading_any_byte(self):
        w, r = parse_schema(V1), parse_schema(evolve(add=[NEW_NO_DEFAULT]))
        data = codec.encode(w, {"id": "x", "level": 1.0, "mode": "A"})
        with pytest.raises(codec.SchemaResolutionError) as exc:
            codec.decode(data, w, r)
        assert exc.value.problems and "extra" in str(exc.value)

    def test_optional_field_resolution(self):
        w = parse_schema(V1)
        r = parse_schema(evolve(change={"level": ["null", "double"]}))
        data = codec.encode(w, {"id": "x", "level": 4.5, "mode": "A"})
        assert codec.decode(data, w, r)["level"] == 4.5

    def test_data_written_as_union_read_by_plain_reader(self):
        w = parse_schema(evolve(change={"level": ["null", "double"]}))
        r = parse_schema(evolve(change={"level": ["null", "double"]}))
        for level in (None, 3.0):
            data = codec.encode(w, {"id": "x", "level": level, "mode": "A"})
            assert codec.decode(data, w, r)["level"] == level


# ── The compatibility modes ───────────────────────────────────────────────────

class TestModes:
    """
    The evolution table.  Columns: BACKWARD / FORWARD / FULL.
        change                      BACKWARD  FORWARD  FULL
        add field WITH default        ok        ok      ok
        add field WITHOUT default     no        ok      no
        remove field                  ok        no*     no*      (*no if the removed field has no default)
        remove field WITH default     ok        ok      ok
        change a type                 no        no      no
    """

    @pytest.mark.parametrize("mode, ok", [
        (M.BACKWARD, True), (M.FORWARD, True), (M.FULL, True), (M.NONE, True)])
    def test_add_field_with_default(self, mode, ok):
        assert (problems(V1, evolve(add=[NEW_WITH_DEFAULT]), mode) == []) is ok

    @pytest.mark.parametrize("mode, ok", [
        (M.BACKWARD, False), (M.FORWARD, True), (M.FULL, False), (M.NONE, True)])
    def test_add_field_without_default(self, mode, ok):
        assert (problems(V1, evolve(add=[NEW_NO_DEFAULT]), mode) == []) is ok

    @pytest.mark.parametrize("mode, ok", [
        (M.BACKWARD, True), (M.FORWARD, False), (M.FULL, False), (M.NONE, True)])
    def test_remove_field_without_default(self, mode, ok):
        assert (problems(V1, evolve(remove=["level"]), mode) == []) is ok

    @pytest.mark.parametrize("mode", [M.BACKWARD, M.FORWARD, M.FULL])
    def test_remove_field_that_had_a_default(self, mode):
        # The old schema has an optional-by-default field 'extra'; the new one drops it.
        # Old readers fall back to the default, new readers never ask for it: safe.
        old = evolve(add=[NEW_WITH_DEFAULT])
        assert problems(old, V1, mode) == []

    @pytest.mark.parametrize("mode", [M.BACKWARD, M.FORWARD, M.FULL])
    def test_type_change_breaks_every_checked_mode(self, mode):
        assert problems(V1, evolve(change={"level": "string"}), mode) != []

    def test_none_allows_anything(self):
        assert problems(V1, {"type": "record", "name": "Totally", "fields": [
            {"name": "z", "type": "boolean"}]}, M.NONE) == []

    def test_first_version_is_always_accepted(self):
        assert check_compatibility(parse_schema(V1), [], M.FULL) == []

    def test_problem_messages_say_which_direction_failed(self):
        out = problems(V1, evolve(add=[NEW_NO_DEFAULT]), M.FULL)
        assert out and out[0].startswith("[BACKWARD")

    def test_new_enum_symbol_is_forward_incompatible(self):
        new = evolve(mode_symbols=["A", "B", "C"])
        assert problems(V1, new, M.BACKWARD) == []          # new reads old: fine
        assert problems(V1, new, M.FORWARD) != []           # old cannot read 'C'

    def test_transitive_checks_every_earlier_version(self):
        v1 = parse_schema(V1)
        v2 = parse_schema(evolve(add=[NEW_WITH_DEFAULT]))
        # v3 drops 'level', which v1 and v2 had WITHOUT a default: a v1 or v2
        # reader cannot read v3 data, so FORWARD fails, BACKWARD still holds.
        v3 = parse_schema(evolve(remove=["level"], add=[NEW_WITH_DEFAULT]))
        assert check_compatibility(v3, [v1, v2], M.BACKWARD_TRANSITIVE) == []
        assert check_compatibility(v3, [v1, v2], M.FORWARD) != []

    def test_non_transitive_only_looks_at_the_latest(self):
        # An early version whose 'level' is a string can never be read by a
        # reader that expects a double.  The latest version is fine.
        early = parse_schema(evolve(change={"level": "string"}))
        latest = parse_schema(evolve(add=[NEW_WITH_DEFAULT]))
        candidate = parse_schema(V1)
        # Non-transitive: compared with the latest only -> accepted.
        assert check_compatibility(candidate, [early, latest], M.BACKWARD) == []
        # Transitive: the early version is checked too -> rejected.
        assert check_compatibility(candidate, [early, latest], M.BACKWARD_TRANSITIVE) != []


# ── The registry ──────────────────────────────────────────────────────────────

class TestRegistry:

    def test_ids_and_versions_start_at_one(self):
        r = SchemaRegistry()
        assert r.register("s", V1) == 1
        assert r.register("s", evolve(add=[NEW_WITH_DEFAULT])) == 2
        assert r.versions("s") == [1, 2]

    def test_registering_the_same_schema_is_idempotent(self):
        r = SchemaRegistry()
        first = r.register("s", V1)
        assert r.register("s", copy.deepcopy(V1)) == first
        assert r.versions("s") == [1]

    def test_formatting_differences_do_not_create_versions(self):
        import json
        r = SchemaRegistry()
        first = r.register("s", V1)
        doc_added = {**V1, "doc": "now with documentation"}
        assert r.register("s", json.dumps(doc_added, indent=4)) == first
        assert r.versions("s") == [1]

    def test_same_schema_under_two_subjects_shares_one_id(self):
        r = SchemaRegistry()
        assert r.register("a", V1) == r.register("b", V1)
        assert r.latest("a").version == r.latest("b").version == 1

    def test_incompatible_registration_is_rejected_with_reasons(self):
        r = SchemaRegistry()
        r.register("s", V1)
        with pytest.raises(IncompatibleSchemaError) as exc:
            r.register("s", evolve(add=[NEW_NO_DEFAULT]))
        assert exc.value.subject == "s" and exc.value.mode == M.BACKWARD
        assert any("extra" in p for p in exc.value.problems)

    def test_a_rejected_schema_leaves_no_trace(self):
        r = SchemaRegistry()
        r.register("s", V1)
        with pytest.raises(IncompatibleSchemaError):
            r.register("s", evolve(add=[NEW_NO_DEFAULT]))
        assert r.versions("s") == [1]
        assert r.register("s", evolve(add=[NEW_WITH_DEFAULT])) == 2   # IDs not wasted

    def test_per_subject_mode_overrides_the_default(self):
        r = SchemaRegistry()
        r.set_compatibility("loose", M.NONE)
        r.register("loose", V1)
        r.register("loose", evolve(add=[NEW_NO_DEFAULT]))             # allowed under NONE
        r.register("strict", V1)
        with pytest.raises(IncompatibleSchemaError):
            r.register("strict", evolve(add=[NEW_NO_DEFAULT]))

    def test_changing_the_mode_applies_to_later_registrations(self):
        r = SchemaRegistry()
        r.register("s", V1)
        r.set_compatibility("s", M.FORWARD)
        r.register("s", evolve(add=[NEW_NO_DEFAULT]))                 # forward-compatible
        assert r.get_compatibility("s") == M.FORWARD

    def test_check_is_a_dry_run(self):
        r = SchemaRegistry()
        r.register("s", V1)
        assert r.check("s", evolve(add=[NEW_NO_DEFAULT])) != []
        assert r.check("s", evolve(add=[NEW_WITH_DEFAULT])) == []
        assert r.versions("s") == [1]

    def test_lookup_by_id_and_version(self):
        r = SchemaRegistry()
        sid = r.register("s", V1)
        assert r.get_schema(sid) == parse_schema(V1)
        assert r.get_version("s", 1).schema_id == sid
        assert r.latest("s").version == 1

    def test_latest_follows_new_versions(self):
        r = SchemaRegistry()
        r.register("s", V1)
        r.register("s", evolve(add=[NEW_WITH_DEFAULT]))
        assert r.latest("s").version == 2

    def test_unknown_things_raise_clear_errors(self):
        r = SchemaRegistry()
        r.register("s", V1)
        with pytest.raises(SchemaNotFoundError):
            r.get_schema(99)
        with pytest.raises(SchemaNotFoundError):
            r.get_version("s", 2)
        with pytest.raises(SchemaNotFoundError):
            r.get_version("s", 0)
        with pytest.raises(SubjectNotFoundError):
            r.latest("nope")
        with pytest.raises(SubjectNotFoundError):
            r.versions("nope")

    def test_find_id(self):
        r = SchemaRegistry()
        sid = r.register("s", V1)
        assert r.find_id("s", V1) == sid
        with pytest.raises(SchemaNotFoundError):
            r.find_id("s", evolve(add=[NEW_WITH_DEFAULT]))

    def test_subjects_listing(self):
        r = SchemaRegistry()
        r.register("b", V1)
        r.register("a", V1)
        assert r.subjects() == ["a", "b"]

    def test_failed_first_registration_leaves_no_empty_subject(self):
        r = SchemaRegistry()
        with pytest.raises(Exception):
            r.register("s", {"type": "record", "name": "R", "fields": []})
        assert r.subjects() == []

    def test_concurrent_registration_gives_consistent_ids(self):
        import threading
        r = SchemaRegistry()
        ids = []

        def work():
            ids.append(r.register("s", V1))

        threads = [threading.Thread(target=work) for _ in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert set(ids) == {1} and r.versions("s") == [1]
