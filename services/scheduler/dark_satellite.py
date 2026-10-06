"""
services/scheduler/dark_satellite.py

Layer 5 — Dark Satellite Detection.

A satellite is "dark" at a given epoch when it has no scheduled ground
contact within a configurable lookahead horizon.  Dark satellites are at
risk of command-and-control loss and require either an opportunistic
relay (Layer 6) or an emergency uplink window to be injected into the
schedule.

Public API
----------
is_dark(satellite_id, epoch_unix, scheduled_windows, horizon_s) -> bool
    Point-in-time darkness check for one satellite.

find_dark_satellites(satellite_ids, epoch_unix, scheduled_windows,
                     horizon_s) -> list[str]
    All satellite IDs that are dark at a given epoch.

DarkInterval
    Contiguous period during which a satellite has no scheduled contact.

compute_dark_intervals(satellite_id, scheduled_windows,
                       horizon_start_unix, horizon_end_unix)
    -> list[DarkInterval]
    Full dark-period timeline across a planning horizon.

Depends on Layer 2: services.scheduler.contact_window (ContactWindow)
"""

from __future__ import annotations

from dataclasses import dataclass


from services.scheduler.contact_window import ContactWindow


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DarkInterval:
    """
    A contiguous period during which a satellite has no scheduled contact.

    Attributes
    ----------
    satellite_id : the satellite this interval belongs to
    start_unix   : Unix timestamp when darkness begins
                   (either the planning horizon start or the LOS of the
                   preceding scheduled window)
    end_unix     : Unix timestamp when darkness ends
                   (either the AOS of the next scheduled window or the
                   planning horizon end)
    duration_s   : end_unix − start_unix, in seconds
    """
    satellite_id: str
    start_unix:   float
    end_unix:     float
    duration_s:   float


# ---------------------------------------------------------------------------
# Point-in-time query
# ---------------------------------------------------------------------------

def is_dark(
    satellite_id:      str,
    epoch_unix:        float,
    scheduled_windows: list[ContactWindow],
    horizon_s:         float = 600.0,
) -> bool:
    """
    Return True if the satellite has no scheduled contact within
    [epoch_unix, epoch_unix + horizon_s].

    A satellite is considered "not dark" if ANY scheduled window for it
    overlaps the lookahead window, i.e.:
        window.aos_unix < epoch_unix + horizon_s
        AND
        window.los_unix > epoch_unix

    Parameters
    ----------
    satellite_id      : satellite to check
    epoch_unix        : current time, Unix timestamp
    scheduled_windows : all ContactWindow objects in the plan
    horizon_s         : lookahead window in seconds (default 10 minutes)

    Returns
    -------
    True  → satellite is dark (no contact within horizon)
    False → satellite has at least one contact within horizon
    """
    horizon_end = epoch_unix + horizon_s

    for w in scheduled_windows:
        if w.satellite_id != satellite_id:
            continue
        # Window overlaps [epoch_unix, horizon_end] ?
        if w.aos_unix < horizon_end and w.los_unix > epoch_unix:
            return False   # contact found — not dark

    return True   # no overlapping window found


# ---------------------------------------------------------------------------
# Fleet-level query
# ---------------------------------------------------------------------------

def find_dark_satellites(
    satellite_ids:     list[str],
    epoch_unix:        float,
    scheduled_windows: list[ContactWindow],
    horizon_s:         float = 600.0,
) -> list[str]:
    """
    Return the list of satellite IDs that are dark at epoch_unix.

    Parameters
    ----------
    satellite_ids     : all satellite IDs in the constellation
    epoch_unix        : current time, Unix timestamp
    scheduled_windows : all ContactWindow objects in the plan
    horizon_s         : lookahead window in seconds

    Returns
    -------
    List of satellite IDs (subset of satellite_ids) that are dark.
    The order matches satellite_ids.
    """
    return [
        sat_id
        for sat_id in satellite_ids
        if is_dark(sat_id, epoch_unix, scheduled_windows, horizon_s)
    ]


# ---------------------------------------------------------------------------
# Dark-interval timeline
# ---------------------------------------------------------------------------

def compute_dark_intervals(
    satellite_id:       str,
    scheduled_windows:  list[ContactWindow],
    horizon_start_unix: float,
    horizon_end_unix:   float,
) -> list[DarkInterval]:
    """
    Compute every contiguous dark period for one satellite within a
    planning horizon [horizon_start_unix, horizon_end_unix].

    The algorithm walks the sorted, merged window list and identifies
    the gaps between windows (and any gap at the start or end of the
    horizon).

    Parameters
    ----------
    satellite_id        : satellite to analyse
    scheduled_windows   : all ContactWindow objects in the plan
    horizon_start_unix  : start of the planning horizon (Unix timestamp)
    horizon_end_unix    : end   of the planning horizon (Unix timestamp)

    Returns
    -------
    List of DarkInterval, sorted by start_unix.  Empty if the satellite
    has continuous coverage across the entire horizon.
    """
    if horizon_end_unix <= horizon_start_unix:
        return []

    # Filter and sort windows for this satellite that overlap the horizon
    sat_windows = sorted(
        [
            w for w in scheduled_windows
            if w.satellite_id == satellite_id
            and w.aos_unix < horizon_end_unix
            and w.los_unix  > horizon_start_unix
        ],
        key=lambda w: w.aos_unix,
    )

    # Merge overlapping windows so gaps are unambiguous
    merged: list[tuple[float, float]] = []
    for w in sat_windows:
        aos = max(w.aos_unix, horizon_start_unix)
        los = min(w.los_unix,  horizon_end_unix)
        if merged and aos <= merged[-1][1]:
            # Extend the last merged window
            merged[-1] = (merged[-1][0], max(merged[-1][1], los))
        else:
            merged.append((aos, los))

    # Build dark intervals from the gaps between merged windows
    dark: list[DarkInterval] = []
    cursor = horizon_start_unix

    for (aos, los) in merged:
        if aos > cursor:
            dark.append(DarkInterval(
                satellite_id = satellite_id,
                start_unix   = cursor,
                end_unix     = aos,
                duration_s   = aos - cursor,
            ))
        cursor = max(cursor, los)

    # Trailing dark period after the last window
    if cursor < horizon_end_unix:
        dark.append(DarkInterval(
            satellite_id = satellite_id,
            start_unix   = cursor,
            end_unix     = horizon_end_unix,
            duration_s   = horizon_end_unix - cursor,
        ))

    return dark
