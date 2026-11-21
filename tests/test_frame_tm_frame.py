"""
tests/test_frame_tm_frame.py

Tests for the TM Transfer Frame layer (S8-07).
"""

import pytest

from services.frame_processor.crc import crc16_ccitt
from services.frame_processor.tm_frame import (
    FHP_IDLE,
    FHP_NO_PACKET_START,
    FrameConfig,
    FrameCrcError,
    FrameFormatError,
    FrameLengthError,
    TMFrame,
    decode_frame,
)

CONFIG = FrameConfig(frame_length=40)
DFL    = CONFIG.data_field_length          # 40 - 6 - 2 = 32


def _frame(**over):
    base = dict(spacecraft_id=5, virtual_channel_id=2, master_channel_count=7,
                virtual_channel_count=9, first_header_pointer=3, data=bytes(range(DFL)))
    base.update(over)
    return TMFrame(**base)


class TestConfig:

    def test_data_field_length(self):
        assert FrameConfig(frame_length=1115).data_field_length == 1115 - 8

    def test_too_short_is_refused(self):
        with pytest.raises(ValueError):
            FrameConfig(frame_length=10)

    def test_too_long_is_refused(self):
        with pytest.raises(ValueError):
            FrameConfig(frame_length=2049)


class TestEncodeDecode:

    def test_round_trip(self):
        frame = _frame()
        assert decode_frame(frame.encode(CONFIG), CONFIG) == frame

    def test_encoded_length_is_the_configured_length(self):
        assert len(_frame().encode(CONFIG)) == 40

    def test_header_bits(self):
        # SCID 5, VCID 2, FHP 3, counters 7 and 9.
        #   word0 = 00 | 0000000101 | 010 | 0   = 0x0054
        #   word2 = three flag bits 000, segment length ID 11, FHP 3
        #         = 0b00011 00000000011 = 0x1803
        raw = _frame().encode(CONFIG)
        assert raw[0:2] == bytes([0x00, 0x54])
        assert raw[2] == 7 and raw[3] == 9
        assert raw[4:6] == bytes([0x18, 0x03])

    def test_crc_covers_everything_before_it(self):
        raw = _frame().encode(CONFIG)
        assert int.from_bytes(raw[-2:], "big") == crc16_ccitt(raw[:-2])

    def test_counters_wrap_to_eight_bits(self):
        raw = _frame(master_channel_count=300, virtual_channel_count=257).encode(CONFIG)
        decoded = decode_frame(raw, CONFIG)
        assert decoded.master_channel_count == 300 & 0xFF
        assert decoded.virtual_channel_count == 1

    def test_special_pointer_values_round_trip(self):
        for fhp in (FHP_NO_PACKET_START, FHP_IDLE, 0):
            raw = _frame(first_header_pointer=fhp).encode(CONFIG)
            assert decode_frame(raw, CONFIG).first_header_pointer == fhp


class TestRejection:

    def test_wrong_length_is_refused(self):
        raw = _frame().encode(CONFIG)
        with pytest.raises(FrameLengthError):
            decode_frame(raw[:-1], CONFIG)

    def test_flipped_bit_in_the_data_is_caught(self):
        raw = bytearray(_frame().encode(CONFIG))
        raw[20] ^= 0x04
        with pytest.raises(FrameCrcError):
            decode_frame(bytes(raw), CONFIG)

    def test_flipped_bit_in_the_header_is_caught(self):
        raw = bytearray(_frame().encode(CONFIG))
        raw[1] ^= 0x10
        with pytest.raises(FrameCrcError):
            decode_frame(bytes(raw), CONFIG)

    def test_damaged_crc_is_caught(self):
        raw = bytearray(_frame().encode(CONFIG))
        raw[-1] ^= 0xFF
        with pytest.raises(FrameCrcError):
            decode_frame(bytes(raw), CONFIG)

    def test_bad_version_is_refused_even_with_a_valid_crc(self):
        raw = bytearray(_frame().encode(CONFIG))
        raw[0] |= 0x40                                   # version bits
        raw[-2:] = crc16_ccitt(bytes(raw[:-2])).to_bytes(2, "big")   # fix the CRC
        with pytest.raises(FrameFormatError):
            decode_frame(bytes(raw), CONFIG)

    def test_wrong_data_field_size_is_refused_when_encoding(self):
        with pytest.raises(FrameLengthError):
            _frame(data=b"short").encode(CONFIG)

    def test_spacecraft_id_out_of_range_is_refused(self):
        with pytest.raises(FrameFormatError):
            _frame(spacecraft_id=1024).encode(CONFIG)
