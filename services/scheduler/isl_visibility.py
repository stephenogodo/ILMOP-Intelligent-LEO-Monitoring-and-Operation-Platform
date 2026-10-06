"""
services/scheduler/isl_visibility.py

Layer 3 — ISL Visibility Model.

Determines which pairs of satellites have line-of-sight (LOS) at a
given epoch, building a dynamic inter-satellite link (ISL) topology
graph. Two satellites are ISL-visible if the straight line between
them does not pass through the Earth (plus an atmospheric margin).

Depends on: Layer 1 geometry constants (EARTH_RADIUS_KM).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from services.scheduler.geometry import EARTH_RADIUS_KM


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Atmospheric margin above Earth's surface below which ISL signals
# are absorbed. 100 km is a conservative but standard value.
ISL_ATMOSPHERE_MARGIN_KM: float = 100.0

# Effective Earth radius for ISL occlusion checks
ISL_EARTH_RADIUS_KM: float = EARTH_RADIUS_KM + ISL_ATMOSPHERE_MARGIN_KM


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ISLLink:
    """
    One directional ISL link between two satellites.

    Links are stored as ordered pairs (sat_a < sat_b lexicographically)
    so each physical link appears only once in the topology.
    """
    sat_a:       str      # satellite ID, lexicographically smaller
    sat_b:       str      # satellite ID, lexicographically larger
    range_km:    float    # straight-line distance between satellites
    is_visible:  bool     # True if line-of-sight is unobstructed


@dataclass
class ISLTopology:
    """
    The complete ISL visibility graph at one instant in time.

    Attributes
    ----------
    unix_time       : epoch of this snapshot
    links           : all ISLLink objects (visible and occluded)
    visible_links   : subset with is_visible=True
    occluded_links  : subset with is_visible=False
    adjacency       : dict mapping sat_id → set of reachable sat_ids
    """
    unix_time:      float
    links:          list[ISLLink]
    visible_links:  list[ISLLink]  = field(default_factory=list)
    occluded_links: list[ISLLink]  = field(default_factory=list)
    adjacency:      dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self):
        self.visible_links  = [l for l in self.links if     l.is_visible]
        self.occluded_links = [l for l in self.links if not l.is_visible]
        self._build_adjacency()

    def _build_adjacency(self):
        """Build adjacency dict from visible links."""
        self.adjacency = {}
        for link in self.visible_links:
            self.adjacency.setdefault(link.sat_a, set()).add(link.sat_b)
            self.adjacency.setdefault(link.sat_b, set()).add(link.sat_a)

    def reachable_from(self, sat_id: str) -> set[str]:
        """
        Return the set of satellites directly reachable from sat_id
        over a single ISL hop.
        """
        return self.adjacency.get(sat_id, set())

    def is_linked(self, sat_a: str, sat_b: str) -> bool:
        """Return True if sat_a and sat_b have a visible ISL link."""
        key_a = min(sat_a, sat_b)
        key_b = max(sat_a, sat_b)
        return key_b in self.adjacency.get(key_a, set())


# ---------------------------------------------------------------------------
# Core line-of-sight computation
# ---------------------------------------------------------------------------

def has_line_of_sight(
    pos_a_km: np.ndarray,
    pos_b_km: np.ndarray,
    earth_radius_km: float = ISL_EARTH_RADIUS_KM,
) -> tuple[bool, float]:
    """
    Determine whether two satellites have unobstructed line of sight.

    Uses the analytical minimum-distance formula for a line segment
    relative to the origin (Earth's centre).

    The line between A and B is parameterised as:
        p(t) = pos_a + t * (pos_b - pos_a),  t in [0, 1]

    The minimum distance from the origin to this segment occurs at:
        t* = -dot(d, pos_a) / dot(d, d)
    where d = pos_b - pos_a, clamped to [0, 1].

    Parameters
    ----------
    pos_a_km        : ECI position of satellite A, shape (3,), km
    pos_b_km        : ECI position of satellite B, shape (3,), km
    earth_radius_km : occlusion radius (Earth + atmosphere margin), km

    Returns
    -------
    (visible: bool, min_distance_km: float)
        visible         — True if line of sight is clear
        min_distance_km — closest approach of the A-B segment to
                          Earth's centre (useful for margin analysis)
    """
    d = pos_b_km - pos_a_km
    d_dot_d = float(np.dot(d, d))

    if d_dot_d < 1e-10:
        dist = float(np.linalg.norm(pos_a_km))
        return dist > earth_radius_km, dist

    t_star = -float(np.dot(d, pos_a_km)) / d_dot_d
    t_star = max(0.0, min(1.0, t_star))

    closest  = pos_a_km + t_star * d
    min_dist = float(np.linalg.norm(closest))
    visible  = min_dist > earth_radius_km

    return visible, min_dist


# ---------------------------------------------------------------------------
# Topology builder
# ---------------------------------------------------------------------------

def compute_isl_topology(
    satellite_positions: dict[str, np.ndarray],
    unix_time:           float,
    earth_radius_km:     float = ISL_EARTH_RADIUS_KM,
    max_range_km:        float | None = None,
) -> ISLTopology:
    """
    Compute the ISL visibility topology for a constellation snapshot.

    Parameters
    ----------
    satellite_positions : dict mapping satellite_id → ECI position (km)
    unix_time           : epoch of this snapshot, Unix timestamp
    earth_radius_km     : occlusion check radius
    max_range_km        : optional maximum ISL range; pairs beyond this
                          distance are marked occluded even if LOS is
                          geometrically clear. None = no limit.

    Returns
    -------
    ISLTopology — the complete visibility graph at this epoch.
    """
    sat_ids = sorted(satellite_positions.keys())
    links:  list[ISLLink] = []

    for i in range(len(sat_ids)):
        for j in range(i + 1, len(sat_ids)):
            sat_a = sat_ids[i]
            sat_b = sat_ids[j]

            pos_a    = satellite_positions[sat_a]
            pos_b    = satellite_positions[sat_b]
            range_km = float(np.linalg.norm(pos_b - pos_a))

            if max_range_km is not None and range_km > max_range_km:
                visible = False
            else:
                visible, _ = has_line_of_sight(
                    pos_a, pos_b, earth_radius_km
                )

            links.append(ISLLink(
                sat_a      = sat_a,
                sat_b      = sat_b,
                range_km   = range_km,
                is_visible = visible,
            ))

    return ISLTopology(unix_time=unix_time, links=links)


# ---------------------------------------------------------------------------
# Convenience: topology time series
# ---------------------------------------------------------------------------

def compute_isl_topology_series(
    satellite_positions_over_time: list[tuple[float, dict[str, np.ndarray]]],
    earth_radius_km: float = ISL_EARTH_RADIUS_KM,
    max_range_km:    float | None = None,
) -> list[ISLTopology]:
    """
    Compute ISL topology at multiple epochs.

    Parameters
    ----------
    satellite_positions_over_time : list of (unix_time, positions_dict)
    earth_radius_km : occlusion radius
    max_range_km    : optional maximum ISL range

    Returns
    -------
    List of ISLTopology, one per epoch, in time order.
    """
    return [
        compute_isl_topology(positions, unix_time,
                             earth_radius_km, max_range_km)
        for unix_time, positions in satellite_positions_over_time
    ]