"""
tests/test_frame_ingest.py

Tests for the frame ingest service (Sprint 9, patch D).

Everything runs on the in-memory bus, so no broker is needed.  Frames come
from the real FrameBuilder, or from SimulatedDownlink (which uses the real
telemetry simulator), and go through the real FrameProcessor.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from services.frame_processor.framer import FrameBuilder
from services.frame_processor.processor import FrameProcessor
from services.frame_processor.telemetry_codec import ScidRegistry
from services.frame_processor.tm_frame import FrameConfig
from services.ingest.__main__ import main
from services.ingest.frame_ingest import (
    FrameIngestService, SimulatedDownlink, build_service, frames_from_bytes,
    parse_scids,
)
from services.messaging.bus import InMemoryBus
from services.messaging.project_codecs import decode_telemetry
from services.satellite_simulator.telemetry import TelemetryGenerator
from shared.config import settings
from shared.schemas.telemetry_schema import Telemetry

CONFIG = FrameConfig()                                   # 1115-byte frames
REGISTRY = ScidRegistry({"SAT-001": 1, "SAT-002": 2})
T0 = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)


def _record(i=0, sat="SAT-001"):
    """A record whose battery_pct equals i, so records can be told apart."""
    return Telemetry(
        satellite_id=sat, timestamp=T0 + timedelta(seconds=i),
        latitude_deg=10.0, longitude_deg=20.0, altitude_km=550.0,
        in_eclipse=False, in_contact=False, orbit_type="LEO_CIRCULAR",
        battery_pct=float(i), battery_voltage_v=28.0, solar_panel_power_w=1300.0,
        temperature_c=20.0, cpu_utilization_pct=35.0, memory_utilization_pct=40.0,
        downlink_rate_mbps=0.0, uplink_rate_mbps=0.0,
    )


@pytest.fixture
def bus():
    return InMemoryBus()


@pytest.fixture
def service(bus):
    return FrameIngestService(FrameProcessor(CONFIG, REGISTRY), bus)


def _builder(sat="SAT-001"):
    return FrameBuilder(CONFIG, REGISTRY.scid_for(sat))


def _published(bus, sat="SAT-001"):
    """Decode every message on a satellite's telemetry topic."""
    return [decode_telemetry(m.value) for m in bus.messages(settings.telemetry_topic(sat))]


# ── Settings parsing ─────────────────────────────────────────────────────────

class TestParseScids:

    def test_a_list_is_parsed(self):
        assert parse_scids("SAT-001=1, SAT-002=2") == {"SAT-001": 1, "SAT-002": 2}

    @pytest.mark.parametrize("bad", ["", " , ", "SAT-001", "=1", "SAT-001=x"])
    def test_malformed_input_is_refused(self, bad):
        with pytest.raises(ValueError):
            parse_scids(bad)

    def test_the_default_setting_is_valid(self):
        assert parse_scids(settings.ingest_satellite_scids)


# ── Core: frames in, records out ─────────────────────────────────────────────

class TestIngest:

    def test_a_frame_becomes_a_record_on_the_right_topic(self, service, bus):
        n = service.ingest(_builder().frames_for_telemetry([_record(7)]))
        assert n == 1
        msgs = bus.messages(settings.telemetry_topic("SAT-001"))
        assert len(msgs) == 1 and msgs[0].key == "SAT-001"
        assert _published(bus)[0].battery_pct == 7.0

    def test_records_keep_their_order(self, service, bus):
        b = _builder()
        for i in range(5):
            service.ingest(b.frames_for_telemetry([_record(i)]))
        assert [t.battery_pct for t in _published(bus)] == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_two_satellites_on_one_stream_stay_separate(self, service, bus):
        a, b = _builder("SAT-001"), _builder("SAT-002")
        mixed = (a.frames_for_telemetry([_record(1, "SAT-001")])
                 + b.frames_for_telemetry([_record(2, "SAT-002")])
                 + a.frames_for_telemetry([_record(3, "SAT-001")]))
        service.ingest(mixed)
        assert [t.battery_pct for t in _published(bus, "SAT-001")] == [1.0, 3.0]
        assert [t.battery_pct for t in _published(bus, "SAT-002")] == [2.0]

    def test_idle_frames_publish_nothing(self, service, bus):
        assert service.ingest([_builder().idle_frame()]) == 0
        assert bus.messages() == []

    def test_a_record_spanning_several_frames_is_reassembled(self, bus):
        small = FrameConfig(frame_length=30)             # 22 data bytes: record spans 3 frames
        svc = FrameIngestService(FrameProcessor(small, REGISTRY), bus)
        frames = FrameBuilder(small, 1).frames_for_telemetry([_record(9)])
        assert len(frames) > 1
        assert svc.ingest(frames) == 1
        assert _published(bus)[0].battery_pct == 9.0

    def test_counters(self, service, bus):
        service.ingest(_builder().frames_for_telemetry([_record(0), _record(1)]))
        assert service.stats.frames_in >= 1
        assert service.stats.telemetry_published == 2
        assert service.processor_stats.telemetry_out == 2


# ── Serialisation follows KAFKA_SERIALIZATION ────────────────────────────────

class TestSerialisation:

    def test_json_by_default(self, service, bus):
        service.ingest(_builder().frames_for_telemetry([_record(1)]))
        raw = bus.messages()[0].value
        assert json.loads(raw)["satellite_id"] == "SAT-001"

    def test_avro_when_configured(self, service, bus, monkeypatch):
        monkeypatch.setattr(settings, "kafka_serialization", "avro")
        service.ingest(_builder().frames_for_telemetry([_record(1)]))
        raw = bus.messages()[0].value
        assert raw[0] == 0                                # Confluent magic byte
        assert decode_telemetry(raw).battery_pct == 1.0   # and it decodes


# ── Damaged links ────────────────────────────────────────────────────────────

class TestNoisyLink:

    def test_a_corrupted_frame_is_counted_and_publishes_nothing(self, service, bus):
        frame = bytearray(_builder().frames_for_telemetry([_record(1)])[0])
        frame[20] ^= 0xFF                                 # flip bits in the payload
        assert service.ingest([bytes(frame)]) == 0
        assert service.processor_stats.crc_errors == 1
        assert bus.messages() == []

    def test_garbage_and_short_input_never_raise(self, service):
        service.ingest([b"", b"\x00" * 5, b"\xff" * 1115])
        assert service.stats.telemetry_published == 0

    def test_a_lost_frame_costs_one_record_and_the_rest_arrive(self, service, bus):
        b = _builder()
        frames = [b.frames_for_telemetry([_record(i)])[0] for i in range(5)]
        del frames[2]                                     # record 2 lost on the link
        service.ingest(frames)
        assert [t.battery_pct for t in _published(bus)] == [0.0, 1.0, 3.0, 4.0]
        assert service.processor_stats.frames_lost == 1

    def test_a_frame_from_an_unknown_spacecraft_is_dropped(self, service, bus):
        stranger = FrameBuilder(CONFIG, 99).frames_for_telemetry([_record(1)])
        service.ingest(stranger)
        assert service.processor_stats.unknown_spacecraft >= 1
        assert bus.messages() == []


# ── Broker trouble ───────────────────────────────────────────────────────────

class TestBrokerOutage:

    def test_an_outage_is_counted_and_ingest_carries_on(self, service, bus):
        b = _builder()
        bus.fail_publishes = True
        assert service.ingest(b.frames_for_telemetry([_record(1)])) == 0
        assert service.stats.publish_errors == 1
        bus.fail_publishes = False                        # broker is back
        assert service.ingest(b.frames_for_telemetry([_record(2)])) == 1
        assert [t.battery_pct for t in _published(bus)] == [2.0]   # record 1 is gone for good
        assert service.stats.telemetry_published == 1


# ── Simulated downlink ───────────────────────────────────────────────────────

class TestSimulatedDownlink:

    @pytest.fixture(autouse=True)
    def _fresh_generators(self):
        # The generators share class-level state; start and finish clean.
        for sat in ("SAT-001", "SAT-002"):
            TelemetryGenerator(satellite_id=sat).clear_state()
        yield
        for sat in ("SAT-001", "SAT-002"):
            TelemetryGenerator(satellite_id=sat).clear_state()

    def test_end_to_end_each_tick_gives_one_record_per_satellite(self, service, bus):
        source = SimulatedDownlink(CONFIG, REGISTRY, ["SAT-001", "SAT-002"])
        service.ingest(source.stream(ticks=3))
        assert len(_published(bus, "SAT-001")) == 3
        assert len(_published(bus, "SAT-002")) == 3
        assert service.processor_stats.frames_lost == 0

    def test_records_look_like_the_simulator_produced_them(self, service, bus):
        service.ingest(SimulatedDownlink(CONFIG, REGISTRY, ["SAT-001"]).stream(1))
        t = _published(bus)[0]
        assert t.satellite_id == "SAT-001" and t.orbit_type == "LEO_CIRCULAR"
        assert 0.0 <= t.battery_pct <= 100.0

    def test_drop_every_n_loses_frames_and_the_receiver_notices(self, service, bus):
        source = SimulatedDownlink(CONFIG, REGISTRY, ["SAT-001"], drop_every_n=3)
        service.ingest(source.stream(ticks=9))            # frames 3, 6, 9 are dropped
        assert len(_published(bus)) == 6
        # The final dropped frame leaves no later frame to reveal the gap.
        assert service.processor_stats.frames_lost == 2


# ── Replay from bytes ────────────────────────────────────────────────────────

class TestFramesFromBytes:

    def test_a_recording_is_cut_into_frames_and_replays(self, service, bus):
        b = _builder()
        recording = b"".join(b.frames_for_telemetry([_record(i)])[0] for i in range(3))
        frames = frames_from_bytes(recording, CONFIG.frame_length)
        assert len(frames) == 3
        service.ingest(frames)
        assert [t.battery_pct for t in _published(bus)] == [0.0, 1.0, 2.0]

    def test_a_ragged_recording_is_refused(self):
        with pytest.raises(ValueError, match="whole number"):
            frames_from_bytes(b"\x00" * 1116, 1115)

    def test_a_bad_frame_length_is_refused(self):
        with pytest.raises(ValueError):
            frames_from_bytes(b"", 0)


# ── Command line ─────────────────────────────────────────────────────────────

class TestEntryPoint:

    def test_once_publishes_one_tick_and_exits(self, bus):
        TelemetryGenerator(satellite_id="SAT-001").clear_state()
        assert main(["--once"], publisher=bus) == 0
        assert len(bus.messages(settings.telemetry_topic("SAT-001"))) == 1

    def test_the_loop_runs_until_interrupted(self, bus):
        calls = []

        def sleep(_):
            calls.append(1)
            if len(calls) == 2:
                raise KeyboardInterrupt

        TelemetryGenerator(satellite_id="SAT-001").clear_state()
        assert main(["--interval", "0"], publisher=bus, sleep=sleep) == 0
        assert len(bus.messages(settings.telemetry_topic("SAT-001"))) == 2

    def test_build_service_uses_the_settings(self, bus):
        svc, cfg, reg = build_service(bus)
        assert cfg.frame_length == settings.ingest_frame_length
        assert reg.scid_for("SAT-001") == 1
