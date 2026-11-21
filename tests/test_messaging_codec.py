"""
tests/test_messaging_codec.py

Tests for the Kafka message codec (Sprint 9) and for the five
services that now use it.

The migration rule being tested: every CONSUMER reads both JSON and Avro;
the PRODUCER writes JSON unless KAFKA_SERIALIZATION=avro.  So the tests
check, for each format, that a message written in it is read by a reader
configured for the OTHER one.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from services.messaging import project_codecs as pc
from services.messaging.codec import (
    MessageCodec, MessageDecodeError, SerializationFormat,
)
from services.schema_registry.generate import (
    TELEMETRY_SUBJECT, telemetry_schema_json,
)
from services.schema_registry.defaults import build_default_registry
from services.schema_registry.serde import pack
from shared.config import Settings, settings
from shared.schemas.alarm_schema import Alarm
from shared.schemas.telemetry_schema import Telemetry

T0 = datetime(2026, 11, 21, 12, 0, 0, 500000, tzinfo=timezone.utc)


def _telemetry(**over):
    base = dict(
        satellite_id="SAT-001", timestamp=T0, latitude_deg=12.5, longitude_deg=-7.2,
        altitude_km=550.0, in_eclipse=False, in_contact=True, battery_pct=92.0,
        battery_voltage_v=28.3, solar_panel_power_w=1300.0, temperature_c=18.4,
        cpu_utilization_pct=35.0, memory_utilization_pct=40.0, downlink_rate_mbps=1.0,
        uplink_rate_mbps=0.0)
    base.update(over)
    return Telemetry(**base)


def _alarm(**over):
    base = dict(
        satellite_id="SAT-001", timestamp=T0, severity="WARNING", alarm_type="ANOMALY_SCORE",
        parameter="anomaly_score", observed_value=-0.08, expected_min=-0.05,
        expected_max=1.0, anomaly_score=-0.0848, model_version="7",
        message="WARNING: anomaly_score=-0.08 on SAT-001")
    base.update(over)
    return Alarm(**base)


def _msg(raw: bytes):
    """A stand-in for a confluent_kafka message."""
    m = MagicMock()
    m.value.return_value = raw
    m.offset.return_value = 0
    m.error.return_value = None
    return m


# ── The codec itself ──────────────────────────────────────────────────────────

class TestCodecFormats:

    @pytest.mark.parametrize("fmt", ["json", "avro"])
    def test_telemetry_round_trip(self, fmt):
        t = _telemetry(orbit_type="HEO_MOLNIYA", safe_mode=True)
        assert pc.decode_telemetry(pc.encode_telemetry(t, fmt)) == t

    @pytest.mark.parametrize("fmt", ["json", "avro"])
    def test_alarm_round_trip(self, fmt):
        a = _alarm(severity="CRITICAL", from_fault_injection=True)
        assert pc.decode_alarm(pc.encode_alarm(a, fmt)) == a

    def test_json_is_unchanged_from_before(self):
        # The JSON bytes must equal what the old code produced, so existing
        # consumers and tools see no difference.
        t = _telemetry()
        old = json.dumps(t.model_dump(mode="json")).encode("utf-8")
        assert pc.encode_telemetry(t, "json") == old

    def test_avro_starts_with_the_magic_byte_and_json_does_not(self):
        t = _telemetry()
        assert pc.encode_telemetry(t, "avro")[0] == 0
        assert pc.encode_telemetry(t, "json")[0] == ord("{")

    def test_avro_is_much_smaller_than_json(self):
        t = _telemetry()
        assert len(pc.encode_telemetry(t, "avro")) < len(pc.encode_telemetry(t, "json")) / 2

    def test_both_formats_decode_to_the_same_model(self):
        t = _telemetry()
        assert pc.decode_telemetry(pc.encode_telemetry(t, "json")) == \
               pc.decode_telemetry(pc.encode_telemetry(t, "avro"))

    @pytest.mark.parametrize("reader_fmt", [SerializationFormat.JSON, SerializationFormat.AVRO])
    def test_a_codec_reads_both_formats_whatever_it_writes(self, reader_fmt):
        codec = MessageCodec(Telemetry, TELEMETRY_SUBJECT, telemetry_schema_json(),
                             build_default_registry(), reader_fmt)
        t = _telemetry()
        as_json = json.dumps(t.model_dump(mode="json")).encode()
        assert codec.decode(as_json) == t
        assert codec.decode(codec.encode(t)) == t
        # and the bytes of the OTHER format:
        other = SerializationFormat.AVRO if reader_fmt == SerializationFormat.JSON \
            else SerializationFormat.JSON
        writer = MessageCodec(Telemetry, TELEMETRY_SUBJECT, telemetry_schema_json(),
                              build_default_registry(), other)
        assert codec.decode(writer.encode(t)) == t

    def test_the_configured_format_follows_the_setting(self, monkeypatch):
        t = _telemetry()
        monkeypatch.setattr(settings, "kafka_serialization", "avro")
        assert pc.encode_telemetry(t)[0] == 0
        monkeypatch.setattr(settings, "kafka_serialization", "json")
        assert pc.encode_telemetry(t)[0] == ord("{")

    def test_default_setting_is_json(self):
        assert Settings().kafka_serialization == "json"

    def test_invalid_setting_is_rejected(self):
        with pytest.raises(Exception):
            Settings(kafka_serialization="protobuf")

    def test_old_json_without_orbit_type_still_decodes(self):
        # A pre-2.1 JSON record has no orbit_type; the model default applies.
        payload = _telemetry().model_dump(mode="json")
        del payload["orbit_type"]
        assert pc.decode_telemetry(json.dumps(payload).encode()).orbit_type == "LEO_CIRCULAR"


class TestCodecErrors:
    """Every kind of bad input gives the one error type, never anything else."""

    @pytest.mark.parametrize("raw", [
        b"", None, "text", b"\xff\xfe not utf-8", b"not json", b"[1, 2, 3]", b"null",
        b'{"satellite_id": "SAT-001"}',                       # missing fields
        b'{"satellite_id": 5}',                               # wrong type
        b"\x00", b"\x00\x00\x00\x00", b"\x00\x00\x00\x00\x02xx",   # header only / garbage
    ])
    def test_bad_input_raises_message_decode_error(self, raw):
        with pytest.raises(MessageDecodeError):
            pc.decode_telemetry(raw)

    def test_truncated_avro_message(self):
        raw = pc.encode_telemetry(_telemetry(), "avro")
        for cut in (6, 20, len(raw) - 1):
            with pytest.raises(MessageDecodeError):
                pc.decode_telemetry(raw[:cut])

    def test_unknown_schema_id(self):
        with pytest.raises(MessageDecodeError, match="SchemaNotFound"):
            pc.decode_telemetry(pack(9999, b"payload"))

    def test_an_alarm_message_is_not_accepted_as_telemetry(self):
        for fmt in ("json", "avro"):
            with pytest.raises(MessageDecodeError):
                pc.decode_telemetry(pc.encode_alarm(_alarm(), fmt))

    def test_pydantic_rejection_is_reported(self):
        payload = _telemetry().model_dump(mode="json")
        payload["orbit_type"] = "MARS_ORBIT"
        with pytest.raises(MessageDecodeError):
            pc.decode_telemetry(json.dumps(payload).encode())

    def test_error_keeps_the_original_cause(self):
        with pytest.raises(MessageDecodeError) as exc:
            pc.decode_telemetry(b"not json")
        assert exc.value.__cause__ is not None

    def test_random_garbage_never_raises_anything_else(self):
        import random
        rng = random.Random(11)
        for _ in range(1000):
            junk = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 90)))
            try:
                pc.decode_telemetry(junk)
            except MessageDecodeError:
                pass


class TestSharedRegistry:

    def test_one_registry_per_process(self):
        assert pc.get_registry() is pc.get_registry()

    def test_two_independent_processes_agree_on_schema_ids(self):
        """
        Simulates producer and consumer as two separate processes: each builds
        its own registry.  A message written against one must be readable
        against the other, because the IDs come out identical.
        """
        producer_registry, consumer_registry = build_default_registry(), build_default_registry()
        w = MessageCodec(Telemetry, TELEMETRY_SUBJECT, telemetry_schema_json(),
                         producer_registry, SerializationFormat.AVRO)
        r = MessageCodec(Telemetry, TELEMETRY_SUBJECT, telemetry_schema_json(),
                         consumer_registry, SerializationFormat.JSON)
        t = _telemetry()
        assert r.decode(w.encode(t)) == t


# ── The five services ─────────────────────────────────────────────────────────

class TestServicesReadBothFormats:

    @pytest.fixture
    def telemetry_sink(self, monkeypatch):
        from services.telemetry_sink.sink import TelemetrySink
        monkeypatch.setattr("services.telemetry_sink.sink.psycopg2.connect", MagicMock())
        monkeypatch.setattr("services.telemetry_sink.sink.Consumer", MagicMock())
        return TelemetrySink()

    @pytest.fixture
    def alarm_sink(self, monkeypatch):
        from services.alarm_sink.sink import AlarmSink
        monkeypatch.setattr("services.alarm_sink.sink.psycopg2.connect", MagicMock())
        monkeypatch.setattr("services.alarm_sink.sink.Consumer", MagicMock())
        return AlarmSink()

    @pytest.mark.parametrize("fmt", ["json", "avro"])
    def test_telemetry_sink(self, telemetry_sink, fmt):
        record = telemetry_sink._parse(_msg(pc.encode_telemetry(_telemetry(), fmt)))
        assert record["satellite_id"] == "SAT-001" and record["battery_pct"] == 92.0
        assert record["orbit_type"] == "LEO_CIRCULAR"

    def test_telemetry_sink_survives_a_bad_message(self, telemetry_sink):
        assert telemetry_sink._parse(_msg(b"\x00\x00\x00\x00\x07junk")) is None

    @pytest.mark.parametrize("fmt", ["json", "avro"])
    def test_alarm_sink(self, alarm_sink, fmt):
        record = alarm_sink._parse(_msg(pc.encode_alarm(_alarm(severity="CRITICAL"), fmt)))
        assert record["severity"] == "CRITICAL" and record["satellite_id"] == "SAT-001"

    def test_alarm_sink_survives_a_bad_message(self, alarm_sink):
        assert alarm_sink._parse(_msg(b"\x00\x00\x00\x00\x07junk")) is None

    @pytest.mark.parametrize("fmt", ["json", "avro"])
    def test_debug_consumer(self, monkeypatch, caplog, fmt):
        from services.kafka_consumer.consumer import TelemetryConsumer
        monkeypatch.setattr("services.kafka_consumer.consumer.Consumer", MagicMock())
        consumer = TelemetryConsumer("SAT-001")
        with caplog.at_level("INFO"):
            consumer._handle(_msg(pc.encode_telemetry(_telemetry(), fmt)))
        assert "Received" in caplog.text

    def test_debug_consumer_logs_instead_of_crashing(self, monkeypatch, caplog):
        from services.kafka_consumer.consumer import TelemetryConsumer
        monkeypatch.setattr("services.kafka_consumer.consumer.Consumer", MagicMock())
        consumer = TelemetryConsumer("SAT-001")
        with caplog.at_level("ERROR"):
            consumer._handle(_msg(b"garbage"))
        assert "Failed to decode" in caplog.text

    @pytest.mark.parametrize("fmt", ["json", "avro"])
    def test_detector_parse(self, fmt):
        from services.anomaly_detection.detector import AnomalyDetectionService
        service = AnomalyDetectionService.__new__(AnomalyDetectionService)   # skip heavy init
        assert service._parse(pc.encode_telemetry(_telemetry(), fmt)) == _telemetry()

    def test_detector_parse_returns_none_on_bad_input(self):
        from services.anomaly_detection.detector import AnomalyDetectionService
        service = AnomalyDetectionService.__new__(AnomalyDetectionService)
        assert service._parse(b"garbage") is None


class TestServicesWriteTheConfiguredFormat:

    @pytest.mark.parametrize("fmt, first_byte", [("json", ord("{")), ("avro", 0)])
    def test_detector_publishes_the_configured_format(self, monkeypatch, fmt, first_byte):
        from services.anomaly_detection.detector import AnomalyDetectionService
        monkeypatch.setattr(settings, "kafka_serialization", fmt)
        service = AnomalyDetectionService.__new__(AnomalyDetectionService)
        service._producer = MagicMock()
        service._publish_alarm(_alarm())
        kwargs = service._producer.produce.call_args.kwargs
        assert kwargs["topic"] == "alarms.SAT-001" and kwargs["key"] == b"SAT-001"
        assert kwargs["value"][0] == first_byte
        assert pc.decode_alarm(kwargs["value"]) == _alarm()

    @pytest.mark.parametrize("fmt, first_byte", [("json", ord("{")), ("avro", 0)])
    def test_producer_publishes_the_configured_format(self, monkeypatch, fmt, first_byte):
        from services.kafka_producer import producer as prod
        monkeypatch.setattr(settings, "kafka_serialization", fmt)
        fake_kafka = MagicMock()
        monkeypatch.setattr(prod, "Producer", lambda conf: fake_kafka)
        p = prod.TelemetryProducer("SAT-001")
        # Stop the endless loop after one record.
        monkeypatch.setattr(prod.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))
        p.run()
        value = fake_kafka.produce.call_args.kwargs["value"]
        assert value[0] == first_byte
        assert pc.decode_telemetry(value).satellite_id == "SAT-001"

    def test_end_to_end_avro_producer_to_sink(self, monkeypatch):
        """Producer writes Avro; the unchanged sink reads it."""
        from services.kafka_producer import producer as prod
        from services.telemetry_sink.sink import TelemetrySink
        monkeypatch.setattr(settings, "kafka_serialization", "avro")
        fake_kafka = MagicMock()
        monkeypatch.setattr(prod, "Producer", lambda conf: fake_kafka)
        monkeypatch.setattr(prod.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))
        prod.TelemetryProducer("SAT-001").run()
        value = fake_kafka.produce.call_args.kwargs["value"]

        monkeypatch.setattr("services.telemetry_sink.sink.psycopg2.connect", MagicMock())
        monkeypatch.setattr("services.telemetry_sink.sink.Consumer", MagicMock())
        record = TelemetrySink()._parse(_msg(value))
        assert record["satellite_id"] == "SAT-001"
