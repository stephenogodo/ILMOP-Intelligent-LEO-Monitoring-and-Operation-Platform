"""
tests/test_frame_codec_framer.py

Tests for the telemetry binary layout and the spacecraft-side framer (S8-07).
"""

from datetime import datetime, timezone

import pytest

from shared.schemas.telemetry_schema import Telemetry
from services.frame_processor.space_packet import decode_packet, IDLE_APID
from services.frame_processor.telemetry_codec import (
    TELEMETRY_APID,
    TELEMETRY_DATA_LEN,
    CodecError,
    ScidRegistry,
    UnknownSpacecraftError,
    decode_telemetry,
    encode_telemetry,
)
from services.frame_processor.tm_frame import (
    FHP_NO_PACKET_START,
    FrameConfig,
    decode_frame,
)
from services.frame_processor.framer import FrameBuilder

CONFIG = FrameConfig(frame_length=60)        # data field = 52 bytes
T0 = datetime(2026, 11, 21, 12, 0, 0, 250000, tzinfo=timezone.utc)


def _telemetry(**over):
    base = dict(
        satellite_id="SAT-001", timestamp=T0,
        latitude_deg=12.345678, longitude_deg=-7.654321, altitude_km=550.25,
        in_eclipse=True, in_contact=False, orbit_type="LEO_CIRCULAR",
        battery_pct=92.5, battery_voltage_v=28.3, solar_panel_power_w=1300.0,
        temperature_c=18.4, cpu_utilization_pct=35.0, memory_utilization_pct=40.0,
        downlink_rate_mbps=0.0, uplink_rate_mbps=0.0,
        safe_mode=False, anomaly_flag=False, fault_injected=False,
    )
    base.update(over)
    return Telemetry(**base)


class TestCodec:

    def test_data_field_is_the_documented_length(self):
        assert len(encode_telemetry(_telemetry())) == TELEMETRY_DATA_LEN == 51

    def test_round_trip_within_float32_precision(self):
        original = _telemetry()
        back = decode_telemetry(encode_telemetry(original), "SAT-001")
        for name in ("latitude_deg", "longitude_deg", "altitude_km", "battery_pct",
                     "battery_voltage_v", "solar_panel_power_w", "temperature_c",
                     "cpu_utilization_pct", "memory_utilization_pct"):
            assert getattr(back, name) == pytest.approx(getattr(original, name), rel=1e-6)

    def test_timestamp_round_trips_to_microsecond_scale(self):
        back = decode_telemetry(encode_telemetry(_telemetry()), "SAT-001")
        assert abs((back.timestamp - T0).total_seconds()) < 1e-4

    def test_timestamp_rounding_into_the_next_second_is_handled(self):
        ts = datetime(2026, 11, 21, 12, 0, 0, 999999, tzinfo=timezone.utc)
        back = decode_telemetry(encode_telemetry(_telemetry(timestamp=ts)), "SAT-001")
        assert abs((back.timestamp - ts).total_seconds()) < 1e-4

    def test_each_flag_round_trips(self):
        for name in ("in_eclipse", "in_contact", "safe_mode", "anomaly_flag", "fault_injected"):
            for value in (True, False):
                back = decode_telemetry(encode_telemetry(_telemetry(**{name: value})), "SAT-001")
                assert getattr(back, name) is value

    def test_flags_do_not_disturb_each_other(self):
        t = _telemetry(in_eclipse=True, in_contact=False, safe_mode=True,
                       anomaly_flag=False, fault_injected=True)
        back = decode_telemetry(encode_telemetry(t), "SAT-001")
        assert (back.in_eclipse, back.in_contact, back.safe_mode,
                back.anomaly_flag, back.fault_injected) == (True, False, True, False, True)

    def test_orbit_type_round_trips(self):
        for orbit in ("LEO_CIRCULAR", "HEO_MOLNIYA"):
            back = decode_telemetry(encode_telemetry(_telemetry(orbit_type=orbit)), "SAT-001")
            assert back.orbit_type == orbit

    def test_satellite_id_comes_from_the_caller(self):
        assert decode_telemetry(encode_telemetry(_telemetry()), "SAT-MA1").satellite_id == "SAT-MA1"

    def test_wrong_length_is_refused(self):
        with pytest.raises(CodecError):
            decode_telemetry(b"\x00" * 50, "SAT-001")

    def test_negative_values_survive(self):
        back = decode_telemetry(encode_telemetry(_telemetry(temperature_c=-42.5)), "SAT-001")
        assert back.temperature_c == pytest.approx(-42.5)


class TestRegistry:

    def test_lookup_both_ways(self):
        reg = ScidRegistry({"SAT-001": 1, "SAT-002": 2})
        assert reg.scid_for("SAT-002") == 2
        assert reg.satellite_for(1) == "SAT-001"

    def test_unknown_name_and_id(self):
        reg = ScidRegistry({"SAT-001": 1})
        with pytest.raises(UnknownSpacecraftError):
            reg.scid_for("SAT-999")
        with pytest.raises(UnknownSpacecraftError):
            reg.satellite_for(99)

    def test_duplicate_scid_is_refused(self):
        with pytest.raises(ValueError):
            ScidRegistry({"SAT-001": 1, "SAT-002": 1})

    def test_scid_out_of_range_is_refused(self):
        with pytest.raises(ValueError):
            ScidRegistry({"SAT-001": 1024})


def _stream(frames, config=CONFIG):
    """Join the data fields of decoded frames back into one byte stream."""
    return b"".join(decode_frame(f, config).data for f in frames)


class TestFramer:

    def test_one_packet_fits_in_one_frame_with_idle_fill(self):
        # Packet is 57 bytes, the data field is 52: this one needs two frames.
        # Use a larger frame so a single packet fits in one.
        config = FrameConfig(frame_length=80)             # data field 72
        builder = FrameBuilder(config, scid=1)
        frames = builder.frames_for_telemetry([_telemetry()])
        assert len(frames) == 1
        decoded = decode_frame(frames[0], config)
        assert decoded.first_header_pointer == 0
        # After the 57-byte packet comes an idle packet filling the rest.
        idle = decode_packet(decoded.data[57:])
        assert idle.apid == IDLE_APID

    def test_every_frame_has_the_configured_length(self):
        builder = FrameBuilder(CONFIG, scid=1)
        frames = builder.frames_for_telemetry([_telemetry()] * 5)
        assert all(len(f) == 60 for f in frames)

    def test_a_packet_longer_than_a_data_field_spans_frames(self):
        builder = FrameBuilder(CONFIG, scid=1)             # data field 52 < packet 57
        frames = builder.frames_for_telemetry([_telemetry()])
        assert len(frames) == 2

    def test_pointer_marks_where_the_next_packet_starts(self):
        builder = FrameBuilder(CONFIG, scid=1)
        frames = builder.frames_for_telemetry([_telemetry(), _telemetry()])
        first = decode_frame(frames[0], CONFIG)
        second = decode_frame(frames[1], CONFIG)
        assert first.first_header_pointer == 0
        # Packet 1 is 57 bytes; this frame holds 52 of them, so packet 1's
        # last 5 bytes lead frame 2 and packet 2 starts at offset 5.
        assert second.first_header_pointer == 5

    def test_pointer_is_no_packet_start_when_a_packet_spans_the_whole_frame(self):
        config = FrameConfig(frame_length=30)              # data field 22 bytes
        builder = FrameBuilder(config, scid=1)
        frames = builder.frames_for_telemetry([_telemetry()])   # 57 bytes: 22+22+13
        pointers = [decode_frame(f, config).first_header_pointer for f in frames]
        assert pointers[1] == FHP_NO_PACKET_START

    def test_frame_counters_increase_by_one_and_wrap(self):
        builder = FrameBuilder(CONFIG, scid=1)
        frames = builder.frames_for_telemetry([_telemetry()] * 300)
        counts = [decode_frame(f, CONFIG).virtual_channel_count for f in frames]
        assert counts[:3] == [0, 1, 2]
        assert counts[256] == 0                            # wrapped
        assert all((b - a) % 256 == 1 for a, b in zip(counts, counts[1:]))

    def test_counters_continue_between_calls(self):
        builder = FrameBuilder(CONFIG, scid=1)
        first = builder.frames_for_telemetry([_telemetry()])
        second = builder.frames_for_telemetry([_telemetry()])
        last = decode_frame(first[-1], CONFIG).virtual_channel_count
        nxt = decode_frame(second[0], CONFIG).virtual_channel_count
        assert nxt == last + 1

    def test_packet_sequence_counts_increase(self):
        builder = FrameBuilder(CONFIG, scid=1)
        a = builder.telemetry_packet(_telemetry())
        b = builder.telemetry_packet(_telemetry())
        assert (a.sequence_count, b.sequence_count) == (0, 1)
        assert a.apid == b.apid == TELEMETRY_APID

    def test_no_packets_gives_no_frames(self):
        assert FrameBuilder(CONFIG, scid=1).frames_for_packets([]) == []

    def test_idle_frame_uses_the_idle_pointer(self):
        frame = decode_frame(FrameBuilder(CONFIG, scid=1).idle_frame(), CONFIG)
        assert frame.first_header_pointer == 0x7FE

    def test_a_remainder_under_seven_bytes_still_makes_valid_frames(self):
        # data field 54 -> a 57-byte packet leaves 51; choose a size that
        # leaves 1..6 bytes spare so the idle packet must run into an extra frame.
        config = FrameConfig(frame_length=8 + 58)          # data field 58: leaves 1 byte
        builder = FrameBuilder(config, scid=1)
        frames = builder.frames_for_telemetry([_telemetry()])
        assert len(frames) == 2                            # packet frame + extra idle frame
        assert all(len(f) == 66 for f in frames)
