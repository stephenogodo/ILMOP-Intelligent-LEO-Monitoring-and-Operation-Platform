"""
services/frame_processor/space_packet.py

CCSDS Space Packet: encode and decode (Sprint 8, S8-07).
Follows the layout of CCSDS 133.0-B-2 (Space Packet Protocol).

WHAT A SPACE PACKET IS
──────────────────────
The unit that carries one application's data, here one telemetry record.
It is a 6-byte primary header followed by a data field:

    byte 0-1   version (3 bits) | type (1) | secondary header flag (1) | APID (11)
    byte 2-3   sequence flags (2) | sequence count (14)
    byte 4-5   packet data length  = (number of bytes in the data field) - 1
    byte 6...  data field

    version        always 0
    type           0 = telemetry, 1 = telecommand
    APID           Application Process ID: says which kind of data this is
    sequence count counts packets per APID, wrapping at 16384
    data length    the standard stores the length MINUS ONE; a one-byte data
                   field is written as 0.  This is the classic off-by-one
                   to get right, and the tests check it.

The APID 0x7FF (2047) is reserved for IDLE packets, which carry only fill
bytes and are ignored by the receiver.
"""

from dataclasses import dataclass

# Sizes and limits from the standard.
PRIMARY_HEADER_LEN = 6
MAX_APID           = 0x7FF          # 11 bits
MAX_SEQ_COUNT      = 0x3FFF         # 14 bits
MAX_DATA_FIELD_LEN = 65536          # the 16-bit length field stores len - 1
IDLE_APID          = 0x7FF

# Sequence flags: 0b11 means "unsegmented", i.e. this packet is complete on
# its own.  ILMOP never splits one packet into several, so this is constant.
SEQ_FLAGS_UNSEGMENTED = 0b11


class SpacePacketError(ValueError):
    """Raised when bytes do not form a valid Space Packet."""


@dataclass(frozen=True)
class SpacePacket:
    """One Space Packet: an APID, a sequence count and the data field bytes."""

    apid:           int
    sequence_count: int
    data:           bytes

    # 0 = telemetry (what ILMOP downlinks), 1 = telecommand.
    packet_type: int = 0

    # True when the data field begins with a secondary header (ILMOP puts a
    # 6-byte time field there for telemetry packets).
    secondary_header: bool = True

    def encode(self) -> bytes:
        """Return the packet as bytes: 6-byte header followed by the data field."""
        if not 0 <= self.apid <= MAX_APID:
            raise SpacePacketError(f"APID {self.apid} out of range 0..{MAX_APID}")
        if not 0 <= self.sequence_count <= MAX_SEQ_COUNT:
            raise SpacePacketError(f"sequence count {self.sequence_count} out of range")
        if not 1 <= len(self.data) <= MAX_DATA_FIELD_LEN:
            raise SpacePacketError("data field must be 1..65536 bytes")

        # First 16 bits: version 0 | type | secondary header flag | APID.
        word0 = (
            (0 << 13)
            | ((self.packet_type & 1) << 12)
            | ((1 if self.secondary_header else 0) << 11)
            | self.apid
        )
        # Second 16 bits: sequence flags | sequence count.
        word1 = (SEQ_FLAGS_UNSEGMENTED << 14) | self.sequence_count
        # Third 16 bits: data length minus one.
        word2 = len(self.data) - 1

        header = word0.to_bytes(2, "big") + word1.to_bytes(2, "big") + word2.to_bytes(2, "big")
        return header + self.data


def packet_length_from_header(header: bytes) -> int:
    """
    Given at least the first 6 bytes of a packet, return the packet's TOTAL
    length in bytes (header plus data field).

    The receiver uses this to know how many bytes to collect, even when the
    packet arrives spread over several frames.
    """
    if len(header) < PRIMARY_HEADER_LEN:
        raise SpacePacketError("need 6 bytes to read the packet length")
    data_length_minus_one = int.from_bytes(header[4:6], "big")
    return PRIMARY_HEADER_LEN + data_length_minus_one + 1


def decode_packet(raw: bytes) -> SpacePacket:
    """
    Decode ONE complete packet.  `raw` must be exactly the packet's bytes.

    Raises SpacePacketError if the version is not 0, or the length in the
    header does not match the number of bytes supplied.
    """
    if len(raw) < PRIMARY_HEADER_LEN + 1:
        raise SpacePacketError("packet shorter than header plus one data byte")

    word0 = int.from_bytes(raw[0:2], "big")
    word1 = int.from_bytes(raw[2:4], "big")

    version = (word0 >> 13) & 0b111
    if version != 0:
        raise SpacePacketError(f"unsupported packet version {version}")

    total = packet_length_from_header(raw)
    if total != len(raw):
        raise SpacePacketError(
            f"length field says {total} bytes but {len(raw)} were supplied"
        )

    return SpacePacket(
        apid=word0 & MAX_APID,
        sequence_count=word1 & MAX_SEQ_COUNT,
        data=bytes(raw[PRIMARY_HEADER_LEN:]),
        packet_type=(word0 >> 12) & 1,
        secondary_header=bool((word0 >> 11) & 1),
    )
