"""
services/otfs/orbital_profile.py

OTFS Orbital Profile Exporter
==============================

Exports a time-series of OrbitalFrame records from ILMOP's SGP4
orbit model.  Each frame contains the four orbital parameters
required by the OTFS channel emulator and the fleet dashboard:

    range_m        → free-space path loss  FSPL(t)
    range_rate_ms  → Doppler shift         ν(t) = ṙ(t)·f_c/c
    elevation_deg  → Rician K-factor       K(θ(t))
    azimuth_deg    → antenna pointing, fleet dashboard, ILP scheduler

All four values come from a single SGP4 propagation step per tick,
so there is no redundant computation.

Design principle
----------------
This module is the data bridge between ILMOP's orbital infrastructure
and the OTFS waveform layer.  Downstream consumers (channel emulator,
navigation validator, fleet dashboard) all read OrbitalFrame sequences
without needing to know anything about SGP4, geodetic coordinates, or
the ILMOP orbit model internals.

Usage
-----
    from services.otfs.orbital_profile import OrbitalProfileExporter

    exporter = OrbitalProfileExporter(
        satellite_id    = "SAT-A1",
        ground_lat_deg  = 52.2,   # Cambridge ground station
        ground_lon_deg  = 0.12,
        ground_alt_m    = 20.0,
    )

    # Export one OTFS frame worth of profile (e.g. 480 seconds)
    profile = exporter.export(
        start_epoch = datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc),
        duration_s  = 480,
        step_s      = 1,
    )

    for frame in profile:
        print(frame.range_m, frame.range_rate_ms,
              frame.elevation_deg, frame.azimuth_deg)

References
----------
- Vallado, D. A., Crawford, P., Hujsak, R., & Kelso, T. S. (2006).
  Revisiting spacetrack report #3. AIAA 2006-6753.
- Hoots, F. R., & Roehrich, R. L. (1980). Spacetrack Report No. 3.
- Montenbruck, O., & Gill, E. (2000). Satellite Orbits. Springer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sgp4.api import Satrec, jday
from sgp4.conveniences import sat_epoch_datetime

# ── constants ──────────────────────────────────────────────────────────────
EARTH_RADIUS_M  = 6_378_137.0          # WGS-84 equatorial radius (m)
EARTH_MU        = 3.986_004_418e14     # gravitational parameter (m³/s²)
DEG2RAD         = math.pi / 180.0
RAD2DEG         = 180.0 / math.pi


# ── OrbitalFrame dataclass ─────────────────────────────────────────────────

@dataclass
class OrbitalFrame:
    """
    A single-epoch snapshot of the orbital geometry between a satellite
    and a ground station.

    Attributes
    ----------
    timestamp : datetime
        UTC epoch of this observation (timezone-aware).
    satellite_id : str
        Identifier of the satellite (e.g. "SAT-A1").
    range_m : float
        Slant range from satellite to ground station in metres.
        Used by the channel emulator to compute F1` SPL(t).
    range_rate_ms : float
        Radial range rate (positive = receding) in m/s.
        Used by the channel emulator to compute Doppler shift
        ν(t) = range_rate_ms × f_carrier / c.
    elevation_deg : float
        Elevation angle of the satellite above the ground station
        horizon in degrees (−90° to +90°).
        Used by the channel emulator to compute Rician K-factor K(θ(t)).
        Negative values mean the satellite is below the horizon.
    azimuth_deg : float
        Azimuth of the satellite measured clockwise from north at the
        ground station in degrees (0° to 360°).
        Used by the fleet dashboard, ILP ground station scheduler,
        and antenna-pointing models.  Not consumed by the channel
        emulator but included for forward compatibility.
    in_view : bool
        True when elevation_deg ≥ min_elevation_deg (set at construction
        time).  Frames with in_view=False are included in the profile so
        callers can see the full pass geometry, but the channel emulator
        skips them.
    """
    timestamp:      datetime
    satellite_id:   str
    range_m:        float
    range_rate_ms:  float
    elevation_deg:  float
    azimuth_deg:    float
    in_view:        bool


# ── OrbitalProfileExporter ────────────────────────────────────────────────

class OrbitalProfileExporter:
    """
    Generates a time-series of OrbitalFrame records for one satellite
    over a specified observation window, using ILMOP's SGP4 orbit model.

    The exporter is initialised with the orbital elements of the
    satellite and the geodetic coordinates of the ground station.
    Call export() to produce an OrbitalFrame sequence for any
    epoch and duration.

    Parameters
    ----------
    satellite_id : str
        Satellite identifier, used to populate OrbitalFrame.satellite_id.
    ground_lat_deg : float
        Geodetic latitude of the ground station (degrees, −90 to +90).
    ground_lon_deg : float
        Geodetic longitude of the ground station (degrees, −180 to +180).
    ground_alt_m : float
        Altitude of the ground station above the WGS-84 ellipsoid (metres).
    altitude_km : float
        Satellite circular orbit altitude above the Earth's surface (km).
        Used to initialise the SGP4 mean motion.
    inclination_deg : float
        Orbital inclination (degrees).
    raan_deg : float
        Right ascension of the ascending node (degrees).
    mean_anomaly_deg : float
        Mean anomaly at epoch (degrees).  Default 0.0.
    eccentricity : float
        Orbital eccentricity.  Default 0.001 (near-circular).
    arg_perigee_deg : float
        Argument of perigee (degrees).  Default 0.0.
    bstar : float
        SGP4 atmospheric drag term.  Default 1e-4 (typical LEO).
    min_elevation_deg : float
        Minimum elevation angle for contact (degrees).  Frames with
        elevation below this threshold have in_view=False.
        Default 10.0°.
    epoch : datetime
        Reference epoch for the orbital elements.  Defaults to
        2026-01-01 00:00:00 UTC if not supplied.
    """

    def __init__(
        self,
        satellite_id:      str,
        ground_lat_deg:    float,
        ground_lon_deg:    float,
        ground_alt_m:      float     = 0.0,
        altitude_km:       float     = 550.0,
        inclination_deg:   float     = 51.6,
        raan_deg:          float     = 0.0,
        mean_anomaly_deg:  float     = 0.0,
        eccentricity:      float     = 0.001,
        arg_perigee_deg:   float     = 0.0,
        bstar:             float     = 1e-4,
        min_elevation_deg: float     = 10.0,
        epoch:             Optional[datetime] = None,
    ) -> None:
        self.satellite_id      = satellite_id
        self.ground_lat_deg    = ground_lat_deg
        self.ground_lon_deg    = ground_lon_deg
        self.ground_alt_m      = ground_alt_m
        self.min_elevation_deg = min_elevation_deg

        if epoch is None:
            epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)

        # Semi-major axis from circular altitude
        a_m  = EARTH_RADIUS_M + altitude_km * 1_000.0
        self.altitude_km = altitude_km
        self._a_m        = a_m
        # Mean motion in radians/minute (SGP4 TLE convention)
        n_rad_min = math.sqrt(EARTH_MU / a_m**3) * 60.0

        # Build Satrec from orbital elements.
        # sgp4 C extension requires positional arguments (no kwargs).
        # Argument order: whichconst, opsmode, satnum, epoch,
        #   bstar, ndot, nddot, ecco, argpo, inclo, mo, no_kozai, nodeo
        epoch_days = (
            epoch - datetime(1949, 12, 31, tzinfo=timezone.utc)
        ).total_seconds() / 86400.0

        self._sat = Satrec()
        self._sat.sgp4init(
            2,                              # whichconst  — WGS-84
            'i',                            # opsmode     — improved
            1,                              # satnum
            epoch_days,                     # epoch (days since 1949-12-31)
            bstar,                          # bstar
            0.0,                            # ndot
            0.0,                            # nddot
            eccentricity,                   # ecco
            arg_perigee_deg  * DEG2RAD,     # argpo
            inclination_deg  * DEG2RAD,     # inclo
            mean_anomaly_deg * DEG2RAD,     # mo
            n_rad_min,                      # no_kozai (rad/min)
            raan_deg         * DEG2RAD,     # nodeo
        )

        # Pre-compute ground station ECEF (static — station does not move)
        self._gs_ecef = self._geodetic_to_ecef(
            ground_lat_deg, ground_lon_deg, ground_alt_m
        )

    # ── public API ──────────────────────────────────────────────────────────

    def export(
        self,
        start_epoch: datetime,
        duration_s:  float  = 480.0,
        step_s:      float  = 1.0,
    ) -> List[OrbitalFrame]:
        """
        Export a sequence of OrbitalFrame records.

        Parameters
        ----------
        start_epoch : datetime
            UTC start time of the export window (timezone-aware).
        duration_s : float
            Length of the export window in seconds.  Default 480 s
            (~one full LEO contact window).
        step_s : float
            Time step between frames in seconds.  Default 1 s.

        Returns
        -------
        List[OrbitalFrame]
            One frame per time step.  The list includes frames with
            in_view=False so callers can see the full orbital arc.
        """
        if start_epoch.tzinfo is None:
            start_epoch = start_epoch.replace(tzinfo=timezone.utc)

        frames: List[OrbitalFrame] = []
        t = start_epoch
        end = start_epoch + timedelta(seconds=duration_s)

        prev_range_m: Optional[float] = None
        prev_time_s:  Optional[float] = None

        while t <= end:
            jd, fr = jday(
                t.year, t.month, t.day,
                t.hour, t.minute, t.second + t.microsecond * 1e-6,
            )
            err, pos_km, vel_kms = self._sat.sgp4(jd, fr)
            if err != 0:
                t += timedelta(seconds=step_s)
                continue

            # Convert satellite ECI → ECEF
            gmst = self._gmst(jd + fr)
            sat_ecef = self._eci_to_ecef(pos_km, gmst)

            # Compute range, elevation, azimuth
            range_m, el_deg, az_deg = self._look_angles(sat_ecef)

            # Numerical range rate: finite difference when previous
            # frame is available; zero on the first step
            if prev_range_m is not None and prev_time_s is not None:
                dt = (t - start_epoch).total_seconds() - prev_time_s
                range_rate_ms = (range_m - prev_range_m) / dt if dt > 0 else 0.0
            else:
                range_rate_ms = 0.0

            frames.append(OrbitalFrame(
                timestamp     = t,
                satellite_id  = self.satellite_id,
                range_m       = range_m,
                range_rate_ms = range_rate_ms,
                elevation_deg = el_deg,
                azimuth_deg   = az_deg,
                in_view       = el_deg >= self.min_elevation_deg,
            ))

            prev_range_m = range_m
            prev_time_s  = (t - start_epoch).total_seconds()
            t += timedelta(seconds=step_s)

        return frames

    def export_in_view_only(
        self,
        start_epoch: datetime,
        duration_s:  float = 480.0,
        step_s:      float = 1.0,
    ) -> List[OrbitalFrame]:
        """
        Convenience wrapper — returns only frames where in_view=True.
        Useful for the channel emulator, which only needs frames during
        which the satellite is above the minimum elevation threshold.
        """
        return [
            f for f in self.export(start_epoch, duration_s, step_s)
            if f.in_view
        ]

    # ── internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _geodetic_to_ecef(
        lat_deg: float,
        lon_deg: float,
        alt_m:   float,
    ) -> tuple[float, float, float]:
        """
        Convert geodetic (WGS-84) coordinates to ECEF (metres).
        Montenbruck & Gill (2000), Eq. 5.83–5.85.
        """
        lat = lat_deg * DEG2RAD
        lon = lon_deg * DEG2RAD
        # WGS-84 flattening
        f   = 1.0 / 298.257_223_563
        e2  = 2 * f - f * f
        N   = EARTH_RADIUS_M / math.sqrt(1 - e2 * math.sin(lat)**2)
        x   = (N + alt_m) * math.cos(lat) * math.cos(lon)
        y   = (N + alt_m) * math.cos(lat) * math.sin(lon)
        z   = (N * (1 - e2) + alt_m) * math.sin(lat)
        return x, y, z

    @staticmethod
    def _gmst(jd_ut1: float) -> float:
        """
        Greenwich Mean Sidereal Time (radians) from Julian Date.
        Vallado (2013), Algorithm 15.
        """
        T  = (jd_ut1 - 2_451_545.0) / 36_525.0
        θ  = (67_310.548_41 +
               (876_600.0 * 3600.0 + 8_640_184.812_866) * T +
               0.093_104 * T**2 -
               6.2e-6    * T**3)
        return math.fmod(θ * DEG2RAD / 240.0, 2 * math.pi)

    @staticmethod
    def _eci_to_ecef(
        pos_km: tuple[float, float, float],
        gmst:   float,
    ) -> tuple[float, float, float]:
        """Rotate ECI position vector (km) to ECEF (m)."""
        x_km, y_km, z_km = pos_km
        cos_g = math.cos(gmst)
        sin_g = math.sin(gmst)
        x_m = (x_km * cos_g + y_km * sin_g) * 1_000.0
        y_m = (-x_km * sin_g + y_km * cos_g) * 1_000.0
        z_m = z_km * 1_000.0
        return x_m, y_m, z_m

    def _look_angles(
        self,
        sat_ecef: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        """
        Compute slant range (m), elevation (deg), and azimuth (deg)
        from the ground station to the satellite.

        Montenbruck & Gill (2000), Section 5.4.
        """
        gx, gy, gz   = self._gs_ecef
        sx, sy, sz   = sat_ecef

        # Range vector in ECEF
        dx, dy, dz = sx - gx, sy - gy, sz - gz
        range_m = math.sqrt(dx*dx + dy*dy + dz*dz)

        # Ground station geodetic → unit vectors in local ENU frame
        lat = self.ground_lat_deg * DEG2RAD
        lon = self.ground_lon_deg * DEG2RAD

        sin_lat, cos_lat = math.sin(lat), math.cos(lat)
        sin_lon, cos_lon = math.sin(lon), math.cos(lon)

        # ENU components of the range vector
        e =  -sin_lon * dx + cos_lon * dy
        n =  -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
        u =   cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz

        # Elevation and azimuth
        el_deg = math.asin(u / range_m) * RAD2DEG
        az_deg = math.atan2(e, n)       * RAD2DEG
        if az_deg < 0:
            az_deg += 360.0

        return range_m, el_deg, az_deg

