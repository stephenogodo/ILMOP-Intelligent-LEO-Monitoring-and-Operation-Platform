"""
services/frame_processor/crc.py

The 16-bit checksum used at the end of every CCSDS transfer frame
(Sprint 8, S8-07).

WHAT A CHECKSUM IS FOR
──────────────────────
The sender computes a number from the bytes of a frame and appends it.  The
receiver recomputes it.  If one bit was flipped by noise on the radio link,
the two numbers differ and the frame is thrown away.

WHICH ONE
─────────
CCSDS names this the Frame Error Control Field (FECF).  It is CRC-16 with
polynomial 0x1021 and initial value 0xFFFF, no bit reflection and no final
XOR (the variant called CRC-16/CCITT-FALSE).  Its standard check value is:
crc16(b"123456789") == 0x29B1, which the tests verify.
"""


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    """Return the CRC-16/CCITT-FALSE checksum of `data` as an integer 0..65535."""
    crc = initial
    for byte in data:
        # Bring the next byte into the top half of the 16-bit register.
        crc ^= byte << 8
        # Process the byte one bit at a time.
        for _ in range(8):
            if crc & 0x8000:
                # Top bit set: shift and apply the polynomial 0x1021.
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc
