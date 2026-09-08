import math
from datetime import datetime, timezone

from sgp4.api import Satrec, WGS84, jday as sgp4_jday


class OrbitModel:
    """
    SGP4-based orbit propagation for a LEO satellite.

    Default orbital parameters match a 550 km, 51.6° inclination orbit
    (same family as the ISS).  Eclipse detection uses a cylindrical shadow
    model: the satellite is in Earth's shadow when it lies inside the
    cylinder cast by the Sun behind the Earth.

    Usage
    ─────
        orbit = OrbitModel()
        lat, lon, alt, in_eclipse = orbit.propagate()

    For deterministic testing pass a specific UTC datetime to _propagate_at().
    """

    EARTH_RADIUS_KM = 6371.0
    _GM_KM3_S2      = 398600.4418

    # Simulation epoch — the TLE reference time.  Using a fixed date keeps the
    # satellite's position in its orbit consistent across simulator restarts
    # that happen within a few days of each other.
    _EPOCH = (2026, 8, 24)   # year, month, day  (00:00:00 UTC)

    def __init__(
        self,
        altitude_km:       float = 550.0,
        inclination_deg:   float = 51.6,
        raan_deg:          float = 45.0,
        mean_anomaly_deg:  float = 0.0,
    ):
        self._altitude_km    = altitude_km
        self._inclination_deg = inclination_deg
        self._sat = self._build_satellite(raan_deg, mean_anomaly_deg)

    # ── public API ────────────────────────────────────────────────────────────

    def propagate(self):
        """
        Propagate to the current UTC time.
        Returns (latitude_deg, longitude_deg, altitude_km, in_eclipse).
        """
        return self._propagate_at(datetime.now(timezone.utc))

    # ── internal helpers ──────────────────────────────────────────────────────

    def _propagate_at(self, utc: datetime):
        """Propagate to a specific UTC datetime (used by tests)."""
        jd, fr = sgp4_jday(
            utc.year, utc.month, utc.day,
            utc.hour, utc.minute,
            utc.second + utc.microsecond / 1e6,
        )
        err, pos, _ = self._sat.sgp4(jd, fr)
        if err != 0:
            raise RuntimeError(f"SGP4 propagation error {err} at {utc.isoformat()}")

        lat, lon, alt = self._eci_to_geodetic(pos, utc)
        ecl           = self._in_eclipse(pos, utc)
        return lat, lon, alt, ecl

    def _build_satellite(self, raan_deg: float, mean_anomaly_deg: float) -> Satrec:
        """Initialise an SGP4 Satrec from orbital elements."""
        a         = self.EARTH_RADIUS_KM + self._altitude_km
        n_rad_min = math.sqrt(self._GM_KM3_S2 / a ** 3) * 60.0   # mean motion (rad/min)

        yr, mo, dy = self._EPOCH
        jd_e, fr_e  = sgp4_jday(yr, mo, dy, 0, 0, 0)
        epoch_days  = (jd_e + fr_e) - 2433281.5   # days since 1949-12-31 00:00 UT

        sat = Satrec()
        sat.sgp4init(
            WGS84, 'i',
            99001,                          # satellite catalogue number (synthetic)
            epoch_days,                     # epoch
            1.0e-4,                         # bstar (drag term)
            0.0,                            # ndot  (first deriv of mean motion)
            0.0,                            # nddot (second deriv of mean motion)
            0.001,                          # eccentricity  (near-circular)
            math.radians(90.0),             # argpo — argument of perigee
            math.radians(self._inclination_deg),
            math.radians(mean_anomaly_deg),
            n_rad_min,                      # no_kozai — mean motion (rad/min)
            math.radians(raan_deg),         # nodeo — RAAN
        )
        return sat

    # ── coordinate transforms ─────────────────────────────────────────────────

    def _eci_to_geodetic(self, pos, utc: datetime):
        """Convert ECI position (km) to geodetic (lat_deg, lon_deg, alt_km)."""
        x, y, z = pos
        theta   = self._gst_rad(utc)                   # Greenwich Sidereal Time

        # ECI → ECEF  (rotate about Z axis by -θ)
        xe =  x * math.cos(theta) + y * math.sin(theta)
        ye = -x * math.sin(theta) + y * math.cos(theta)
        # ze = z  (no change)

        r      = math.sqrt(xe ** 2 + ye ** 2 + z ** 2)
        lat    = math.degrees(math.asin(z / r))          # spherical approx — fine for sim
        lon    = math.degrees(math.atan2(ye, xe))         # [-180, +180]
        alt_km = r - self.EARTH_RADIUS_KM
        return lat, lon, alt_km

    @staticmethod
    def _gst_rad(utc: datetime) -> float:
        """Approximate Greenwich Sidereal Time in radians."""
        jd, fr = sgp4_jday(utc.year, utc.month, utc.day,
                           utc.hour, utc.minute, utc.second)
        T   = (jd + fr - 2451545.0) / 36525.0
        gst = (
            280.46061837
            + 360.98564736629 * (jd + fr - 2451545.0)
            + 0.000387933 * T ** 2
        )
        return math.radians(gst % 360)

    @staticmethod
    def _sun_eci_km(utc: datetime):
        """
        Low-precision Sun position in ECI (km).
        Vallado algorithm — accurate to ~1°, sufficient for eclipse detection.
        """
        jd, fr = sgp4_jday(utc.year, utc.month, utc.day,
                           utc.hour, utc.minute, utc.second)
        T     = (jd + fr - 2451545.0) / 36525.0
        L0    = 280.46646 + 36000.76983 * T
        M     = math.radians(357.52911 + 35999.05029 * T)
        C     = (
            (1.914602 - 0.004817 * T) * math.sin(M)
            + 0.019993 * math.sin(2 * M)
            + 0.000289 * math.sin(3 * M)
        )
        lon_s = math.radians((L0 + C) % 360)
        R_km  = 1.000001018 * (1 - 0.01671022 * math.cos(M)) * 149_597_870.7
        eps   = math.radians(23.439291111 - 0.013004167 * T)

        return (
            R_km * math.cos(lon_s),
            R_km * math.cos(eps) * math.sin(lon_s),
            R_km * math.sin(eps) * math.sin(lon_s),
        )

    def _in_eclipse(self, pos_eci, utc: datetime) -> bool:
        """
        Cylindrical shadow model.

        The satellite is in eclipse if it lies within Earth's cylindrical shadow
        (cast by the distant Sun):
          1. Project satellite position onto the Earth→Sun unit vector.
          2. If projection > 0, satellite is on the sunlit hemisphere — illuminated.
          3. Otherwise, check perpendicular distance from the shadow axis.
             Eclipse if perp_distance < R_earth.
        """
        sx, sy, sz = self._sun_eci_km(utc)
        sd         = math.sqrt(sx ** 2 + sy ** 2 + sz ** 2)
        hx, hy, hz = sx / sd, sy / sd, sz / sd          # unit vec Earth → Sun

        dot = pos_eci[0] * hx + pos_eci[1] * hy + pos_eci[2] * hz

        if dot > 0:          # satellite on the Sun side → always illuminated
            return False

        perp_sq = sum(p ** 2 for p in pos_eci) - dot ** 2
        return perp_sq < self.EARTH_RADIUS_KM ** 2
