"""
services/dashboard/panel_data.py

S7-15 — data layer for the operator-dashboard handover, dark-satellite and
relay panels.

This module is free of Streamlit so it can be unit-tested. It wires the
Sprint 7 scheduler layers together for a dashboard scenario:

    SGP4 ECI positions (OrbitModel)          -> propagator for the scheduler
    build_contact_schedule  (contact_window) -> scheduled windows
    build_handover_plan     (handover)       -> per-satellite handover state
    find_dark_satellites    (dark_satellite) -> dark list + dark intervals
    compute_isl_topology    (isl_visibility) -> ISL graph, per-class ranges
    coordinate_relay        (relay_coordinator) -> relay-and-forward sessions

Assumptions (also shown on the dashboard):
  * Satellite data queues are modelled, not measured: the queue of a satellite
    grows at QUEUE_GROWTH_MB_S since its last scheduled ground contact, capped
    at QUEUE_CAP_MB. Data age is the time since that contact (or the assumed
    INITIAL_DATA_AGE_S before the horizon start if it had none).
  * The ILP is used when PuLP can solve it; otherwise the greedy fallback runs
    and the snapshot says so in `solver_status`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import jday

from services.satellite_simulator.orbit import OrbitModel
from services.scheduler.contact_window import (
    ContactWindow,
    SatelliteState,
    build_contact_schedule,
)
from services.scheduler.dark_satellite import (
    compute_dark_intervals,
    find_dark_satellites,
)
from services.scheduler.geometry import GroundStation
from services.scheduler.handover import (
    HandoverPlan,
    HandoverState,
    build_handover_plan,
    get_active_station,
)
from services.scheduler.isl_visibility import ISLTopology, compute_isl_topology
from services.scheduler.window_metadata import EnrichedPropagator
from services.scheduler.relay_coordinator import (
    MAX_RELAY_HOPS,
    RelaySession,
    coordinate_relay,
)

#: A scenario shell is treated as HEO when its eccentricity exceeds this.
HEO_ECCENTRICITY_MIN = 0.1

#: Modelled data generation rate while a satellite is out of ground contact.
QUEUE_GROWTH_MB_S = 0.02
#: Cap on the modelled queue (MB).
QUEUE_CAP_MB = 600.0

DARK_HORIZON_S = 600.0

#: Data age assumed for every satellite at the start of the schedule horizon
#: (the earlier contact history is not modelled).
INITIAL_DATA_AGE_S = 1800.0


# ---------------------------------------------------------------------------
# Result structures
# ---------------------------------------------------------------------------

@dataclass
class HandoverRow:
    satellite_id:   str
    orbit_class:    str
    state:          str            # HandoverState value
    station:        str | None     # station holding control now
    next_event_s:   float | None   # seconds until the next transition
    next_station:   str | None


@dataclass
class DarkRow:
    satellite_id:     str
    orbit_class:      str
    is_dark:          bool
    next_contact_s:   float | None   # seconds until the next scheduled AOS
    dark_fraction:    float          # share of the horizon spent dark
    data_age_s:       float
    queue_mb:         float


@dataclass
class RelayRow:
    contact_satellite: str
    station:           str
    window_start_unix: float
    window_active:     bool
    session:           RelaySession
    isl_links_visible: int


@dataclass
class PanelSnapshot:
    now_unix:        float
    horizon_start:   float
    horizon_end:     float
    solver_status:   str
    n_windows:       int
    n_scheduled:     int
    handover:        list[HandoverRow] = field(default_factory=list)
    dark:            list[DarkRow]     = field(default_factory=list)
    relay:           list[RelayRow]    = field(default_factory=list)
    isl_topology:    ISLTopology | None = None
    orbit_classes:   dict[str, str]    = field(default_factory=dict)

    @property
    def dark_ids(self) -> list[str]:
        return [r.satellite_id for r in self.dark if r.is_dark]


# ---------------------------------------------------------------------------
# Orbit helpers
# ---------------------------------------------------------------------------

def shell_orbit_class(shell: dict) -> str:
    """'HEO' for a highly eccentric shell, otherwise 'LEO'."""
    return 'HEO' if shell.get('eccentricity', 0.0) > HEO_ECCENTRICITY_MIN else 'LEO'


def build_orbit_models(scenario_cfg: dict, slots: list) -> tuple[dict, dict]:
    """
    Build one OrbitModel per satellite slot.

    `slots` is the (sid, raan, ma, shell) list from
    fleet_dashboard.satellite_slots. Returns (models, orbit_classes).
    """
    models, classes = {}, {}
    for sid, raan, ma, shell in slots:
        cls = shell_orbit_class(shell)
        # For a HEO shell, 'altitude_km' in the dashboard config is the legacy
        # mean altitude; the perigee/apogee are taken from the shell if given.
        perigee = shell.get('perigee_altitude_km', shell['altitude_km'])
        apogee  = shell.get('apogee_altitude_km')
        if cls == 'HEO' and apogee is None:
            # Dashboard Scenario 4 only lists e and the mean altitude; derive
            # the apogee/perigee pair from the Molniya definition.
            apogee, perigee = 39750.0, 500.0
        models[sid] = OrbitModel(
            altitude_km        = perigee,
            inclination_deg    = shell['inclination_deg'],
            raan_deg           = raan,
            mean_anomaly_deg   = ma,
            arg_perigee_deg    = shell.get('arg_perigee', 90.0),
            apogee_altitude_km = apogee if cls == 'HEO' else None,
        )
        classes[sid] = cls
    return models, classes


def eci_state(model: OrbitModel, unix: float):
    """(position_km, velocity_km_s) in ECI at a Unix time."""
    t = datetime.fromtimestamp(unix, tz=timezone.utc)
    jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                  t.second + t.microsecond / 1e6)
    err, pos, vel = model._sat.sgp4(jd, fr)
    if err != 0:
        raise RuntimeError(f'SGP4 error {err}')
    return np.array(pos), np.array(vel)


def make_propagator(models: dict):
    """Propagator callable(sat_id, unix) -> SatelliteState for the scheduler."""
    def propagator(sat_id: str, unix: float) -> SatelliteState:
        pos, vel = eci_state(models[sat_id], unix)
        return SatelliteState(sat_id, pos, vel)
    return propagator


def make_stations(ground_stations: dict) -> list[GroundStation]:
    return [GroundStation(name, g['lat'], g['lon'], g['alt'])
            for name, g in ground_stations.items()]


# ---------------------------------------------------------------------------
# Handover state at an instant
# ---------------------------------------------------------------------------

def handover_state_at(plan: HandoverPlan, epoch_unix: float):
    """
    (state, station, next_event) of a plan at an epoch.

    state is IDLE before the first event; station is the controlling station
    (None when IDLE/RELEASED); next_event is the first later HandoverEvent.
    """
    state, current = HandoverState.IDLE, None
    for ev in plan.events:
        if ev.epoch_unix > epoch_unix:
            return state, get_active_station(plan, epoch_unix), ev
        state = ev.state
        current = ev
    return state, get_active_station(plan, epoch_unix), None


# ---------------------------------------------------------------------------
# Data age / queue model
# ---------------------------------------------------------------------------

def data_age_s(sat_id: str, now_unix: float, horizon_start: float,
               scheduled: list[ContactWindow]) -> float:
    """Seconds since the last scheduled contact ended (INITIAL_DATA_AGE_S before the horizon if none)."""
    last = horizon_start - INITIAL_DATA_AGE_S
    for w in scheduled:
        if w.satellite_id == sat_id:
            if w.aos_unix <= now_unix < w.los_unix:
                return 0.0          # in contact right now
            if w.los_unix <= now_unix:
                last = max(last, w.los_unix)
    return max(0.0, now_unix - last)


def queue_mb(age_s: float) -> float:
    return min(QUEUE_CAP_MB, age_s * QUEUE_GROWTH_MB_S)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

@dataclass
class PanelContext:
    """The expensive, slowly changing part: orbit models and the ILP schedule."""
    sat_ids:       list
    models:        dict
    orbit_classes: dict
    stations:      list
    schedule:      object          # ScheduleResult
    horizon_start: float
    horizon_end:   float
    solver_status: str


def horizon_bucket(now_unix: float, bucket_s: float = 3600.0) -> float:
    """Start of the schedule bucket containing now_unix (schedule is reused within it)."""
    return math.floor(now_unix / bucket_s) * bucket_s


def build_panel_context(
    scenario_cfg:    dict,
    slots:           list,
    ground_stations: dict,
    horizon_start:   float,
    horizon_h:       float = 6.0,
    step_s:          float = 60.0,
    elevation_mask_deg: float = 5.0,
    use_greedy:      bool = False,
) -> PanelContext:
    """Propagate the fleet and build the contact schedule for one horizon."""
    models, classes = build_orbit_models(scenario_cfg, slots)
    sat_ids  = [s[0] for s in slots]
    stations = make_stations(ground_stations)
    h1 = horizon_start + horizon_h * 3600.0
    # Geometry propagator wrapped so each window carries relay_value and
    # data_age_s (the beta and gamma terms of the composite objective).
    prop = EnrichedPropagator(
        sat_ids, stations, make_propagator(models), horizon_start, h1,
        step_s=step_s, elevation_mask_deg=elevation_mask_deg,
        orbit_classes=classes,
    )

    status = 'Greedy' if use_greedy else None
    try:
        result = build_contact_schedule(
            sat_ids, stations, prop, horizon_start, h1, step_s=step_s,
            elevation_mask_deg=elevation_mask_deg, use_greedy=use_greedy,
        )
        status = status or result.solver_status
    except Exception:                       # PuLP unavailable / ILP failure
        result = build_contact_schedule(
            sat_ids, stations, prop, horizon_start, h1, step_s=step_s,
            elevation_mask_deg=elevation_mask_deg, use_greedy=True,
        )
        status = 'Greedy fallback'
    return PanelContext(sat_ids, models, classes, stations, result,
                        horizon_start, h1, status)


def build_panel_snapshot(
    ctx:      PanelContext,
    now_unix: float,
    max_relay_sessions: int = 3,
) -> PanelSnapshot:
    """Compute everything the three panels display, at `now_unix` (cheap)."""
    sat_ids, models, classes = ctx.sat_ids, ctx.models, ctx.orbit_classes
    h0, h1 = ctx.horizon_start, ctx.horizon_end
    result = ctx.schedule
    sched  = result.scheduled_windows
    snap = PanelSnapshot(
        now_unix=now_unix, horizon_start=h0, horizon_end=h1,
        solver_status=ctx.solver_status, n_windows=len(result.all_windows),
        n_scheduled=len(sched), orbit_classes=classes,
    )

    # ── handover ──────────────────────────────────────────────────────────
    for sid in sat_ids:
        plan = build_handover_plan(sid, sched)
        state, station, nxt = handover_state_at(plan, now_unix)
        snap.handover.append(HandoverRow(
            satellite_id=sid, orbit_class=classes[sid], state=state.value,
            station=station.name if station else None,
            next_event_s=(nxt.epoch_unix - now_unix) if nxt else None,
            next_station=(nxt.to_station.name if nxt and nxt.to_station else None),
        ))

    # ── dark satellites ───────────────────────────────────────────────────
    dark_now = set(find_dark_satellites(sat_ids, now_unix, sched, DARK_HORIZON_S))
    for sid in sat_ids:
        upcoming = [w.aos_unix for w in sched
                    if w.satellite_id == sid and w.los_unix > now_unix]
        nxt = (min(upcoming) - now_unix) if upcoming else None
        dark_s = sum(i.duration_s for i in
                     compute_dark_intervals(sid, sched, h0, h1))
        age = data_age_s(sid, now_unix, h0, sched)
        snap.dark.append(DarkRow(
            satellite_id=sid, orbit_class=classes[sid],
            is_dark=sid in dark_now,
            next_contact_s=max(0.0, nxt) if nxt is not None else None,
            dark_fraction=dark_s / (h1 - h0),
            data_age_s=age, queue_mb=queue_mb(age),
        ))

    # ── relay-and-forward ─────────────────────────────────────────────────
    active = [w for w in sched if w.aos_unix <= now_unix < w.los_unix]
    later  = sorted((w for w in sched if w.aos_unix > now_unix),
                    key=lambda w: w.aos_unix)
    chosen = (active + later)[:max_relay_sessions]
    age_by = {r.satellite_id: r.data_age_s for r in snap.dark}
    q_by   = {r.satellite_id: r.queue_mb for r in snap.dark}

    # Sessions whose windows are open at the same moment must not collect the
    # same dark satellite twice: a satellite serviced by one is removed from the
    # candidates of the next.
    claimed: set[str] = set()

    for w in chosen:
        concurrent = w.aos_unix <= now_unix < w.los_unix
        t = max(now_unix, w.aos_unix)
        positions = {sid: eci_state(models[sid], t)[0] for sid in sat_ids}
        topo = compute_isl_topology(positions, t, orbit_classes=classes)
        sess = coordinate_relay(
            contact_window=w,
            all_satellite_ids=[x for x in sat_ids
                               if not (concurrent and x in claimed)],
            scheduled_windows=sched, isl_topology=topo,
            data_age_fn=lambda s: age_by[s],
            queue_size_fn=lambda s: q_by[s],
            dark_horizon_s=DARK_HORIZON_S, max_hops=MAX_RELAY_HOPS,
        )
        if concurrent:
            claimed.update(sess.dark_serviced)
        snap.relay.append(RelayRow(
            contact_satellite=w.satellite_id, station=w.station.name,
            window_start_unix=w.aos_unix,
            window_active=w.aos_unix <= now_unix < w.los_unix,
            session=sess, isl_links_visible=len(topo.visible_links),
        ))
        if snap.isl_topology is None:
            snap.isl_topology = topo

    if snap.isl_topology is None:
        pos = {sid: eci_state(models[sid], now_unix)[0] for sid in sat_ids}
        snap.isl_topology = compute_isl_topology(pos, now_unix,
                                                 orbit_classes=classes)
    return snap
