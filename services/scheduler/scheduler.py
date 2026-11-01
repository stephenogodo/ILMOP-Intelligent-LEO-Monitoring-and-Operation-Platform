"""
services/scheduler/scheduler.py

Layer 3 — Contact Scheduler (Runtime Engine).

Translates the static ILP contact schedule (ScheduleResult) produced by
Layer 2 into a live simulation runtime:  on every clock tick it fires
AOS and LOS events, publishes them to Kafka, drives the Layer 4 handover
state machine, and tracks which satellites are dark.

Architecture Decision: ADR-020

Kafka topics
------------
Each satellite gets three topics:

  contact.<sat_id>.aos       — published when a contact window opens
  contact.<sat_id>.los       — published when a contact window closes
  contact.<sat_id>.schedule  — one-shot plan dump at scheduler startup

Payload schemas follow the Pydantic v2 models defined below.

Integration
-----------
  1. Build your ScheduleResult with build_contact_schedule()  (Layer 2).
  2. Build HandoverPlans with build_constellation_handover_plans() (Layer 4).
  3. Instantiate ContactScheduler(result, handover_plans, kafka_producer).
  4. Call scheduler.tick() once per simulation step.
  5. Inspect scheduler.active_windows, scheduler.dark_satellite_ids,
     scheduler.history as needed.

The scheduler is intentionally decoupled from wall-clock time:
pass a clock_fn=lambda: sim_time to drive it from a simulation clock,
or omit it to use datetime.utcnow() for live operations.

Depends on:
  Layer 2: services.scheduler.contact_window  (ContactWindow, ScheduleResult)
  Layer 4: services.scheduler.handover        (HandoverPlan, HandoverState,
                                               get_active_station)
  Layer 5: services.scheduler.dark_satellite  (find_dark_satellites)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from services.scheduler.contact_window import ContactWindow, ScheduleResult
from services.scheduler.dark_satellite import find_dark_satellites
from services.scheduler.handover import (
    HandoverPlan,
    HandoverState,
    get_active_station,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kafka payload schemas (plain dataclasses — no heavy dependency needed)
# ---------------------------------------------------------------------------

@dataclass
class AOSPayload:
    """
    Payload published to  contact.<sat_id>.aos  when a window opens.

    Fields
    ------
    event           : always "AOS"
    satellite_id    : the satellite acquiring the ground station
    station_name    : name of the ground station
    station_lat_deg : station geodetic latitude
    station_lon_deg : station geodetic longitude
    aos_unix        : window AOS, Unix timestamp (float seconds)
    los_unix        : window LOS, Unix timestamp (float seconds)
    duration_s      : planned contact duration in seconds
    max_elevation_deg: peak elevation during this pass
    composite_score : ILP composite objective score for this window
    relay_value     : ISL relay utility at AOS (0–1)
    data_age_s      : seconds since last ground contact at AOS
    handover_state  : current handover state string (e.g. "ACQUIRING")
    ts_iso          : ISO-8601 wall-clock timestamp when this event fired
    """
    event:              str
    satellite_id:       str
    station_name:       str
    station_lat_deg:    float
    station_lon_deg:    float
    aos_unix:           float
    los_unix:           float
    duration_s:         float
    max_elevation_deg:  float
    composite_score:    float
    relay_value:        float
    data_age_s:         float
    handover_state:     str
    ts_iso:             str


@dataclass
class LOSPayload:
    """
    Payload published to  contact.<sat_id>.los  when a window closes.

    Fields
    ------
    event           : always "LOS"
    satellite_id    : the satellite losing the ground station
    station_name    : name of the ground station
    los_unix        : actual LOS, Unix timestamp (float seconds)
    actual_duration_s : wall-clock seconds from AOS to LOS
    handover_state  : current handover state string (e.g. "RELEASED")
    is_dark_now     : True if the satellite enters darkness after this LOS
    ts_iso          : ISO-8601 wall-clock timestamp when this event fired
    """
    event:             str
    satellite_id:      str
    station_name:      str
    los_unix:          float
    actual_duration_s: float
    handover_state:    str
    is_dark_now:       bool
    ts_iso:            str


@dataclass
class SchedulePlanPayload:
    """
    One-shot plan dump published to  contact.<sat_id>.schedule  at startup.

    Contains the full ordered list of scheduled windows for the satellite,
    serialised as a list of dicts.  Consumers (e.g. the fleet dashboard)
    use this to pre-populate the timeline view.
    """
    event:        str          # always "SCHEDULE_PLAN"
    satellite_id: str
    window_count: int
    windows:      list         # each window as a plain dict
    ts_iso:       str


# ---------------------------------------------------------------------------
# Window status tracker
# ---------------------------------------------------------------------------

@dataclass
class ActiveWindow:
    """
    Tracks a contact window that is currently open (between AOS and LOS).

    Attributes
    ----------
    window        : the underlying ContactWindow from the schedule
    opened_at     : simulation clock time when AOS was fired
    """
    window:    ContactWindow
    opened_at: float   # simulation Unix timestamp when AOS fired


# ---------------------------------------------------------------------------
# ContactScheduler
# ---------------------------------------------------------------------------

class ContactScheduler:
    """
    Runtime execution engine for the ILP contact schedule.

    Drives AOS/LOS events from the static ILP schedule on every
    simulation tick, publishes Kafka messages, tracks handover state,
    and monitors dark satellite status.

    Parameters
    ----------
    schedule        : ScheduleResult from build_contact_schedule() (Layer 2)
    handover_plans  : dict mapping satellite_id → HandoverPlan (Layer 4).
                      Pass an empty dict to skip handover state tracking.
    kafka_producer  : object with a .produce(topic: str, value: str) method,
                      or None to disable Kafka output (useful in tests).
    all_satellite_ids: complete list of satellite IDs in the constellation.
                      Defaults to the IDs found in the schedule.
    dark_horizon_s  : lookahead horizon for dark-satellite detection (s).
                      Default 600 s (10 minutes).
    clock_fn        : callable() → float (Unix timestamp).
                      Defaults to  lambda: datetime.utcnow().timestamp().
                      Override for simulation time injection.
    """

    def __init__(
        self,
        schedule:          ScheduleResult,
        handover_plans:    dict,
        kafka_producer:    Any = None,
        all_satellite_ids: list = None,
        dark_horizon_s:    float = 600.0,
        clock_fn:          Callable = None,
    ) -> None:
        self._schedule       = schedule
        self._plans          = handover_plans
        self._producer       = kafka_producer
        self._dark_horizon_s = dark_horizon_s
        self._clock          = clock_fn or (
            lambda: datetime.now(timezone.utc).timestamp()
        )

        # Ordered list of scheduled windows, sorted by AOS
        self._pending: list = sorted(
            schedule.scheduled_windows,
            key=lambda w: w.aos_unix,
        )

        # Satellite IDs — use provided list or derive from schedule
        if all_satellite_ids is not None:
            self._all_sat_ids = list(all_satellite_ids)
        else:
            self._all_sat_ids = sorted({
                w.satellite_id for w in schedule.scheduled_windows
            })

        # Runtime state
        self.active_windows:     dict = {}   # "<sat_id>:<station>" → ActiveWindow
        self.history:            list = []   # completed ContactWindows
        self.dark_satellite_ids: list = []   # currently dark sat IDs

        # Track which windows have been fired to prevent duplicates
        self._fired_aos: set = set()   # id(window)
        self._fired_los: set = set()   # id(window)

        logger.info(
            "ContactScheduler initialised | %d scheduled windows | "
            "%d satellites | dark horizon %.0f s",
            len(self._pending),
            len(self._all_sat_ids),
            dark_horizon_s,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Publish the full schedule plan to Kafka and log startup.

        Call once before the first tick().  Emits one
        contact.<sat_id>.schedule message per satellite so consumers
        can pre-populate their timeline views.

        Safe to call with kafka_producer=None (no-op).
        """
        for sat_id in self._all_sat_ids:
            sat_windows = [
                w for w in self._pending
                if w.satellite_id == sat_id
            ]
            payload = SchedulePlanPayload(
                event        = "SCHEDULE_PLAN",
                satellite_id = sat_id,
                window_count = len(sat_windows),
                windows      = [self._window_to_dict(w) for w in sat_windows],
                ts_iso       = datetime.now(timezone.utc).isoformat(),
            )
            self._publish(
                topic   = f"contact.{sat_id}.schedule",
                payload = payload,
            )

        logger.info("ContactScheduler started — schedule published to Kafka.")

    def tick(self) -> list:
        """
        Advance the scheduler by one simulation step.

        Should be called once per simulation tick (e.g. every simulated
        second, or at whatever granularity the sim loop runs).

        Returns a list of human-readable event strings describing every
        AOS and LOS that fired this tick — useful for logging, tests,
        and dashboard status lines.

        Side effects
        ------------
        * Publishes AOS/LOS Kafka messages.
        * Updates self.active_windows.
        * Appends completed windows to self.history.
        * Updates self.dark_satellite_ids.
        """
        now    = self._clock()
        events = []

        events += self._open_due_windows(now)
        events += self._close_expired_windows(now)

        # Refresh dark-satellite list
        self.dark_satellite_ids = find_dark_satellites(
            satellite_ids     = self._all_sat_ids,
            epoch_unix        = now,
            scheduled_windows = self._schedule.scheduled_windows,
            horizon_s         = self._dark_horizon_s,
        )

        return events

    def window_for(self, sat_id: str, station_name: str):
        """
        Return the currently active window for a satellite/station pair,
        or None if the satellite is not in contact with that station.
        """
        key = self._key(sat_id, station_name)
        return self.active_windows.get(key)

    def is_in_contact(self, sat_id: str) -> bool:
        """Return True if the satellite has any active contact window."""
        return any(
            aw.window.satellite_id == sat_id
            for aw in self.active_windows.values()
        )

    def is_dark(self, sat_id: str) -> bool:
        """Return True if the satellite is currently dark."""
        return sat_id in self.dark_satellite_ids

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_due_windows(self, now: float) -> list:
        """Fire AOS for every window whose AOS time has passed."""
        events = []
        still_pending = []

        for window in self._pending:
            if window.aos_unix <= now and id(window) not in self._fired_aos:
                events += self._fire_aos(window, now)
                self._fired_aos.add(id(window))
            else:
                still_pending.append(window)

        self._pending = still_pending
        return events

    def _close_expired_windows(self, now: float) -> list:
        """Fire LOS for every active window whose LOS time has passed."""
        events = []
        keys_to_close = []

        for key, aw in self.active_windows.items():
            if aw.window.los_unix <= now and id(aw.window) not in self._fired_los:
                keys_to_close.append(key)

        for key in keys_to_close:
            aw = self.active_windows.pop(key)
            events += self._fire_los(aw, now)
            self._fired_los.add(id(aw.window))
            self.history.append(aw.window)

        return events

    def _fire_aos(self, window: ContactWindow, now: float) -> list:
        """Register an open window and publish the AOS Kafka message."""
        key = self._key(window.satellite_id, window.station.name)

        # Determine handover state from Layer 4
        h_state = self._handover_state_at(window.satellite_id, now)

        payload = AOSPayload(
            event             = "AOS",
            satellite_id      = window.satellite_id,
            station_name      = window.station.name,
            station_lat_deg   = window.station.lat_deg,
            station_lon_deg   = window.station.lon_deg,
            aos_unix          = window.aos_unix,
            los_unix          = window.los_unix,
            duration_s        = window.duration_s,
            max_elevation_deg = window.max_elevation_deg,
            composite_score   = window.composite_score,
            relay_value       = window.relay_value,
            data_age_s        = window.data_age_s,
            handover_state    = h_state,
            ts_iso            = datetime.fromtimestamp(
                                    now, tz=timezone.utc
                                ).isoformat(),
        )

        self._publish(
            topic   = f"contact.{window.satellite_id}.aos",
            payload = payload,
        )

        self.active_windows[key] = ActiveWindow(window=window, opened_at=now)

        msg = (
            f"AOS  | {window.satellite_id} -> {window.station.name} "
            f"| el={window.max_elevation_deg:.1f}deg "
            f"| dur={window.duration_s:.0f}s "
            f"| state={h_state}"
        )
        logger.info(msg)
        return [msg]

    def _fire_los(self, aw: ActiveWindow, now: float) -> list:
        """Publish the LOS Kafka message for a closing window."""
        window = aw.window

        # Check darkness after this LOS — exclude the closing window itself
        remaining_scheduled = [
            w for w in self._schedule.scheduled_windows
            if w is not window
        ]
        is_dark_after = find_dark_satellites(
            satellite_ids     = [window.satellite_id],
            epoch_unix        = now,
            scheduled_windows = remaining_scheduled,
            horizon_s         = self._dark_horizon_s,
        )
        dark_now = window.satellite_id in is_dark_after

        h_state = self._handover_state_at(window.satellite_id, now)

        payload = LOSPayload(
            event             = "LOS",
            satellite_id      = window.satellite_id,
            station_name      = window.station.name,
            los_unix          = window.los_unix,
            actual_duration_s = now - aw.opened_at,
            handover_state    = h_state,
            is_dark_now       = dark_now,
            ts_iso            = datetime.fromtimestamp(
                                    now, tz=timezone.utc
                                ).isoformat(),
        )

        self._publish(
            topic   = f"contact.{window.satellite_id}.los",
            payload = payload,
        )

        msg = (
            f"LOS  | {window.satellite_id} <- {window.station.name} "
            f"| actual={now - aw.opened_at:.0f}s "
            f"| dark={'YES' if dark_now else 'no'} "
            f"| state={h_state}"
        )
        logger.info(msg)
        return [msg]

    def _handover_state_at(self, sat_id: str, now: float) -> str:
        """Return the handover state string for a satellite at `now`."""
        plan = self._plans.get(sat_id)
        if plan is None:
            return HandoverState.IDLE.value

        # Walk plan events to find current state
        current_state = HandoverState.IDLE
        for event in plan.events:
            if event.epoch_unix > now:
                break
            current_state = event.state

        return current_state.value

    def _publish(self, topic: str, payload: Any) -> None:
        """Serialise payload to JSON and call the Kafka producer."""
        if self._producer is None:
            return
        try:
            value = json.dumps(asdict(payload))
            self._producer.produce(topic=topic, value=value)
        except Exception as exc:
            logger.warning("Kafka publish failed on %s: %s", topic, exc)

    @staticmethod
    def _key(sat_id: str, station_name: str) -> str:
        return f"{sat_id}:{station_name}"

    @staticmethod
    def _window_to_dict(w: ContactWindow) -> dict:
        return {
            "satellite_id":       w.satellite_id,
            "station_name":       w.station.name,
            "station_lat_deg":    w.station.lat_deg,
            "station_lon_deg":    w.station.lon_deg,
            "aos_unix":           w.aos_unix,
            "los_unix":           w.los_unix,
            "duration_s":         w.duration_s,
            "max_elevation_deg":  w.max_elevation_deg,
            "composite_score":    w.composite_score,
            "relay_value":        w.relay_value,
            "data_age_s":         w.data_age_s,
        }


# ---------------------------------------------------------------------------
# Module-level factory convenience
# ---------------------------------------------------------------------------

def create_scheduler(
    schedule:          ScheduleResult,
    handover_plans:    dict = None,
    kafka_producer:    Any = None,
    all_satellite_ids: list = None,
    dark_horizon_s:    float = 600.0,
    clock_fn:          Callable = None,
) -> ContactScheduler:
    """
    Convenience factory for ContactScheduler.

    Parameters match ContactScheduler.__init__.  handover_plans defaults
    to an empty dict if not provided.

    Example
    -------
    >>> result   = build_contact_schedule(...)
    >>> plans    = build_constellation_handover_plans(sat_ids, result.scheduled_windows)
    >>> sched    = create_scheduler(result, plans, kafka_producer=producer)
    >>> sched.start()
    >>> while sim_running:
    ...     events = sched.tick()
    ...     for e in events:
    ...         print(e)
    """
    return ContactScheduler(
        schedule          = schedule,
        handover_plans    = handover_plans if handover_plans is not None else {},
        kafka_producer    = kafka_producer,
        all_satellite_ids = all_satellite_ids,
        dark_horizon_s    = dark_horizon_s,
        clock_fn          = clock_fn,
    )


# ---------------------------------------------------------------------------
# CLI entry point (dev / smoke test)
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Minimal smoke-test runner.

    Builds a tiny synthetic schedule with two windows and runs the
    scheduler for a few ticks, printing events to stdout.  Useful for
    quick sanity checks without the full simulation stack.
    """
    import time as _time

    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s  %(levelname)-8s  %(message)s",
    )

    from services.scheduler.contact_window import ScheduleResult, ContactWindow
    from services.scheduler.geometry import GroundStation

    # Build a tiny synthetic schedule — two back-to-back windows
    t0 = _time.time()

    stn_a = GroundStation(name="GST-ALPHA", lat_deg=51.5, lon_deg=-0.1, alt_km=0.05)
    stn_b = GroundStation(name="GST-BETA",  lat_deg=35.7, lon_deg=139.7, alt_km=0.05)

    win1 = ContactWindow(
        satellite_id="SAT-A1", station=stn_a,
        aos_unix=t0 + 1.0, los_unix=t0 + 4.0,
        max_elevation_deg=32.5, duration_s=3.0,
        relay_value=0.0, data_age_s=120.0, scheduled=True,
        composite_score=0.72,
    )
    win2 = ContactWindow(
        satellite_id="SAT-A1", station=stn_b,
        aos_unix=t0 + 6.0, los_unix=t0 + 9.0,
        max_elevation_deg=18.0, duration_s=3.0,
        relay_value=0.2, data_age_s=240.0, scheduled=True,
        composite_score=0.55,
    )

    dummy_result = ScheduleResult(
        horizon_start_unix = t0,
        horizon_end_unix   = t0 + 12.0,
        all_windows        = [win1, win2],
        scheduled_windows  = [win1, win2],
        dropped_windows    = [],
        total_contact_s    = 6.0,
        solver_status      = "Synthetic",
    )

    sched = create_scheduler(
        schedule          = dummy_result,
        kafka_producer    = None,      # no Kafka in smoke test
        all_satellite_ids = ["SAT-A1"],
        dark_horizon_s    = 3.0,
    )
    sched.start()

    print("\n-- ContactScheduler smoke test --\n")
    deadline = t0 + 12.0
    while _time.time() < deadline:
        fired = sched.tick()
        for e in fired:
            print(e)
        _time.sleep(0.25)

    print(f"\nCompleted. History: {len(sched.history)} windows | "
          f"Dark now: {sched.dark_satellite_ids}")


if __name__ == "__main__":
    main()
