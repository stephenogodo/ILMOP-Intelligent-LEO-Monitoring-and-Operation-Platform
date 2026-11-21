"""
services/frame_processor/framer.py

The spacecraft-side encoder (Sprint 8, S8-07).

WHAT THIS IS
────────────
The reverse of the ground processor.  It plays the part of the satellite's
onboard software: it takes Telemetry, wraps each record in a Space Packet,
packs the packets into fixed-size transfer frames, and adds the counters and
checksum.  Without it there would be nothing to decode in the tests, and it
lets the simulator produce realistic downlink bytes.

HOW PACKING WORKS
─────────────────
All packets of one call are joined into one byte stream.  The stream is cut
into pieces exactly one data field long, and each piece becomes a frame.  A
packet that does not fit in the space left in a frame simply continues into
the next frame, which is why a packet can span several frames.

FILL
────
The last frame is padded to full length with an IDLE Space Packet (APID
0x7FF), which the receiver ignores.  A Space Packet needs at least 7 bytes,
so when 1 to 6 bytes remain the idle packet is made long enough to run
into one extra frame; that frame is then entirely the tail of the idle
packet.

THE FIRST HEADER POINTER
────────────────────────
For each frame it is the offset of the first packet that STARTS inside that
frame, or 0x7FF if none does.  The receiver relies on it to find packet
boundaries after a lost frame.

STATE
─────
The builder remembers its frame counters and packet sequence counters
between calls, as a real spacecraft would.  Each call starts on a fresh
frame, so a call's last frame is always padded.
"""

from typing import Dict, Iterable, List

from shared.schemas.telemetry_schema import Telemetry
from services.frame_processor.space_packet import (
    IDLE_APID,
    MAX_SEQ_COUNT,
    SpacePacket,
)
from services.frame_processor.telemetry_codec import (
    TELEMETRY_APID,
    ScidRegistry,
    encode_telemetry,
)
from services.frame_processor.tm_frame import (
    FHP_IDLE,
    FHP_NO_PACKET_START,
    FrameConfig,
    TMFrame,
)

# Smallest possible Space Packet: 6-byte header plus one data byte.
_MIN_PACKET = 7


class FrameBuilder:
    """Builds transfer frames for ONE spacecraft on ONE virtual channel."""

    def __init__(self, config: FrameConfig, scid: int, vcid: int = 0) -> None:
        self._config = config
        self._scid   = scid
        self._vcid   = vcid

        # Frame counters (8 bits, wrap at 256).  Both start at 0.
        self._mc_count = 0
        self._vc_count = 0

        # Packet sequence counter per APID (14 bits, wraps at 16384).
        self._seq: Dict[int, int] = {}

    # ── Packets ───────────────────────────────────────────────────────────────

    def telemetry_packet(self, telemetry: Telemetry) -> SpacePacket:
        """Wrap one Telemetry record in a telemetry Space Packet."""
        return SpacePacket(
            apid=TELEMETRY_APID,
            sequence_count=self._next_seq(TELEMETRY_APID),
            data=encode_telemetry(telemetry),
        )

    def _next_seq(self, apid: int) -> int:
        value = self._seq.get(apid, 0)
        self._seq[apid] = (value + 1) & MAX_SEQ_COUNT
        return value

    def _idle_packet(self, total_bytes: int) -> bytes:
        """An idle packet exactly `total_bytes` long (must be at least 7)."""
        packet = SpacePacket(
            apid=IDLE_APID,
            sequence_count=self._next_seq(IDLE_APID),
            data=b"\x00" * (total_bytes - 6),
            secondary_header=False,
        )
        return packet.encode()

    # ── Frames ────────────────────────────────────────────────────────────────

    def frames_for_packets(self, packets: Iterable[SpacePacket]) -> List[bytes]:
        """
        Pack packets into frames and return the frame bytes, oldest first.

        Returns an empty list when there are no packets.
        """
        dfl = self._config.data_field_length

        # 1. Join the packets into one stream, noting where each one starts.
        stream = bytearray()
        starts: List[int] = []
        for packet in packets:
            starts.append(len(stream))
            stream += packet.encode()
        if not stream:
            return []

        # 2. Pad the last frame to full length with an idle packet.
        remainder = (-len(stream)) % dfl
        if remainder:
            starts.append(len(stream))
            if remainder >= _MIN_PACKET:
                stream += self._idle_packet(remainder)
            else:
                # Too small for a packet: make the idle packet run into one
                # extra frame instead (see module docstring).
                stream += self._idle_packet(remainder + dfl)

        # 3. Cut the stream into frames.
        frames: List[bytes] = []
        for frame_start in range(0, len(stream), dfl):
            frame_end = frame_start + dfl
            # First packet start inside this frame, as an offset into it.
            inside = [s - frame_start for s in starts if frame_start <= s < frame_end]
            fhp = inside[0] if inside else FHP_NO_PACKET_START
            frames.append(self._make_frame(bytes(stream[frame_start:frame_end]), fhp))
        return frames

    def frames_for_telemetry(self, records: Iterable[Telemetry]) -> List[bytes]:
        """Convenience: telemetry records straight to frames."""
        return self.frames_for_packets(self.telemetry_packet(t) for t in records)

    def idle_frame(self) -> bytes:
        """A frame that carries only idle fill (pointer value 0x7FE)."""
        return self._make_frame(b"\x00" * self._config.data_field_length, FHP_IDLE)

    def _make_frame(self, data: bytes, fhp: int) -> bytes:
        frame = TMFrame(
            spacecraft_id=self._scid,
            virtual_channel_id=self._vcid,
            master_channel_count=self._mc_count,
            virtual_channel_count=self._vc_count,
            first_header_pointer=fhp,
            data=data,
        ).encode(self._config)
        # Advance both counters, wrapping at 256.
        self._mc_count = (self._mc_count + 1) & 0xFF
        self._vc_count = (self._vc_count + 1) & 0xFF
        return frame


def make_builders(config: FrameConfig, registry: ScidRegistry,
                  satellite_ids: Iterable[str]) -> Dict[str, FrameBuilder]:
    """One FrameBuilder per satellite, each with its own counters."""
    return {
        sat: FrameBuilder(config, registry.scid_for(sat)) for sat in satellite_ids
    }
