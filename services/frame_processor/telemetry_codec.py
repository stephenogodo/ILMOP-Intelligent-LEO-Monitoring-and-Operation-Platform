"""
services/frame_processor/telemetry_codec.py

The binary layout of an ILMOP telemetry packet, and the table that maps
spacecraft IDs to satellite names (Sprint 8, S8-07).

WHY A FIXED BINARY LAYOUT
─────────────────────────
A satellite does not send JSON.  Bandwidth is scarce, so it sends a fixed
sequence of numbers whose meaning is agreed in advance.  The receiver
decodes them by position.  This module is that agreement.

THE TELEMETRY PACKET (APID 0x010)
─────────────────────────────────
The packet data field is:

    secondary header (6 bytes)   time: 4 bytes whole seconds + 2 bytes fraction
    user data (45 bytes)         11 measurements, then 1 flag byte

User data, big-endian (the CCSDS convention), in this order:

    latitude_deg, longitude_deg, altitude_km, battery_pct, battery_voltage_v,
    solar_panel_power_w, temperature_c, cpu_utilization_pct,
    memory_utilization_pct, downlink_rate_mbps, uplink_rate_mbps
                  each an IEEE 754 32-bit float (4 bytes)
    flags         1 byte:  bit0 in_eclipse     bit1 in_contact
                           bit2 safe_mode      bit3 anomaly_flag
                           bit4 fault_injected bit5 orbit type (1 = HEO_MOLNIYA)

PRECISION (stated plainly)
──────────────────────────
32-bit floats keep about 7 significant digits.  That is far more than these
quantities need, but it is NOT lossless against the simulator's 64-bit
values, so a decoded record equals the original only to within about one
part in ten million.  Real missions often use scaled integers instead; floats
were chosen here for readability.

THE TIME FIELD
──────────────
Whole seconds since 1970-01-01 UTC (a CCSDS time code allows an
agency-defined epoch) plus a 16-bit fraction in units of 1/65536 second,
a resolution of about 15 microseconds.

WHICH SATELLITE
───────────────
A packet does not carry the satellite's name.  The frame header carries a
spacecraft ID (SCID), and ScidRegistry maps it to the name.
"""

import struct
from datetime import datetime, timezone
from typing import Dict

from shared.schemas.telemetry_schema import Telemetry

# The APID used for telemetry packets in ILMOP.
TELEMETRY_APID = 0x010

# Big-endian: 11 floats then one unsigned byte.
_USER_STRUCT = struct.Struct(">11fB")
_TIME_STRUCT = struct.Struct(">IH")

SECONDARY_HEADER_LEN = _TIME_STRUCT.size        # 6
USER_DATA_LEN        = _USER_STRUCT.size        # 45
TELEMETRY_DATA_LEN   = SECONDARY_HEADER_LEN + USER_DATA_LEN   # 51

# Bit positions inside the flag byte.
_BIT_ECLIPSE, _BIT_CONTACT, _BIT_SAFE, _BIT_ANOMALY, _BIT_FAULT, _BIT_HEO = range(6)


class CodecError(ValueError):
    """Raised when telemetry bytes cannot be decoded."""


class UnknownSpacecraftError(KeyError):
    """Raised when a spacecraft ID or satellite name is not registered."""


class ScidRegistry:
    """Two-way table between spacecraft IDs (numbers) and satellite names."""

    def __init__(self, mapping: Dict[str, int]) -> None:
        # mapping: satellite name -> SCID, e.g. {"SAT-001": 1}
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("two satellites share one spacecraft ID")
        for scid in mapping.values():
            if not 0 <= scid <= 0x3FF:
                raise ValueError(f"spacecraft ID {scid} out of range 0..1023")
        self._by_name = dict(mapping)
        self._by_scid = {scid: name for name, scid in mapping.items()}

    def scid_for(self, satellite_id: str) -> int:
        try:
            return self._by_name[satellite_id]
        except KeyError:
            raise UnknownSpacecraftError(satellite_id) from None

    def satellite_for(self, scid: int) -> str:
        try:
            return self._by_scid[scid]
        except KeyError:
            raise UnknownSpacecraftError(scid) from None


def _encode_time(ts: datetime) -> bytes:
    """Timestamp -> 6 bytes (whole seconds, then 1/65536 s fraction)."""
    seconds_float = ts.timestamp()
    seconds = int(seconds_float // 1)
    fine = round((seconds_float - seconds) * 65536)
    if fine == 65536:            # rounding reached the next whole second
        seconds += 1
        fine = 0
    return _TIME_STRUCT.pack(seconds, fine)


def _decode_time(raw: bytes) -> datetime:
    seconds, fine = _TIME_STRUCT.unpack(raw)
    return datetime.fromtimestamp(seconds + fine / 65536, tz=timezone.utc)


def encode_telemetry(t: Telemetry) -> bytes:
    """Telemetry -> the 51-byte packet data field (time + measurements + flags)."""
    flags = (
        (int(t.in_eclipse)     << _BIT_ECLIPSE)
        | (int(t.in_contact)     << _BIT_CONTACT)
        | (int(t.safe_mode)      << _BIT_SAFE)
        | (int(t.anomaly_flag)   << _BIT_ANOMALY)
        | (int(t.fault_injected) << _BIT_FAULT)
        | (int(t.orbit_type == "HEO_MOLNIYA") << _BIT_HEO)
    )
    user = _USER_STRUCT.pack(
        t.latitude_deg, t.longitude_deg, t.altitude_km,
        t.battery_pct, t.battery_voltage_v, t.solar_panel_power_w,
        t.temperature_c, t.cpu_utilization_pct, t.memory_utilization_pct,
        t.downlink_rate_mbps, t.uplink_rate_mbps,
        flags,
    )
    return _encode_time(t.timestamp) + user


def decode_telemetry(data: bytes, satellite_id: str) -> Telemetry:
    """
    The 51-byte packet data field -> Telemetry.

    satellite_id comes from the frame's spacecraft ID (see ScidRegistry),
    since the packet itself does not name the satellite.
    """
    if len(data) != TELEMETRY_DATA_LEN:
        raise CodecError(
            f"telemetry data field is {len(data)} bytes, expected {TELEMETRY_DATA_LEN}"
        )

    timestamp = _decode_time(data[:SECONDARY_HEADER_LEN])
    (lat, lon, alt, batt_pct, batt_v, solar_w, temp, cpu, mem, dl, ul,
     flags) = _USER_STRUCT.unpack(data[SECONDARY_HEADER_LEN:])

    def bit(n: int) -> bool:
        return bool((flags >> n) & 1)

    return Telemetry(
        satellite_id=satellite_id,
        timestamp=timestamp,
        latitude_deg=lat, longitude_deg=lon, altitude_km=alt,
        in_eclipse=bit(_BIT_ECLIPSE), in_contact=bit(_BIT_CONTACT),
        orbit_type="HEO_MOLNIYA" if bit(_BIT_HEO) else "LEO_CIRCULAR",
        battery_pct=batt_pct, battery_voltage_v=batt_v, solar_panel_power_w=solar_w,
        temperature_c=temp,
        cpu_utilization_pct=cpu, memory_utilization_pct=mem,
        downlink_rate_mbps=dl, uplink_rate_mbps=ul,
        safe_mode=bit(_BIT_SAFE), anomaly_flag=bit(_BIT_ANOMALY),
        fault_injected=bit(_BIT_FAULT),
    )
