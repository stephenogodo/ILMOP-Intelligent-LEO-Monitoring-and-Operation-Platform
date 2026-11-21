"""
services/frame_processor/processor.py

The ground-side frame processor (Sprint 8, S8-07).

WHAT IT DOES
────────────
Takes the raw bytes of received transfer frames and returns the Telemetry
records inside them.  This is "frame ingestion and decommutation".

    feed(frame_bytes) -> list of Telemetry (possibly empty)

THE STEPS FOR EACH FRAME
────────────────────────
    1. Check length and CRC.  A bad frame is dropped and counted.
    2. Look up the spacecraft ID.  An unknown one is dropped and counted.
    3. Check the frame counter for this virtual channel.
         repeat of the last counter   -> duplicate frame, ignored
         a jump                       -> frames were lost; the packet being
                                         assembled is thrown away, and
                                         decoding resumes at the next packet
                                         boundary the frame points to
    4. Use the first header pointer to split the frame's data into
         (a) the continuation of a packet begun in an earlier frame, and
         (b) new packets that start in this frame.
    5. Pull every complete packet out of the assembly buffer, and decode
       telemetry packets into Telemetry objects.

WHY A LOST FRAME DISCARDS THE PARTIAL PACKET
────────────────────────────────────────────
If the first half of a packet arrived and the second half was lost, joining
it to the next packet's bytes would produce a packet that looks complete but
is wrong.  Discarding it is the only safe choice.

NEVER RAISES ON BAD INPUT
─────────────────────────
Noise is normal on a radio link.  Every kind of bad input is counted in
`stats`, and feed() simply returns nothing for it.

ONE BUFFER PER VIRTUAL CHANNEL
──────────────────────────────
State (last counter, partial packet) is kept separately for each
(spacecraft, virtual channel) pair, so several satellites can share one
stream of frames without disturbing one another.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from shared.schemas.telemetry_schema import Telemetry
from services.frame_processor.space_packet import (
    IDLE_APID,
    PRIMARY_HEADER_LEN,
    SpacePacketError,
    decode_packet,
    packet_length_from_header,
)
from services.frame_processor.telemetry_codec import (
    TELEMETRY_APID,
    CodecError,
    ScidRegistry,
    UnknownSpacecraftError,
    decode_telemetry,
)
from services.frame_processor.tm_frame import (
    FHP_IDLE,
    FHP_NO_PACKET_START,
    FrameConfig,
    FrameCrcError,
    FrameError,
    FrameFormatError,
    FrameLengthError,
    decode_frame,
)


@dataclass
class ProcessorStats:
    """Running counts of everything the processor has seen."""

    frames_received:    int = 0
    frames_ok:          int = 0     # passed length, CRC and format checks
    length_errors:      int = 0
    crc_errors:         int = 0
    format_errors:      int = 0
    unknown_spacecraft: int = 0
    duplicate_frames:   int = 0
    frames_lost:        int = 0     # inferred from counter jumps
    idle_frames:        int = 0
    resyncs:            int = 0     # partial packet dropped, waiting for a boundary
    packets_decoded:    int = 0
    idle_packets:       int = 0
    unknown_apid:       int = 0
    malformed_packets:  int = 0
    telemetry_out:      int = 0


@dataclass
class _ChannelState:
    """What is remembered for one (spacecraft, virtual channel) pair."""

    last_count: Optional[int] = None          # last virtual channel frame count seen
    buffer: bytearray = field(default_factory=bytearray)   # packet being assembled
    # True when we have no trustworthy start of a packet (at startup, and
    # after any loss).  Continuation bytes are discarded until a frame's
    # first header pointer shows where a packet begins.
    awaiting_start: bool = True


class FrameProcessor:
    """Turns received frame bytes into Telemetry records."""

    def __init__(self, config: FrameConfig, registry: ScidRegistry) -> None:
        self._config   = config
        self._registry = registry
        self.stats     = ProcessorStats()
        self._channels: Dict[Tuple[int, int], _ChannelState] = {}

    # ── Public entry point ────────────────────────────────────────────────────

    def feed(self, raw: bytes) -> List[Telemetry]:
        """Process ONE received frame and return the telemetry it completed."""
        s = self.stats
        s.frames_received += 1

        # Step 1: length, CRC and header format.
        try:
            frame = decode_frame(raw, self._config)
        except FrameLengthError:
            s.length_errors += 1
            return []
        except FrameCrcError:
            s.crc_errors += 1
            return []
        except FrameFormatError:
            s.format_errors += 1
            return []
        s.frames_ok += 1

        # Step 2: is this a spacecraft we know?
        try:
            satellite_id = self._registry.satellite_for(frame.spacecraft_id)
        except UnknownSpacecraftError:
            s.unknown_spacecraft += 1
            return []

        channel = self._channels.setdefault(
            (frame.spacecraft_id, frame.virtual_channel_id), _ChannelState()
        )

        # Step 3: continuity of the frame counter.
        if not self._check_continuity(channel, frame.virtual_channel_count):
            return []        # a duplicate: nothing more to do

        # Step 4 and 5: reassemble and decode.
        return self._process_data(channel, frame.first_header_pointer,
                                  frame.data, satellite_id)

    def feed_many(self, frames) -> List[Telemetry]:
        """Process an iterable of frames in order; return all telemetry."""
        out: List[Telemetry] = []
        for raw in frames:
            out.extend(self.feed(raw))
        return out

    # ── Step 3 ────────────────────────────────────────────────────────────────

    def _check_continuity(self, channel: _ChannelState, count: int) -> bool:
        """
        Compare the frame counter with the one expected.

        Returns False for a duplicate (the caller should ignore the frame).
        On a jump, records the lost frames and discards the partial packet.
        """
        s = self.stats
        if channel.last_count is not None:
            if count == channel.last_count:
                s.duplicate_frames += 1
                return False
            expected = (channel.last_count + 1) & 0xFF
            lost = (count - expected) & 0xFF
            if lost:
                s.frames_lost += lost
                self._resync(channel)
        channel.last_count = count
        return True

    def _resync(self, channel: _ChannelState) -> None:
        """Throw away any partial packet and wait for a packet boundary."""
        if channel.buffer:
            self.stats.resyncs += 1
        channel.buffer.clear()
        channel.awaiting_start = True

    # ── Steps 4 and 5 ─────────────────────────────────────────────────────────

    def _process_data(self, channel: _ChannelState, fhp: int, data: bytes,
                      satellite_id: str) -> List[Telemetry]:
        s = self.stats

        # An idle-only frame carries no packet data.  A packet that was
        # still incomplete can never be finished, so drop it.
        if fhp == FHP_IDLE:
            s.idle_frames += 1
            self._resync(channel)
            channel.awaiting_start = False    # an idle frame is a clean boundary
            return []

        if fhp == FHP_NO_PACKET_START:
            # The whole data field continues a packet from earlier frames.
            if channel.awaiting_start:
                return []                      # no known start: cannot use it
            channel.buffer += data
        else:
            if fhp >= len(data):
                # A pointer past the end of the data field is nonsense.
                s.malformed_packets += 1
                self._resync(channel)
                return []
            head, tail = data[:fhp], data[fhp:]
            if head:
                # Bytes before the pointer finish the packet from earlier
                # frames, if we have its beginning; otherwise they are lost.
                if not channel.awaiting_start and channel.buffer:
                    channel.buffer += head
                else:
                    s.resyncs += 1
            # Anything still in the buffer is now a complete packet's worth
            # (or an unfinishable one); extract it, then start afresh at fhp.
            out = self._extract_packets(channel, satellite_id)
            if channel.buffer:
                s.resyncs += 1        # leftover bytes: an unfinishable packet
            channel.buffer.clear()
            channel.awaiting_start = False
            channel.buffer += tail
            return out + self._extract_packets(channel, satellite_id)

        return self._extract_packets(channel, satellite_id)

    def _extract_packets(self, channel: _ChannelState,
                         satellite_id: str) -> List[Telemetry]:
        """Pull every complete packet out of the buffer and decode it."""
        s = self.stats
        out: List[Telemetry] = []
        buf = channel.buffer

        while len(buf) >= PRIMARY_HEADER_LEN:
            total = packet_length_from_header(bytes(buf[:PRIMARY_HEADER_LEN]))
            if len(buf) < total:
                break                          # wait for the rest in later frames
            raw_packet = bytes(buf[:total])
            del buf[:total]
            telemetry = self._handle_packet(raw_packet, satellite_id)
            if telemetry is not None:
                out.append(telemetry)
        return out

    def _handle_packet(self, raw_packet: bytes,
                       satellite_id: str) -> Optional[Telemetry]:
        """Decode one complete packet; return Telemetry, or None if it is not telemetry."""
        s = self.stats
        try:
            packet = decode_packet(raw_packet)
        except SpacePacketError:
            s.malformed_packets += 1
            return None
        s.packets_decoded += 1

        if packet.apid == IDLE_APID:
            s.idle_packets += 1
            return None
        if packet.apid != TELEMETRY_APID:
            s.unknown_apid += 1
            return None

        try:
            telemetry = decode_telemetry(packet.data, satellite_id)
        except (CodecError, ValueError):
            s.malformed_packets += 1
            return None
        s.telemetry_out += 1
        return telemetry
