"""
tests/test_schema_serde.py

End-to-end tests of the Avro contract for the real project models:
generated schemas, the Kafka wire format, round trips of Telemetry and Alarm,
and the real v2.0 -> v2.1 evolution (orbit_type, ADR-016) in both directions.
"""

import json
import struct
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from services.schema_registry import generate as gen
from services.schema_registry.avro_codec import AvroDecodeError, encode
from services.schema_registry.avro_schema import RecordSchema, parse_schema
from services.schema_registry.compatibility import CompatibilityMode, can_read
from services.schema_registry.defaults import build_default_registry, load_avsc
from services.schema_registry.registry import (
    IncompatibleSchemaError, SchemaNotFoundError,
)
from services.schema_registry.serde import (
    AvroSerde, WireFormatError, datetime_to_micros, micros_to_datetime, model_to_record,
    pack, unpack,
)
from shared.schemas.alarm_schema import Alarm
from shared.schemas.telemetry_schema import Telemetry

T0 = datetime(2026, 11, 21, 12, 0, 0, 123456, tzinfo=timezone.utc)


def _telemetry(**over):
    base = dict(
        satellite_id="SAT-001", timestamp=T0, latitude_deg=-12.3456789,
        longitude_deg=170.25, altitude_km=550.125, in_eclipse=True, in_contact=False,
        battery_pct=81.5, battery_voltage_v=28.1, solar_panel_power_w=1234.5,
        temperature_c=-3.25, cpu_utilization_pct=35.0, memory_utilization_pct=40.0,
        downlink_rate_mbps=0.0, uplink_rate_mbps=0.0)
    base.update(over)
    return Telemetry(**base)


def _alarm(**over):
    base = dict(
        satellite_id="SAT-001", timestamp=T0, severity="CRITICAL", alarm_type="BATTERY_LOW",
        parameter="battery_pct", observed_value=4.5, expected_min=20.0, expected_max=100.0,
        anomaly_score=-0.31, model_version="7", message="Battery below limit — check EPS")
    base.update(over)
    return Alarm(**base)


class _TelemetryV20(BaseModel):
    """The Telemetry model as it was BEFORE orbit_type (an old consumer)."""
    satellite_id: str
    timestamp: datetime
    latitude_deg: float
    longitude_deg: float
    altitude_km: float
    in_eclipse: bool
    in_contact: bool
    battery_pct: float
    battery_voltage_v: float
    solar_panel_power_w: float
    temperature_c: float
    cpu_utilization_pct: float
    memory_utilization_pct: float
    downlink_rate_mbps: float
    uplink_rate_mbps: float
    safe_mode: bool = False
    anomaly_flag: bool = False
    fault_injected: bool = False


@pytest.fixture
def registry():
    return build_default_registry()


@pytest.fixture
def tel_serde(registry):
    return AvroSerde(registry, gen.TELEMETRY_SUBJECT, Telemetry, gen.telemetry_schema_json())


@pytest.fixture
def alarm_serde(registry):
    return AvroSerde(registry, gen.ALARM_SUBJECT, Alarm, gen.alarm_schema_json())


# ── Generated schemas stay in step with the Pydantic models ──────────────────

class TestNoDrift:
    """If one of these fails, a model changed without regenerating the .avsc files."""

    def test_committed_telemetry_schema_matches_the_model(self):
        assert load_avsc("telemetry_v2.avsc") == gen.telemetry_schema_json(), \
            "run: python -m services.schema_registry.generate"

    def test_committed_alarm_schema_matches_the_model(self):
        assert load_avsc("alarm_v1.avsc") == gen.alarm_schema_json(), \
            "run: python -m services.schema_registry.generate"

    def test_every_model_field_is_in_the_schema(self):
        for model, schema in ((Telemetry, gen.telemetry_schema_json()),
                              (Alarm, gen.alarm_schema_json())):
            assert [f["name"] for f in schema["fields"]] == list(model.model_fields)

    def test_defaults_come_from_the_model(self):
        fields = {f["name"]: f for f in gen.telemetry_schema_json()["fields"]}
        assert fields["orbit_type"]["default"] == "LEO_CIRCULAR"
        assert fields["safe_mode"]["default"] is False
        assert "default" not in fields["battery_pct"]            # required stays required

    def test_literals_become_enums(self):
        fields = {f["name"]: f for f in gen.telemetry_schema_json()["fields"]}
        assert fields["orbit_type"]["type"]["symbols"] == ["LEO_CIRCULAR", "HEO_MOLNIYA"]
        # No enum default: an unknown orbit must be rejected, not read as LEO.
        assert "default" not in fields["orbit_type"]["type"]

    def test_historic_v1_is_v2_without_orbit_type(self):
        v1, v2 = load_avsc("telemetry_v1.avsc"), load_avsc("telemetry_v2.avsc")
        assert [f for f in v2["fields"] if f["name"] != "orbit_type"] == v1["fields"]

    def test_unsupported_annotations_are_rejected(self):
        class Bad(BaseModel):
            items: list

        with pytest.raises(TypeError):
            gen.avro_schema_for_model(Bad, "Bad", "x")

    def test_optional_fields_become_nullable_unions(self):
        from typing import Optional

        class Opt(BaseModel):
            a: Optional[str] = None
            b: Optional[datetime] = None

        schema = gen.avro_schema_for_model(Opt, "Opt", "x")
        assert schema["fields"][0] == {"name": "a", "type": ["null", "string"], "default": None}
        assert schema["fields"][1]["type"][1] == {"type": "long",
                                                   "logicalType": "timestamp-micros"}


# ── The default registry (the project's real history) ────────────────────────

class TestDefaultRegistry:

    def test_history_is_registered_in_order(self, registry):
        assert registry.versions(gen.TELEMETRY_SUBJECT) == [1, 2]
        assert registry.versions(gen.ALARM_SUBJECT) == [1]

    def test_v1_to_v2_is_fully_compatible(self, registry):
        v1 = registry.get_version(gen.TELEMETRY_SUBJECT, 1).schema
        v2 = registry.get_version(gen.TELEMETRY_SUBJECT, 2).schema
        assert can_read(writer=v1, reader=v2) == []          # backward
        assert can_read(writer=v2, reader=v1) == []          # forward

    def test_removing_a_field_is_backward_ok_but_full_rejects_it(self, registry):
        bad = gen.telemetry_schema_json()
        bad["fields"] = [f for f in bad["fields"] if f["name"] != "battery_pct"]
        assert registry.check(gen.TELEMETRY_SUBJECT, bad) == []   # backward-ok to REMOVE...
        registry.set_compatibility(gen.TELEMETRY_SUBJECT, CompatibilityMode.FULL)
        assert registry.check(gen.TELEMETRY_SUBJECT, bad) != []   # ...but not under FULL

    def test_a_new_required_field_is_rejected_by_default(self, registry):
        bad = gen.telemetry_schema_json()
        bad["fields"].append({"name": "gps_fix", "type": "boolean"})
        with pytest.raises(IncompatibleSchemaError) as exc:
            registry.register(gen.TELEMETRY_SUBJECT, bad)
        assert "gps_fix" in str(exc.value)

    def test_a_new_defaulted_field_is_accepted(self, registry):
        ok = gen.telemetry_schema_json()
        ok["fields"].append({"name": "gps_fix", "type": "boolean", "default": False})
        assert registry.register(gen.TELEMETRY_SUBJECT, ok) == 4   # next id (1,2 telemetry; 3 alarm)
        assert registry.versions(gen.TELEMETRY_SUBJECT) == [1, 2, 3]

    def test_a_new_orbit_symbol_is_backward_ok_but_forward_unsafe(self, registry):
        new = gen.telemetry_schema_json()
        for f in new["fields"]:
            if f["name"] == "orbit_type":
                f["type"]["symbols"].append("MEO_CIRCULAR")
        assert registry.check(gen.TELEMETRY_SUBJECT, new) == []          # BACKWARD (default)
        registry.set_compatibility(gen.TELEMETRY_SUBJECT, CompatibilityMode.FORWARD)
        assert registry.check(gen.TELEMETRY_SUBJECT, new) != []          # old readers reject it


# ── Wire format ──────────────────────────────────────────────────────────────

class TestWireFormat:

    def test_header_layout(self):
        message = pack(0x01020304, b"\xAA\xBB")
        assert message == bytes([0, 1, 2, 3, 4, 0xAA, 0xBB])
        assert unpack(message) == (0x01020304, b"\xAA\xBB")

    def test_schema_id_is_big_endian_in_the_message(self, tel_serde):
        message = tel_serde.serialize(_telemetry())
        assert message[0] == 0
        assert struct.unpack(">I", message[1:5])[0] == tel_serde.schema_id

    def test_bad_magic_byte(self):
        with pytest.raises(WireFormatError, match="magic"):
            unpack(b"\x01\x00\x00\x00\x01payload")

    def test_message_shorter_than_the_header(self):
        for n in range(5):
            with pytest.raises(WireFormatError):
                unpack(b"\x00" * n)

    def test_schema_id_out_of_range(self):
        for bad in (-1, 2**32):
            with pytest.raises(WireFormatError):
                pack(bad, b"")

    def test_unknown_schema_id(self, tel_serde):
        with pytest.raises(SchemaNotFoundError):
            tel_serde.deserialize(pack(9999, b"whatever"))

    def test_truncated_payload(self, tel_serde):
        message = tel_serde.serialize(_telemetry())
        for cut in (5, 6, 30, len(message) - 1):
            with pytest.raises(AvroDecodeError):
                tel_serde.deserialize(message[:cut])

    def test_trailing_garbage(self, tel_serde):
        with pytest.raises(AvroDecodeError):
            tel_serde.deserialize(tel_serde.serialize(_telemetry()) + b"\x00")

    def test_payload_written_for_another_schema_is_not_silently_accepted(
            self, tel_serde, alarm_serde):
        alarm_message = alarm_serde.serialize(_alarm())
        # Re-label alarm bytes with the telemetry schema ID: must not decode cleanly.
        forged = pack(tel_serde.schema_id, alarm_message[5:])
        with pytest.raises(Exception):
            tel_serde.deserialize(forged)

    def test_message_is_compact(self, tel_serde):
        # Telemetry as JSON is several hundred bytes; Avro should be far smaller.
        message = tel_serde.serialize(_telemetry())
        assert len(message) < len(_telemetry().model_dump_json()) / 2


# ── Round trips of the real models ───────────────────────────────────────────

class TestRoundTrips:

    def test_telemetry(self, tel_serde):
        t = _telemetry()
        assert tel_serde.deserialize(tel_serde.serialize(t)) == t

    def test_telemetry_with_every_flag_and_heo(self, tel_serde):
        t = _telemetry(orbit_type="HEO_MOLNIYA", safe_mode=True, anomaly_flag=True,
                       fault_injected=True, in_contact=True)
        assert tel_serde.deserialize(tel_serde.serialize(t)) == t

    def test_microsecond_timestamp_is_exact(self, tel_serde):
        t = _telemetry(timestamp=datetime(2026, 1, 1, 0, 0, 0, 1, tzinfo=timezone.utc))
        assert tel_serde.deserialize(tel_serde.serialize(t)).timestamp == t.timestamp

    def test_non_utc_timestamp_keeps_the_instant(self, tel_serde):
        plus_two = timezone(timedelta(hours=2))
        t = _telemetry(timestamp=datetime(2026, 5, 1, 12, 0, tzinfo=plus_two))
        assert tel_serde.deserialize(tel_serde.serialize(t)).timestamp == t.timestamp

    def test_naive_timestamp_is_taken_as_utc(self, tel_serde):
        t = _telemetry(timestamp=datetime(2026, 5, 1, 12, 0))
        out = tel_serde.deserialize(tel_serde.serialize(t))
        assert out.timestamp == datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    def test_timestamps_before_the_epoch(self):
        d = datetime(1960, 6, 1, 3, 4, 5, 6, tzinfo=timezone.utc)
        assert micros_to_datetime(datetime_to_micros(d)) == d

    def test_float_values_are_exact_not_float32(self, tel_serde):
        t = _telemetry(latitude_deg=0.1 + 0.2)       # 0.30000000000000004
        assert tel_serde.deserialize(tel_serde.serialize(t)).latitude_deg == t.latitude_deg

    def test_alarm(self, alarm_serde):
        a = _alarm()
        assert alarm_serde.deserialize(alarm_serde.serialize(a)) == a

    def test_alarm_with_non_ascii_message_and_flag(self, alarm_serde):
        a = _alarm(message="Température élevée ⚠", from_fault_injection=True, severity="INFO",
                   orbit_type="HEO_MOLNIYA")
        assert alarm_serde.deserialize(alarm_serde.serialize(a)) == a

    def test_many_records_in_sequence(self, tel_serde):
        for i in range(200):
            t = _telemetry(battery_pct=i / 2, timestamp=T0 + timedelta(seconds=i))
            assert tel_serde.deserialize(tel_serde.serialize(t)) == t

    def test_model_to_record_converts_only_timestamps(self, tel_serde):
        schema = parse_schema(gen.telemetry_schema_json())
        record = model_to_record(_telemetry(), schema)
        assert isinstance(record["timestamp"], int) and record["satellite_id"] == "SAT-001"

    def test_pydantic_still_validates_after_decoding(self, tel_serde, registry):
        # A message whose enum symbol is valid Avro but whose value Pydantic
        # would reject must raise rather than produce a bad model.
        wide = gen.telemetry_schema_json()
        for f in wide["fields"]:
            if f["name"] == "orbit_type":
                f["type"]["symbols"].append("MARS_ORBIT")
        sid = registry.register(gen.TELEMETRY_SUBJECT, wide)
        schema = parse_schema(wide)
        record = model_to_record(_telemetry(), schema)
        record["orbit_type"] = "MARS_ORBIT"
        message = pack(sid, encode(schema, record))
        with pytest.raises(Exception):
            tel_serde.deserialize(message)   # our v2 reader has no enum default -> rejected


# ── Serde construction ───────────────────────────────────────────────────────

class TestSerdeConstruction:

    def test_auto_register_returns_the_existing_id(self, registry):
        before = registry.latest(gen.TELEMETRY_SUBJECT).schema_id
        serde = AvroSerde(registry, gen.TELEMETRY_SUBJECT, Telemetry, gen.telemetry_schema_json())
        assert serde.schema_id == before
        assert registry.versions(gen.TELEMETRY_SUBJECT) == [1, 2]

    def test_incompatible_schema_fails_at_construction(self, registry):
        bad = gen.telemetry_schema_json()
        bad["fields"].append({"name": "gps_fix", "type": "boolean"})
        with pytest.raises(IncompatibleSchemaError):
            AvroSerde(registry, gen.TELEMETRY_SUBJECT, Telemetry, bad)

    def test_without_auto_register_the_schema_must_exist(self, registry):
        AvroSerde(registry, gen.TELEMETRY_SUBJECT, Telemetry, gen.telemetry_schema_json(),
                  auto_register=False)                         # present: fine
        unknown = gen.telemetry_schema_json()
        unknown["fields"].append({"name": "gps_fix", "type": "boolean", "default": False})
        with pytest.raises(SchemaNotFoundError):
            AvroSerde(registry, gen.TELEMETRY_SUBJECT, Telemetry, unknown, auto_register=False)

    def test_needs_a_record_schema(self, registry):
        with pytest.raises(TypeError):
            AvroSerde(registry, "x", Telemetry, "string")


# ── The real evolution: Telemetry 2.0 <-> 2.1 ────────────────────────────────

class TestRealEvolution:

    def test_old_producer_new_consumer_gets_the_orbit_default(self, registry, tel_serde):
        """A producer still on 2.0 (no orbit_type) feeds a 2.1 consumer."""
        old_producer = AvroSerde(registry, gen.TELEMETRY_SUBJECT, _TelemetryV20,
                                 load_avsc("telemetry_v1.avsc"))
        old = _TelemetryV20(**_telemetry().model_dump(exclude={"orbit_type"}))
        out = tel_serde.deserialize(old_producer.serialize(old))
        assert out.orbit_type == "LEO_CIRCULAR"                   # ADR-016 default
        assert out == _telemetry()

    def test_new_producer_old_consumer_ignores_orbit_type(self, registry, tel_serde):
        """A 2.1 producer feeds a consumer that has not been upgraded."""
        old_consumer = AvroSerde(registry, gen.TELEMETRY_SUBJECT, _TelemetryV20,
                                 load_avsc("telemetry_v1.avsc"))
        out = old_consumer.deserialize(tel_serde.serialize(_telemetry()))
        assert out.battery_pct == 81.5 and not hasattr(out, "orbit_type")

    def test_documented_hazard_old_consumer_cannot_see_heo(self, registry, tel_serde):
        """
        KNOWN LIMIT of forward compatibility, recorded as a test on purpose.
        An un-upgraded consumer silently drops orbit_type, so it cannot tell a
        HEO record from a LEO one.  Compatibility is about whether bytes can be
        read, not about whether the reader understands what it lost.  The
        mitigation is operational: upgrade consumers BEFORE producers
        (the BACKWARD rule), never the reverse.
        """
        old_consumer = AvroSerde(registry, gen.TELEMETRY_SUBJECT, _TelemetryV20,
                                 load_avsc("telemetry_v1.avsc"))
        out = old_consumer.deserialize(tel_serde.serialize(_telemetry(orbit_type="HEO_MOLNIYA")))
        assert not hasattr(out, "orbit_type")

    def test_both_versions_coexist_on_one_stream(self, registry, tel_serde):
        old_producer = AvroSerde(registry, gen.TELEMETRY_SUBJECT, _TelemetryV20,
                                 load_avsc("telemetry_v1.avsc"))
        old = _TelemetryV20(**_telemetry().model_dump(exclude={"orbit_type"}))
        stream = [old_producer.serialize(old), tel_serde.serialize(_telemetry()),
                  old_producer.serialize(old), tel_serde.serialize(
                      _telemetry(orbit_type="HEO_MOLNIYA"))]
        orbits = [tel_serde.deserialize(m).orbit_type for m in stream]
        assert orbits == ["LEO_CIRCULAR", "LEO_CIRCULAR", "LEO_CIRCULAR", "HEO_MOLNIYA"]

    def test_the_two_versions_have_different_ids(self, registry):
        v1 = registry.get_version(gen.TELEMETRY_SUBJECT, 1).schema_id
        v2 = registry.get_version(gen.TELEMETRY_SUBJECT, 2).schema_id
        assert v1 != v2
