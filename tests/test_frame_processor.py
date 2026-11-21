"""
tests/test_frame_processor.py

Tests for the ground-side frame processor (S8-07).

Frames are produced by the spacecraft-side FrameBuilder, then fed to the
FrameProcessor.  Each test damages the stream in one specific way and
checks the processor's response, using its statistics.

Most tests use a SMALL frame (30 bytes, so 22 data bytes) so that a
57-byte telemetry packet spans three frames and every reassembly path
is exercised.
"""

from datetime import datetime, timedelta, timezone

import pytest

from shared.schemas.telemetry_schema import Telemetry
from services.frame_processor.crc import crc16_ccitt
from services.frame_processor.framer import FrameBuilder
from services.frame_processor.processor import FrameProcessor
from services.frame_processor.telemetry_codec import ScidRegistry
from services.frame_processor.tm_frame import FrameConfig

SMALL = FrameConfig(frame_length=30)             # data field 22 bytes
REGISTRY = ScidRegistry({"SAT-001": 1, "SAT-002": 2})
T0 = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)


def _telemetry(i=0, satellite_id="SAT-001", **over):
    """A distinguishable record: battery_pct encodes the index i."""
    base = dict(
        satellite_id=satellite_id, timestamp=T0 + timedelta(seconds=i),
        latitude_deg=10.0, longitude_deg=20.0, altitude_km=550.0,
        in_eclipse=False, in_contact=False, orbit_type="LEO_CIRCULAR",
        battery_pct=float(i % 100), battery_voltage_v=28.0, solar_panel_power_w=1300.0,
        temperature_c=20.0, cpu_utilization_pct=35.0, memory_utilization_pct=40.0,
        downlink_rate_mbps=0.0, uplink_rate_mbps=0.0,
    )
    base.update(over)
    return Telemetry(**base)


def _setup(config=SMALL):
    return FrameBuilder(config, scid=1), FrameProcessor(config, REGISTRY)


def _corrupt(frame: bytes, index: int = 12) -> bytes:
    damaged = bytearray(frame)
    damaged[index] ^= 0x01
    return bytes(damaged)


def _with_valid_crc(raw: bytearray) -> bytes:
    """Recompute the CRC after editing a frame, so only the edit is wrong."""
    raw[-2:] = crc16_ccitt(bytes(raw[:-2])).to_bytes(2, "big")
    return bytes(raw)


# ── The happy path ────────────────────────────────────────────────────────────

class TestRoundTrip:

    def test_one_record_through_frames_and_back(self):
        builder, proc = _setup()
        out = proc.feed_many(builder.frames_for_telemetry([_telemetry(7)]))
        assert len(out) == 1
        assert out[0].satellite_id == "SAT-001"
        assert out[0].battery_pct == pytest.approx(7.0)
        assert out[0].timestamp == T0 + timedelta(seconds=7)

    def test_many_records_come_back_in_order(self):
        builder, proc = _setup()
        records = [_telemetry(i) for i in range(40)]
        out = proc.feed_many(builder.frames_for_telemetry(records))
        assert [t.battery_pct for t in out] == pytest.approx([float(i) for i in range(40)])

    def test_a_packet_spanning_three_frames_is_reassembled(self):
        builder, proc = _setup()                        # 57 bytes over 22-byte fields
        frames = builder.frames_for_telemetry([_telemetry(3)])
        assert len(frames) == 3
        assert len(proc.feed_many(frames)) == 1

    def test_default_large_frame_size_also_works(self):
        config = FrameConfig()                          # 1115 bytes
        builder, proc = _setup(config)
        out = proc.feed_many(builder.frames_for_telemetry([_telemetry(i) for i in range(50)]))
        assert len(out) == 50

    def test_records_split_over_several_calls(self):
        builder, proc = _setup()
        out = []
        for batch in range(5):
            out += proc.feed_many(builder.frames_for_telemetry(
                [_telemetry(batch * 3 + k) for k in range(3)]))
        assert len(out) == 15

    def test_counters_wrapping_past_256_frames(self):
        builder, proc = _setup()
        out = proc.feed_many(builder.frames_for_telemetry([_telemetry(i) for i in range(200)]))
        assert len(out) == 200                          # ~600 frames, wrapped twice
        assert proc.stats.frames_lost == 0

    def test_statistics_after_a_clean_run(self):
        builder, proc = _setup()
        frames = builder.frames_for_telemetry([_telemetry(i) for i in range(10)])
        proc.feed_many(frames)
        s = proc.stats
        assert s.frames_received == s.frames_ok == len(frames)
        assert s.telemetry_out == 10
        assert (s.crc_errors, s.frames_lost, s.resyncs) == (0, 0, 0)
        assert s.idle_packets >= 1                      # the padding in the last frame


# ── Damaged frames ────────────────────────────────────────────────────────────

class TestBadFrames:

    def test_corrupt_frame_is_dropped_and_counted(self):
        builder, proc = _setup()
        frames = builder.frames_for_telemetry([_telemetry(1)])
        out = proc.feed_many([_corrupt(frames[0])] + frames[1:])
        assert proc.stats.crc_errors == 1
        assert out == []                                # packet incomplete, nothing returned

    def test_a_packet_hit_by_corruption_is_lost_but_later_ones_survive(self):
        builder, proc = _setup()
        frames = builder.frames_for_telemetry([_telemetry(i) for i in range(10)])
        frames[4] = _corrupt(frames[4])
        out = proc.feed_many(frames)
        assert proc.stats.crc_errors == 1
        assert 0 < len(out) < 10                        # damaged packet gone, others kept

    def test_wrong_length_block_is_counted(self):
        _, proc = _setup()
        assert proc.feed(b"\x00" * 29) == []
        assert proc.stats.length_errors == 1

    def test_empty_input_is_counted_not_raised(self):
        _, proc = _setup()
        assert proc.feed(b"") == []
        assert proc.stats.length_errors == 1

    def test_bad_version_is_counted(self):
        builder, proc = _setup()
        frame = bytearray(builder.frames_for_telemetry([_telemetry()])[0])
        frame[0] |= 0x40
        assert proc.feed(_with_valid_crc(frame)) == []
        assert proc.stats.format_errors == 1

    def test_unknown_spacecraft_is_counted(self):
        stranger = FrameBuilder(SMALL, scid=99)
        proc = FrameProcessor(SMALL, REGISTRY)
        assert proc.feed_many(stranger.frames_for_telemetry([_telemetry()])) == []
        assert proc.stats.unknown_spacecraft == 3


# ── Lost and repeated frames ──────────────────────────────────────────────────

class TestLossAndDuplicates:

    def test_a_missing_frame_is_counted(self):
        builder, proc = _setup()
        frames = builder.frames_for_telemetry([_telemetry(i) for i in range(10)])
        del frames[5]
        proc.feed_many(frames)
        assert proc.stats.frames_lost == 1

    def test_several_missing_frames_are_counted(self):
        builder, proc = _setup()
        frames = builder.frames_for_telemetry([_telemetry(i) for i in range(20)])
        del frames[8:12]
        proc.feed_many(frames)
        assert proc.stats.frames_lost == 4

    def test_a_loss_never_produces_a_false_record(self):
        # Every record that comes out must be one that was really sent
        # with exactly the values it had: no glued-together packets.
        builder, proc = _setup()
        sent = {float(i): _telemetry(i) for i in range(30)}
        frames = builder.frames_for_telemetry(list(sent.values()))
        del frames[7]
        out = proc.feed_many(frames)
        assert out, "later packets should be recovered after the loss"
        for t in out:
            assert t.battery_pct in sent
            assert t.timestamp == sent[t.battery_pct].timestamp

    def test_decoding_resumes_after_a_loss(self):
        builder, proc = _setup()
        frames = builder.frames_for_telemetry([_telemetry(i) for i in range(30)])
        del frames[5]
        out = proc.feed_many(frames)
        assert out[-1].battery_pct == pytest.approx(29.0)   # the last record arrived

    def test_losing_a_frame_in_the_middle_of_a_packet_counts_a_resync(self):
        builder, proc = _setup()
        frames = builder.frames_for_telemetry([_telemetry(i) for i in range(5)])
        del frames[1]                                   # the 2nd of packet 1's 3 frames
        proc.feed_many(frames)
        assert proc.stats.resyncs >= 1

    def test_a_repeated_frame_is_ignored(self):
        builder, proc = _setup()
        frames = builder.frames_for_telemetry([_telemetry(i) for i in range(5)])
        out = proc.feed_many([frames[0], frames[0]] + frames[1:])
        assert proc.stats.duplicate_frames == 1
        assert len(out) == 5                            # no packet was doubled

    def test_starting_mid_stream_waits_for_a_packet_boundary(self):
        builder, proc = _setup()
        frames = builder.frames_for_telemetry([_telemetry(i) for i in range(10)])
        out = proc.feed_many(frames[2:])                # joined late, mid-packet
        assert len(out) >= 7                            # the early packet is lost, the rest kept
        assert out[-1].battery_pct == pytest.approx(9.0)


# ── Idle data ─────────────────────────────────────────────────────────────────

class TestIdle:

    def test_idle_frames_are_counted_and_produce_nothing(self):
        builder, proc = _setup()
        assert proc.feed(builder.idle_frame()) == []
        assert proc.stats.idle_frames == 1

    def test_idle_frames_between_records_do_not_disturb_decoding(self):
        builder, proc = _setup()
        out = []
        out += proc.feed_many(builder.frames_for_telemetry([_telemetry(1)]))
        out.append(proc.feed(builder.idle_frame()))
        out += proc.feed_many(builder.frames_for_telemetry([_telemetry(2)]))
        telemetry = [t for t in out if isinstance(t, Telemetry)]
        assert [round(t.battery_pct) for t in telemetry] == [1, 2]

    def test_an_idle_frame_discards_an_unfinished_packet(self):
        builder, proc = _setup()
        frames = builder.frames_for_telemetry([_telemetry(1)])
        proc.feed(frames[0])                            # start of a packet only
        # Build the idle frame with the next counter value so it is not seen as a loss.
        proc.feed(builder.idle_frame())
        assert proc.stats.resyncs >= 1


# ── Several satellites on one stream ──────────────────────────────────────────

class TestMultipleSpacecraft:

    def test_interleaved_satellites_are_kept_apart(self):
        b1 = FrameBuilder(SMALL, scid=1)
        b2 = FrameBuilder(SMALL, scid=2)
        f1 = b1.frames_for_telemetry([_telemetry(i, "SAT-001") for i in range(6)])
        f2 = b2.frames_for_telemetry([_telemetry(i + 50, "SAT-002") for i in range(6)])

        mixed = []
        for a, b in zip(f1, f2):                        # alternate frames from each
            mixed += [a, b]
        proc = FrameProcessor(SMALL, REGISTRY)
        out = proc.feed_many(mixed)

        by_sat = {}
        for t in out:
            by_sat.setdefault(t.satellite_id, []).append(round(t.battery_pct))
        assert by_sat["SAT-001"] == [0, 1, 2, 3, 4, 5]
        assert by_sat["SAT-002"] == [50, 51, 52, 53, 54, 55]
        assert proc.stats.frames_lost == 0

    def test_a_loss_on_one_satellite_does_not_disturb_the_other(self):
        b1 = FrameBuilder(SMALL, scid=1)
        b2 = FrameBuilder(SMALL, scid=2)
        f1 = b1.frames_for_telemetry([_telemetry(i, "SAT-001") for i in range(6)])
        f2 = b2.frames_for_telemetry([_telemetry(i + 50, "SAT-002") for i in range(6)])
        del f1[3]
        proc = FrameProcessor(SMALL, REGISTRY)
        out = proc.feed_many([f for pair in zip(f1, f2) for f in pair] + f2[len(f1):])
        sat2 = [round(t.battery_pct) for t in out if t.satellite_id == "SAT-002"]
        assert sat2 == [50, 51, 52, 53, 54, 55]          # all of satellite 2 intact

    def test_orbit_type_survives_the_whole_path(self):
        builder, proc = _setup()
        out = proc.feed_many(builder.frames_for_telemetry(
            [_telemetry(1, orbit_type="HEO_MOLNIYA")]))
        assert out[0].orbit_type == "HEO_MOLNIYA"


# ── Never raises ──────────────────────────────────────────────────────────────

class TestRobustness:

    def test_random_garbage_never_raises(self):
        import random
        rng = random.Random(1234)
        _, proc = _setup()
        for _ in range(500):
            junk = bytes(rng.randrange(256) for _ in range(30))
            proc.feed(junk)                              # must not raise
        assert proc.stats.frames_received == 500

    def test_garbage_with_a_valid_crc_never_raises(self):
        import random
        rng = random.Random(99)
        _, proc = _setup()
        for _ in range(500):
            body = bytearray(rng.randrange(256) for _ in range(28))
            body[0] &= 0x3F                              # keep the version field 0
            body[0] = (body[0] & 0xC0) | (body[0] & 0x3F)
            body[1] = (body[1] & 0xF0) | 0x10            # SCID 1: known spacecraft
            frame = bytes(body) + crc16_ccitt(bytes(body)).to_bytes(2, "big")
            proc.feed(frame)                             # must not raise
        assert proc.stats.frames_ok > 0
