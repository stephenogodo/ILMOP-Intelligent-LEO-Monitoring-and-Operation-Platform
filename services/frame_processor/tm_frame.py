"""
services/frame_processor/tm_frame.py

CCSDS TM Transfer Frame: encode and decode (Sprint 8, S8-07).
Follows the layout of CCSDS 132.0-B-3 (TM Space Data Link Protocol).

WHAT A TRANSFER FRAME IS
────────────────────────
The fixed-size block that actually crosses the radio link.  Every frame on a
link has the same length (a "managed parameter").  Layout:

    6-byte primary header
    data field             (carries pieces of Space Packets)
    2-byte FECF            (CRC-16 over everything before it)

Primary header, bit by bit:

    byte 0-1   version (2 bits, always 0) | spacecraft ID (10) |
               virtual channel ID (3) | operational control field flag (1)
    byte 2     master channel frame count (8 bits, wraps at 256)
    byte 3     virtual channel frame count (8 bits, wraps at 256)
    byte 4-5   secondary header flag (1) | sync flag (1) | packet order flag (1) |
               segment length ID (2, always 0b11) | first header pointer (11)

THE FIRST HEADER POINTER (FHP)
──────────────────────────────
Says where the FIRST packet that STARTS in this frame begins inside the data
field, counted in bytes from the start of the data field:

    0 .. N-1   a packet starts at that offset.  Bytes before it are the tail
               of a packet that began in an earlier frame.
    0x7FF      no packet starts in this frame: the whole data field is the
               middle or end of a packet from earlier frames.
    0x7FE      the whole data field is idle fill.

WHAT IS NOT IMPLEMENTED
───────────────────────
Channel coding (Reed-Solomon, LDPC), the sync marker used to find frame
boundaries in a raw stream, the operational control field, and the frame
secondary header.  Frames arrive as complete byte blocks here.
"""

from dataclasses import dataclass

from services.frame_processor.crc import crc16_ccitt

PRIMARY_HEADER_LEN = 6
FECF_LEN           = 2

# Special first-header-pointer values.
FHP_NO_PACKET_START = 0x7FF
FHP_IDLE            = 0x7FE

MAX_SCID = 0x3FF     # 10 bits
MAX_VCID = 0x7       # 3 bits


class FrameError(ValueError):
    """Base class for every way a received frame can be rejected."""


class FrameLengthError(FrameError):
    """The block is not the configured frame length."""


class FrameCrcError(FrameError):
    """The checksum at the end does not match the contents."""


class FrameFormatError(FrameError):
    """The header is not a TM frame we understand (for example a bad version)."""


@dataclass(frozen=True)
class FrameConfig:
    """
    The managed parameters of the link.  Sender and receiver must agree.

    frame_length: total bytes in every frame, header and checksum included.
                  1115 is a common real-world value.  The standard allows
                  up to 2048.
    """

    frame_length: int = 1115

    def __post_init__(self):
        # The data field must be large enough to be useful (at least one
        # packet header) and the first header pointer is only 11 bits.
        if not (PRIMARY_HEADER_LEN + FECF_LEN + 7 <= self.frame_length <= 2048):
            raise ValueError("frame_length must be between 15 and 2048 bytes")

    @property
    def data_field_length(self) -> int:
        """Bytes available for packet data in each frame."""
        return self.frame_length - PRIMARY_HEADER_LEN - FECF_LEN


@dataclass(frozen=True)
class TMFrame:
    """One decoded transfer frame."""

    spacecraft_id:        int
    virtual_channel_id:   int
    master_channel_count: int      # counts every frame of the spacecraft (mod 256)
    virtual_channel_count: int     # counts frames of this virtual channel (mod 256)
    first_header_pointer: int
    data:                 bytes    # exactly config.data_field_length bytes

    def encode(self, config: FrameConfig) -> bytes:
        """Build the frame bytes: header, data field, then the CRC."""
        if not 0 <= self.spacecraft_id <= MAX_SCID:
            raise FrameFormatError(f"spacecraft ID {self.spacecraft_id} out of range")
        if not 0 <= self.virtual_channel_id <= MAX_VCID:
            raise FrameFormatError(f"virtual channel ID {self.virtual_channel_id} out of range")
        if len(self.data) != config.data_field_length:
            raise FrameLengthError(
                f"data field is {len(self.data)} bytes, expected {config.data_field_length}"
            )
        if not 0 <= self.first_header_pointer <= 0x7FF:
            raise FrameFormatError("first header pointer must fit in 11 bits")

        # Bytes 0-1: version 0 | SCID | VCID | OCF flag 0.
        word0 = (0 << 14) | (self.spacecraft_id << 4) | (self.virtual_channel_id << 1) | 0

        # Bytes 4-5: flags all 0 except segment length ID = 0b11, then the FHP.
        word2 = (0b11 << 11) | self.first_header_pointer

        header = (
            word0.to_bytes(2, "big")
            + bytes([self.master_channel_count & 0xFF, self.virtual_channel_count & 0xFF])
            + word2.to_bytes(2, "big")
        )
        body = header + self.data

        # The CRC covers the header and the data field.
        return body + crc16_ccitt(body).to_bytes(2, "big")


def decode_frame(raw: bytes, config: FrameConfig) -> TMFrame:
    """
    Check and decode one received frame.

    Checks run in this order, and each raises its own error:
        1. length is exactly config.frame_length        (FrameLengthError)
        2. CRC matches                                  (FrameCrcError)
        3. version field is 0                           (FrameFormatError)

    The CRC is checked BEFORE trusting any header field, because a corrupted
    frame's header cannot be believed either.
    """
    if len(raw) != config.frame_length:
        raise FrameLengthError(
            f"frame is {len(raw)} bytes, expected {config.frame_length}"
        )

    body, received_crc = raw[:-FECF_LEN], int.from_bytes(raw[-FECF_LEN:], "big")
    if crc16_ccitt(body) != received_crc:
        raise FrameCrcError("frame checksum mismatch")

    word0 = int.from_bytes(body[0:2], "big")
    word2 = int.from_bytes(body[4:6], "big")

    if (word0 >> 14) & 0b11 != 0:
        raise FrameFormatError("unsupported TM frame version")

    return TMFrame(
        spacecraft_id=(word0 >> 4) & MAX_SCID,
        virtual_channel_id=(word0 >> 1) & MAX_VCID,
        master_channel_count=body[2],
        virtual_channel_count=body[3],
        first_header_pointer=word2 & 0x7FF,
        data=bytes(body[PRIMARY_HEADER_LEN:]),
    )
