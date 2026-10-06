"""
services/scheduler/geometry.py

Ground station geometry — AOS/LOS computation.

Layer 1 of the ILMOP LEO Constellation Operations Suite.

Given a satellite position (from SGP4) and a ground station location,
computes whether the satellite is visible (above the elevation mask)
and returns the elevation and azimuth angles.

All angles in degrees. All distances in kilometres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EARTH_RADIUS_KM   = 6371.0          # mean Earth radius
EARTH_ROT_RAD_S   = 7.2921150e-5    # Earth rotation rate, rad/s
J2000_EPOCH_JD    = 2451545.0       # Julian date of J2000.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GroundStation:
    """A ground station with geodetic coordinates."""
    name:      str
    lat_deg:   float   # geodetic latitude,  −90 to +90
    lon_deg:   float   # longitude,         −180 to +180
    alt_km:    float   # altitude above WGS84 ellipsoid


@dataclass(frozen=True)
class Observation:
    """
    The result of observing a satellite from a ground station
    at one instant in time.
    """
    station:          GroundStation
    elevation_deg:    float     # negative means below horizon
    azimuth_deg:      float     # 0 = North, 90 = East
    range_km:         float     # slant range
    range_rate_km_s:  float     # positive = receding
    above_mask:       bool      # True if elevation >= elevation_mask_deg


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_km: float
                      ) -> np.ndarray:
    """Step 1 for the satellite position — Propagate the satellite position. SGP4 gives us the 
    satellite's position in the Earth-Centred Inertial (ECI) frame — 
    a coordinate system fixed to the stars, with origin at Earth's centre. 
    This is what the existing satellite_simulator already computes.

    Convert geodetic coordinates to ECEF (km).

    Uses the WGS84 ellipsoid.

    Parameters
    ----------
    lat_deg : geodetic latitude in degrees
    lon_deg : longitude in degrees
    alt_km  : altitude above ellipsoid in km

    Returns
    -------
    np.ndarray of shape (3,) — [X, Y, Z] in km
    """
    # WGS84 parameters
    a  = 6378.137       # semi-major axis, km
    f  = 1 / 298.257223563
    e2 = 2*f - f**2     # first eccentricity squared

    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)

    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)

    # radius of curvature in the prime vertical
    N = a / math.sqrt(1 - e2 * sin_lat**2)

    x = (N + alt_km) * cos_lat * cos_lon
    y = (N + alt_km) * cos_lat * sin_lon
    z = (N * (1 - e2) + alt_km) * sin_lat

    return np.array([x, y, z])


def _eci_to_ecef(eci_km: np.ndarray, gmst_rad: float) -> np.ndarray:
    """Step 2 (is for the ground station location)— Convert from ECI to 
    Earth-Centred Earth-Fixed (ECEF). ECEF rotates with the Earth. 
    The ground station is fixed in ECEF. We rotate the ECI position by 
    Earth's rotation angle (Greenwich Sidereal Time) to get both the 
    satellite and the ground station in the same frame.

    Rotate an ECI position vector to ECEF using the Greenwich Mean
    Sidereal Time (GMST) angle.

    This is a rotation about the Z-axis by -gmst_rad.

    Parameters
    ----------
    eci_km   : position in ECI frame, shape (3,), km
    gmst_rad : Greenwich Mean Sidereal Time in radians

    Returns
    -------
    np.ndarray of shape (3,) — ECEF position in km
    """
    cos_g = math.cos(gmst_rad)
    sin_g = math.sin(gmst_rad)

    # Rotation matrix: R_z(-gmst)
    R = np.array([
        [ cos_g,  sin_g, 0],
        [-sin_g,  cos_g, 0],
        [     0,      0, 1],
    ])

    return R @ eci_km


def _gmst_from_unix(unix_time_s: float) -> float:
    """
    Compute Greenwich Mean Sidereal Time (GMST) in radians
    from a Unix timestamp (seconds since 1970-01-01 00:00:00 UTC).

    Uses the standard IAU formula accurate to ~0.1 arcsecond
    for dates within a few centuries of J2000.

    Parameters
    ----------
    unix_time_s : Unix timestamp in seconds

    Returns
    -------
    GMST in radians, in range [0, 2π)
    """
    # Julian date from Unix time
    jd = unix_time_s / 86400.0 + 2440587.5

    # Julian centuries from J2000.0
    T = (jd - J2000_EPOCH_JD) / 36525.0

    # GMST in degrees (IAU 1982 formula)
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - J2000_EPOCH_JD)
        + 0.000387933 * T**2
        - T**3 / 38710000.0
    )

    # Normalise to [0, 360)
    gmst_deg = gmst_deg % 360.0

    return math.radians(gmst_deg)


# ---------------------------------------------------------------------------
# Core visibility computation
# ---------------------------------------------------------------------------

def observe(
    sat_eci_km:      np.ndarray,
    sat_vel_eci_km_s: np.ndarray,
    unix_time_s:     float,
    station:         GroundStation,
    elevation_mask_deg: float = 5.0,
) -> Observation:
    """
    Compute the observation angles and visibility of a satellite
    from a ground station at a given instant.

    Parameters
    ----------
    sat_eci_km        : satellite ECI position vector, km, shape (3,)
    sat_vel_eci_km_s  : satellite ECI velocity vector, km/s, shape (3,)
    unix_time_s       : observation time as Unix timestamp
    station           : the observing ground station
    elevation_mask_deg: minimum elevation for visibility (default 5°)

    Returns
    -------
    Observation dataclass with elevation, azimuth, range, range rate,
    and above_mask flag.

    Theory
    ------
    1. Convert station geodetic → ECEF.
    2. Rotate satellite ECI → ECEF using GMST.
    3. Compute topocentric vector: rho = sat_ecef − stn_ecef.
    4. Project rho into the station's local South-East-Z frame.
    5. Elevation = arcsin(Z / |rho|).
    6. Azimuth = atan2(East, North), measured clockwise from North.
    7. Range rate = dot(rho_hat, vel_ecef − vel_station).
       (station velocity from Earth rotation: v = omega × r_stn)
    """
    gmst = _gmst_from_unix(unix_time_s)

    # 1. Station ECEF position
    stn_ecef = _geodetic_to_ecef(
        station.lat_deg, station.lon_deg, station.alt_km
    )

    # 2. Satellite ECEF position
    sat_ecef = _eci_to_ecef(sat_eci_km, gmst)

    # 3. Topocentric vector (station → satellite), ECEF
    rho_ecef = sat_ecef - stn_ecef
    rho_mag  = float(np.linalg.norm(rho_ecef))

    # 4. Local South-East-Z unit vectors at the station
    #    (derived from the station's geodetic latitude and longitude)
    lat = math.radians(station.lat_deg)
    lon = math.radians(station.lon_deg)

    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)

    # Unit vector pointing Up (outward along the local vertical)
    up = np.array([
        cos_lat * cos_lon,
        cos_lat * sin_lon,
        sin_lat,
    ])

    # Unit vector pointing East
    east = np.array([-sin_lon, cos_lon, 0.0])

    # Unit vector pointing North
    north = np.array([
        -sin_lat * cos_lon,
        -sin_lat * sin_lon,
         cos_lat,
    ])

    # 5. Project topocentric vector onto local frame
    rho_up    = float(np.dot(rho_ecef, up))
    rho_east  = float(np.dot(rho_ecef, east))
    rho_north = float(np.dot(rho_ecef, north))

    # 6. Elevation and azimuth
    elevation_rad = math.asin(rho_up / rho_mag)
    elevation_deg = math.degrees(elevation_rad)

    azimuth_rad = math.atan2(rho_east, rho_north)
    azimuth_deg = math.degrees(azimuth_rad) % 360.0

    # 7. Range rate
    #    Station velocity due to Earth rotation: v_stn = omega_earth × r_stn
    omega = np.array([0.0, 0.0, EARTH_ROT_RAD_S])
    vel_stn_ecef = np.cross(omega, stn_ecef)

    #    Satellite velocity in ECEF (approximate — ignores Coriolis)
    sat_vel_ecef = _eci_to_ecef(sat_vel_eci_km_s, gmst)

    rho_hat      = rho_ecef / rho_mag
    range_rate   = float(np.dot(rho_hat, sat_vel_ecef - vel_stn_ecef))

    return Observation(
        station         = station,
        elevation_deg   = elevation_deg,
        azimuth_deg     = azimuth_deg,
        range_km        = rho_mag,
        range_rate_km_s = range_rate,
        above_mask      = elevation_deg >= elevation_mask_deg,
    )