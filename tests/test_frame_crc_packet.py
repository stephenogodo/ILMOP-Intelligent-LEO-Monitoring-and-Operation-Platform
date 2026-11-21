"""
tests/test_frame_crc_packet.py

Tests for the CRC-16 and the Space Packet layer (S8-07).
"""

import pytest

from services.frame_processor.crc import crc16_ccitt
from services.frame_processor.space_packet import (
    IDLE_APID,
    SpacePacket,
    SpacePacketError,
    decode_packet,
    packet_length_from_header,
)


class TestCrc:

    def test_standard_check_value(self):
        # The published check value for CRC-16/CCITT-FALSE.
        assert crc16_ccitt(b"123456789") == 0x29B1

    def test_empty_input_returns_the_initial_value(self):
        assert crc16_ccitt(b"") == 0xFFFF

    def test_a_single_flipped_bit_changes_the_crc(self):
        original = bytes(range(64))
        damaged = bytearray(original)
        damaged[10] ^= 0x01
        assert crc16_ccitt(original) != crc16_ccitt(bytes(damaged))

    def test_result_fits_in_16_bits(self):
        assert 0 <= crc16_ccitt(b"anything at all") <= 0xFFFF


class TestSpacePacket:

    def test_round_trip(self):
        packet = SpacePacket(apid=0x10, sequence_count=1234, data=b"hello world")
        assert decode_packet(packet.encode()) == packet

    def test_header_bits(self):
        # Worked by hand.  APID 0x010, secondary header flag set, TM type 0:
        #   word0 = 0b000 0 1 00000010000 = 0x0810
        #   word1 = sequence flags 0b11, count 5 -> 0b11 00000000000101 = 0xC005
        #   word2 = data length 4 bytes -> stored as 3
        raw = SpacePacket(apid=0x10, sequence_count=5, data=b"abcd").encode()
        assert raw[:6] == bytes([0x08, 0x10, 0xC0, 0x05, 0x00, 0x03])
        assert raw[6:] == b"abcd"

    def test_length_field_is_data_length_minus_one(self):
        raw = SpacePacket(apid=1, sequence_count=0, data=b"x" * 10).encode()
        assert int.from_bytes(raw[4:6], "big") == 9

    def test_total_length_from_header(self):
        raw = SpacePacket(apid=1, sequence_count=0, data=b"x" * 10).encode()
        assert packet_length_from_header(raw[:6]) == 16 == len(raw)

    def test_a_single_data_byte_is_legal(self):
        raw = SpacePacket(apid=1, sequence_count=0, data=b"x").encode()
        assert len(raw) == 7 and decode_packet(raw).data == b"x"

    def test_idle_apid_round_trips(self):
        packet = SpacePacket(apid=IDLE_APID, sequence_count=0, data=b"\x00" * 4,
                             secondary_header=False)
        decoded = decode_packet(packet.encode())
        assert decoded.apid == IDLE_APID and decoded.secondary_header is False

    def test_apid_out_of_range_is_refused(self):
        with pytest.raises(SpacePacketError):
            SpacePacket(apid=0x800, sequence_count=0, data=b"x").encode()

    def test_sequence_count_out_of_range_is_refused(self):
        with pytest.raises(SpacePacketError):
            SpacePacket(apid=1, sequence_count=0x4000, data=b"x").encode()

    def test_empty_data_is_refused(self):
        with pytest.raises(SpacePacketError):
            SpacePacket(apid=1, sequence_count=0, data=b"").encode()

    def test_wrong_length_is_refused_when_decoding(self):
        raw = SpacePacket(apid=1, sequence_count=0, data=b"abcd").encode()
        with pytest.raises(SpacePacketError):
            decode_packet(raw[:-1])

    def test_unsupported_version_is_refused(self):
        raw = bytearray(SpacePacket(apid=1, sequence_count=0, data=b"abcd").encode())
        raw[0] |= 0x20                       # set a version bit
        with pytest.raises(SpacePacketError):
            decode_packet(bytes(raw))

    def test_too_short_to_read_a_length_is_refused(self):
        with pytest.raises(SpacePacketError):
            packet_length_from_header(b"\x00\x01")
