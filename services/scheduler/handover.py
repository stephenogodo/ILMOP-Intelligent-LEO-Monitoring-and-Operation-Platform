"""
services/scheduler/handover.py

Layer 4 — Handover State Machine.

Given a satellite's ordered list of scheduled contact windows (from
Layer 2), this module determines the control-authority state at every
moment in the planning horizon and generates a HandoverPlan describing
every transition between ground stations.

State model
-----------
IDLE          — satellite has no active or imminent ground contact.
ACQUIRING     — AOS has occurred; link is being established.
ACTIVE        — satellite is under full control of the current station.
HANDING_OVER  — two stations overlap; control is being transferred.
RELEASED      — LOS has occurred; station has relinquished control.

Transitions
-----------
                     AOS
  IDLE ──────────────────────────► ACQUIRING
  ACQUIRING ──(link up)──────────► ACTIVE
  ACTIVE ──(next AOS, overlap)───► HANDING_OVER
  HANDING_OVER ──(old LOS)───────► ACTIVE  (now at new station)
  ACTIVE ──(LOS, no successor)───► RELEASED
  RELEASED ──────────────────────► IDLE

Depends on Layer 2: services.scheduler.contact_window (ContactWindow)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from services.scheduler.contact_window import ContactWindow
from services.scheduler.geometry import GroundStation


# ---------------------------------------------------------------------------
# State enumeration
# ---------------------------------------------------------------------------

class HandoverState(str, Enum):
    IDLE         = "IDLE"
    ACQUIRING    = "ACQUIRING"
    ACTIVE       = "ACTIVE"
    HANDING_OVER = "HANDING_OVER"
    RELEASED     = "RELEASED"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class HandoverEvent:
    """
    One state transition for a satellite at a specific instant.

    Attributes
    ----------
    satellite_id     : the satellite this event belongs to
    state            : the HandoverState entered at epoch_unix
    epoch_unix       : Unix timestamp when this state begins
    from_station     : station relinquishing control (None for IDLE/ACQUIRING
                       at the start of the first window)
    to_station       : station taking control (None for RELEASED/IDLE)
    window_aos_unix  : AOS of the window associated with to_station
                       (None when transitioning away from all contact)
    window_los_unix  : LOS of the window associated with from_station
                       (None when no prior window exists)
    """
    satellite_id:    str
    state:           HandoverState
    epoch_unix:      float
    from_station:    GroundStation | None = None
    to_station:      GroundStation | None = None
    window_aos_unix: float | None = None
    window_los_unix: float | None = None


@dataclass
class HandoverPlan:
    """
    The complete ordered sequence of state transitions for one satellite
    over a planning horizon.

    Attributes
    ----------
    satellite_id     : the satellite this plan covers
    events           : HandoverEvent list in chronological order
    contact_windows  : the scheduled windows that were used as input
    overlap_margin_s : the minimum overlap duration (seconds) that
                       triggers a HANDING_OVER state rather than a gap
    """
    satellite_id:    str
    events:          list[HandoverEvent]
    contact_windows: list[ContactWindow]
    overlap_margin_s: float


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_handover_plan(
    satellite_id:     str,
    scheduled_windows: list[ContactWindow],
    overlap_margin_s: float = 30.0,
) -> HandoverPlan:
    """
    Build a HandoverPlan for one satellite from its scheduled contact windows.

    Parameters
    ----------
    satellite_id      : identifier of the satellite
    scheduled_windows : ContactWindow objects for this satellite, already
                        filtered to scheduled=True and sorted by AOS.
                        Windows for OTHER satellites are silently ignored.
    overlap_margin_s  : minimum overlap duration (s) between consecutive
                        windows that triggers a HANDING_OVER transition.
                        Overlaps shorter than this are treated as gaps.

    Returns
    -------
    HandoverPlan with events in chronological order.

    Notes
    -----
    The function accepts the full ScheduleResult.scheduled_windows list
    and filters by satellite_id internally, so callers do not need to
    pre-filter.
    """
    # Filter and sort windows for this satellite
    sat_windows = sorted(
        [w for w in scheduled_windows if w.satellite_id == satellite_id],
        key=lambda w: w.aos_unix,
    )

    events: list[HandoverEvent] = []

    for idx, window in enumerate(sat_windows):
        prev_window = sat_windows[idx - 1] if idx > 0 else None
        next_window = sat_windows[idx + 1] if idx < len(sat_windows) - 1 else None

        # ── Entering this window ─────────────────────────────────────────────

        if prev_window is None:
            # First window: start from IDLE then ACQUIRING
            events.append(HandoverEvent(
                satellite_id    = satellite_id,
                state           = HandoverState.IDLE,
                epoch_unix      = window.aos_unix,
                from_station    = None,
                to_station      = None,
            ))
            events.append(HandoverEvent(
                satellite_id    = satellite_id,
                state           = HandoverState.ACQUIRING,
                epoch_unix      = window.aos_unix,
                from_station    = None,
                to_station      = window.station,
                window_aos_unix = window.aos_unix,
            ))
            events.append(HandoverEvent(
                satellite_id    = satellite_id,
                state           = HandoverState.ACTIVE,
                epoch_unix      = window.aos_unix,
                from_station    = None,
                to_station      = window.station,
                window_aos_unix = window.aos_unix,
            ))

        else:
            overlap_s = prev_window.los_unix - window.aos_unix

            if overlap_s >= overlap_margin_s:
                # Overlapping windows — HANDING_OVER transition
                events.append(HandoverEvent(
                    satellite_id    = satellite_id,
                    state           = HandoverState.HANDING_OVER,
                    epoch_unix      = window.aos_unix,
                    from_station    = prev_window.station,
                    to_station      = window.station,
                    window_aos_unix = window.aos_unix,
                    window_los_unix = prev_window.los_unix,
                ))
                # ACTIVE at new station once old window closes
                events.append(HandoverEvent(
                    satellite_id    = satellite_id,
                    state           = HandoverState.ACTIVE,
                    epoch_unix      = prev_window.los_unix,
                    from_station    = prev_window.station,
                    to_station      = window.station,
                    window_aos_unix = window.aos_unix,
                ))
            else:
                # Gap between windows — RELEASED then IDLE then ACQUIRING
                events.append(HandoverEvent(
                    satellite_id    = satellite_id,
                    state           = HandoverState.RELEASED,
                    epoch_unix      = prev_window.los_unix,
                    from_station    = prev_window.station,
                    to_station      = None,
                    window_los_unix = prev_window.los_unix,
                ))
                events.append(HandoverEvent(
                    satellite_id    = satellite_id,
                    state           = HandoverState.IDLE,
                    epoch_unix      = prev_window.los_unix,
                    from_station    = None,
                    to_station      = None,
                ))
                events.append(HandoverEvent(
                    satellite_id    = satellite_id,
                    state           = HandoverState.ACQUIRING,
                    epoch_unix      = window.aos_unix,
                    from_station    = None,
                    to_station      = window.station,
                    window_aos_unix = window.aos_unix,
                ))
                events.append(HandoverEvent(
                    satellite_id    = satellite_id,
                    state           = HandoverState.ACTIVE,
                    epoch_unix      = window.aos_unix,
                    from_station    = None,
                    to_station      = window.station,
                    window_aos_unix = window.aos_unix,
                ))

        # ── Leaving this window ──────────────────────────────────────────────

        if next_window is None:
            # Last window: end with RELEASED then IDLE
            events.append(HandoverEvent(
                satellite_id    = satellite_id,
                state           = HandoverState.RELEASED,
                epoch_unix      = window.los_unix,
                from_station    = window.station,
                to_station      = None,
                window_los_unix = window.los_unix,
            ))
            events.append(HandoverEvent(
                satellite_id    = satellite_id,
                state           = HandoverState.IDLE,
                epoch_unix      = window.los_unix,
                from_station    = None,
                to_station      = None,
            ))

    # If there were no windows at all, return an empty plan
    return HandoverPlan(
        satellite_id     = satellite_id,
        events           = events,
        contact_windows  = sat_windows,
        overlap_margin_s = overlap_margin_s,
    )


# ---------------------------------------------------------------------------
# Query helper
# ---------------------------------------------------------------------------

def get_active_station(
    plan:       HandoverPlan,
    epoch_unix: float,
) -> GroundStation | None:
    """
    Return the ground station currently holding control authority at
    a given epoch, or None if the satellite is IDLE/RELEASED.

    Walks the event list to find the most recent event at or before
    epoch_unix, then returns the controlling station for that state.

    Parameters
    ----------
    plan       : HandoverPlan produced by build_handover_plan
    epoch_unix : query time, Unix timestamp

    Returns
    -------
    GroundStation that holds control, or None.
    """
    active_station: GroundStation | None = None

    for event in plan.events:
        if event.epoch_unix > epoch_unix:
            break
        if event.state in (HandoverState.ACTIVE, HandoverState.ACQUIRING):
            active_station = event.to_station
        elif event.state == HandoverState.HANDING_OVER:
            # During handover both stations are involved;
            # report the incoming station as the authority.
            active_station = event.to_station
        elif event.state in (HandoverState.RELEASED, HandoverState.IDLE):
            active_station = None

    return active_station


# ---------------------------------------------------------------------------
# Convenience: multi-satellite plans
# ---------------------------------------------------------------------------

def build_constellation_handover_plans(
    satellite_ids:     list[str],
    scheduled_windows: list[ContactWindow],
    overlap_margin_s:  float = 30.0,
) -> dict[str, HandoverPlan]:
    """
    Build a HandoverPlan for every satellite in the constellation.

    Parameters
    ----------
    satellite_ids     : list of all satellite IDs
    scheduled_windows : all scheduled ContactWindow objects
    overlap_margin_s  : handover overlap threshold in seconds

    Returns
    -------
    Dict mapping satellite_id → HandoverPlan.
    """
    return {
        sat_id: build_handover_plan(sat_id, scheduled_windows, overlap_margin_s)
        for sat_id in satellite_ids
    }
