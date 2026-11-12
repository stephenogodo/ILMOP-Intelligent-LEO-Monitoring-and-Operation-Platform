"""
services/dashboard/pages/fleet_dashboard.py

ILMOP Fleet Dashboard — Sprint 7, Deliverable 6
=================================================

Displays all constellation satellites simultaneously on a world map,
with real-time orbital positions, visibility counts, GDOP statistics,
contact window scheduling, and a fading ground-track trail.

Four constellation scenarios are selectable:

    Scenario 1 — 1 satellite,  1 plane,   550 km, 51.6°
    Scenario 2 — 6 satellites, 1 plane,   550 km, 53.0°
    Scenario 3 — 24 satellites, 4 planes, 550 km, 53.0°
    Scenario 4 — 6 Molniya HEO (1 per plane, 63.4°, e=0.74) + 12 LEO (2 planes × 6, 550 km, 53°)

The dashboard operates in two modes:
    Live mode   — reads telemetry from TimescaleDB via the ILMOP REST API
    Orbital mode — uses SGP4 orbital mechanics only (no services required)

Sprint 7 addition: ground-track trail
    Each satellite leaves a fading colour trail of recent positions behind
    it on the world map.  Trail history is maintained in st.session_state
    across auto-refresh cycles.  Trail length and visibility are
    configurable from the sidebar.

Run from the ILMOP project root:
    streamlit run services/dashboard/pages/fleet_dashboard.py

Dependencies: streamlit, plotly, sgp4, scipy, numpy
"""

import math
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import plotly.graph_objects as go
import streamlit as st

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title  = 'ILMOP Fleet Dashboard',
    page_icon   = '🛰️',
    layout      = 'wide',
    initial_sidebar_state = 'expanded',
)

# ── path setup (allow running from project root) ──────────────────────────────
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from services.otfs.orbital_profile import OrbitalProfileExporter, OrbitalFrame
from services.navigation.validator  import NavigationValidator

# ── constants ─────────────────────────────────────────────────────────────────
GROUND_STATIONS = {
    # LEO circular stations (Scenarios 1–3)
    'Cambridge, UK'    : {'lat':  52.205, 'lon':   0.119, 'alt':  20.0},
    'Lagos, Nigeria'   : {'lat':   6.454, 'lon':   3.395, 'alt':  41.0},
    'Houston, USA'     : {'lat':  29.760, 'lon': -95.370, 'alt':  15.0},
    'Tokyo, Japan'     : {'lat':  35.690, 'lon':  139.692,'alt':  40.0},
    'Sydney, Australia': {'lat': -33.865, 'lon':  151.210,'alt':  20.0},
    # Molniya HEO stations (Scenario 4) — high-latitude apogee visibility
    'Svalbard, Norway' : {'lat':  78.229, 'lon':   15.608,'alt':  24.0},
    'Fairbanks, Alaska': {'lat':  64.838, 'lon': -147.716,'alt': 136.0},
}

SCENARIOS = {
    'Scenario 1 — 1 sat, 1 plane': {
        'n_planes': 1, 'sats_per_plane': 1,
        'altitude_km': 550.0, 'inclination_deg': 51.6,
        'raan_base': 45.0,   'raan_step': 90.0,
        'eccentricity': 0.001, 'arg_perigee': 0.0,
        'color': '#1D9E75',
        'description': 'Pipeline baseline — single satellite LEO circular',
    },
    'Scenario 2 — 6 sats, 1 plane': {
        'n_planes': 1, 'sats_per_plane': 6,
        'altitude_km': 550.0, 'inclination_deg': 53.0,
        'raan_base': 45.0,   'raan_step': 0.0,
        'eccentricity': 0.001, 'arg_perigee': 0.0,
        'color': '#3B8BD4',
        'description': 'Sequential accumulated ranging — 6 satellites single plane',
    },
    'Scenario 3 — 24 sats, 4 planes': {
        'n_planes': 4, 'sats_per_plane': 6,
        'altitude_km': 550.0, 'inclination_deg': 53.0,
        'raan_base': 45.0,   'raan_step': 90.0,
        'eccentricity': 0.001, 'arg_perigee': 0.0,
        'color': '#E85D24',
        'description': 'Simultaneous trilateration — 24 satellites 4 planes',
    },
    'Scenario 4 — 6 HEO + 12 LEO': {
        'n_planes': 6, 'sats_per_plane': 1,
        'altitude_km': 20200.0, 'inclination_deg': 63.4,
        'raan_base': 0.0,    'raan_step': 60.0, 'ma_plane_step': 60.0,
        'eccentricity': 0.74,  'arg_perigee': 270.0,
        'extra_shells': [{
            'n_planes': 2, 'sats_per_plane': 6,
            'altitude_km': 550.0, 'inclination_deg': 53.0,
            'raan_base': 0.0, 'raan_step': 90.0,
            'eccentricity': 0.001, 'arg_perigee': 0.0,
        }],
        'color': '#B034C5',
        'description': 'Hybrid — 6 Molniya HEO (1 per plane) + 12 LEO (2 planes × 6): HEO anomaly training and the contact-scheduling / relay research scenario',
    },
}

GROUND_TRACK_STEPS  = 90     # points per satellite ground track (1 orbit)
PASS_FORECAST_MIN   = 180    # minutes ahead to forecast passes
PASS_STEP_S         = 30     # step size for pass forecast

# Trail constants
TRAIL_MAX_POINTS    = 180    # hard cap on trail history per satellite
                             # (at 10 s refresh = 30 min max retention)


# ── orbit builder ─────────────────────────────────────────────────────────────

def satellite_slots(scenario_cfg: dict) -> list:
    """
    One (satellite_id, raan_deg, mean_anomaly_deg, shell) tuple per satellite.

    A scenario describes its main orbit shell with the top-level keys; a hybrid
    scenario (Scenario 4: HEO + LEO) lists further shells in 'extra_shells'.
    Satellite IDs of the main shell are P<plane>S<slot>; those of extra shell k
    are prefixed X<k>.
    """
    slots = []
    shells = [scenario_cfg] + list(scenario_cfg.get('extra_shells', []))
    for k, shell in enumerate(shells):
        prefix = '' if k == 0 else f'X{k}'
        for p in range(shell['n_planes']):
            raan = shell['raan_base'] + p * shell['raan_step']
            for s in range(shell['sats_per_plane']):
                ma = (p * shell.get('ma_plane_step', 0.0)
                      + s * (360.0 / shell['sats_per_plane'])) % 360.0
                slots.append((f'{prefix}P{p+1}S{s+1}', raan, ma, shell))
    return slots


def build_exporters(
    scenario_cfg: dict,
    epoch:        datetime,
    gs_lat:       float,
    gs_lon:       float,
    gs_alt:       float,
) -> List[Tuple[str, OrbitalProfileExporter]]:
    """Build one OrbitalProfileExporter per satellite in the scenario."""
    exporters = []
    for sid, raan, ma, shell in satellite_slots(scenario_cfg):
        exp  = OrbitalProfileExporter(
            satellite_id      = sid,
            ground_lat_deg    = gs_lat,
            ground_lon_deg    = gs_lon,
            ground_alt_m      = gs_alt,
            altitude_km       = shell['altitude_km'],
            inclination_deg   = shell['inclination_deg'],
            raan_deg          = raan,
            mean_anomaly_deg  = ma,
            eccentricity      = shell['eccentricity'],
            arg_perigee_deg   = shell['arg_perigee'],
            min_elevation_deg = 10.0,
            epoch             = epoch,
        )
        exporters.append((sid, exp))
    return exporters


def get_current_positions(
    exporters: List[Tuple[str, OrbitalProfileExporter]],
    now:       datetime,
) -> List[dict]:
    """Get the current (instantaneous) position of each satellite."""
    positions = []
    for sid, exp in exporters:
        try:
            frames = exp.export(now, duration_s=1.0, step_s=1.0)
            if frames:
                f = frames[0]
                positions.append({
                    'id'          : sid,
                    'lat'         : _ecef_to_latlon(f)[0],
                    'lon'         : _ecef_to_latlon(f)[1],
                    'elevation_deg': f.elevation_deg,
                    'range_km'    : f.range_m / 1000.0,
                    'azimuth_deg' : f.azimuth_deg,
                    'in_view'     : f.in_view,
                    'range_rate'  : f.range_rate_ms,
                })
        except Exception:
            pass
    return positions


def get_ground_tracks(
    exporters: List[Tuple[str, OrbitalProfileExporter]],
    now:       datetime,
    n_steps:   int = GROUND_TRACK_STEPS,
) -> Dict[str, List[Tuple[float, float]]]:
    """Get one full orbital period of ground track for each satellite."""
    tracks = {}
    for sid, exp in exporters:
        period_s = 2 * math.pi * math.sqrt(
            exp._a_m ** 3 / 3.986_004_418e14
        )
        step_s   = period_s / n_steps
        try:
            frames = exp.export(now, duration_s=period_s, step_s=step_s)
            lats, lons = [], []
            for f in frames:
                lat, lon = _ecef_to_latlon(f)
                lats.append(lat)
                lons.append(lon)
            tracks[sid] = list(zip(lats, lons))
        except Exception:
            tracks[sid] = []
    return tracks


def _ecef_to_latlon(frame: OrbitalFrame) -> Tuple[float, float]:
    """Convert SGP4 ECI frame (via slant range geometry) to approx lat/lon.

    Since OrbitalFrame stores elevation and azimuth from the ground station
    but not directly the satellite's geographic coordinates, we derive the
    subsatellite point from the range, elevation, and azimuth.
    This is an approximation suitable for map display.
    """
    Re = 6_371.0
    rho = frame.range_m / 1000.0
    el  = math.radians(frame.elevation_deg)
    az  = math.radians(frame.azimuth_deg)

    sin_rho = rho * math.cos(el) / Re
    sin_rho = max(-1.0, min(1.0, sin_rho))
    earth_angle = math.asin(sin_rho)

    gs_lat = math.radians(52.205)
    gs_lon = math.radians(0.119)

    sat_lat = math.asin(
        math.sin(gs_lat) * math.cos(earth_angle)
        + math.cos(gs_lat) * math.sin(earth_angle) * math.cos(az)
    )
    sat_lon = gs_lon + math.atan2(
        math.sin(az) * math.sin(earth_angle) * math.cos(gs_lat),
        math.cos(earth_angle) - math.sin(gs_lat) * math.sin(sat_lat)
    )
    return math.degrees(sat_lat), math.degrees(sat_lon)


def forecast_passes(
    exporters : List[Tuple[str, OrbitalProfileExporter]],
    now       : datetime,
    minutes   : int   = PASS_FORECAST_MIN,
    step_s    : float = PASS_STEP_S,
) -> List[dict]:
    """Forecast upcoming contact windows for each satellite."""
    passes = []
    for sid, exp in exporters:
        frames = exp.export(now, duration_s=minutes * 60, step_s=step_s)
        in_pass, pass_start, max_el = False, None, 0.0
        for f in frames:
            if f.in_view and not in_pass:
                in_pass, pass_start, max_el = True, f.timestamp, f.elevation_deg
            elif f.in_view and in_pass:
                max_el = max(max_el, f.elevation_deg)
            elif not f.in_view and in_pass:
                minutes_from_now = (pass_start - now).total_seconds() / 60
                passes.append({
                    'satellite'    : sid,
                    'aos'          : pass_start,
                    'los'          : f.timestamp,
                    'max_el'       : round(max_el, 1),
                    'minutes_away' : round(minutes_from_now, 1),
                    'duration_min' : round((f.timestamp - pass_start).total_seconds() / 60, 1),
                })
                in_pass = False
    passes.sort(key=lambda p: p['aos'])
    return passes[:20]


def compute_gdop(positions: List[dict]) -> float:
    """Compute GDOP from list of currently in-view satellite positions."""
    in_view = [p for p in positions if p['in_view']]
    if len(in_view) < 4:
        return float('inf')
    best_el = max(p['elevation_deg'] for p in in_view)
    return round(1.0 / math.sin(math.radians(max(best_el, 5.0))), 2)


# ── trail helpers ─────────────────────────────────────────────────────────────

def _update_trail(
    trail_history: Dict[str, deque],
    positions:     List[dict],
    sim_now:       datetime,
    max_points:    int,
    trail_min:     int,
) -> None:
    """Append the current satellite positions to the per-satellite trail deque.

    Points older than trail_min minutes are pruned from the front.

    Args:
        trail_history:  session_state dict of {sat_id: deque of (lat, lon, sim_time)}
        positions:      current positions list from get_current_positions()
        sim_now:        current simulation datetime (used for age comparison)
        max_points:     absolute maximum points per satellite (hard cap)
        trail_min:      soft age limit in minutes; older points are dropped
    """
    cutoff = sim_now - timedelta(minutes=trail_min)
    for p in positions:
        sid = p['id']
        if sid not in trail_history:
            trail_history[sid] = deque(maxlen=max_points)
        trail_history[sid].append((p['lat'], p['lon'], sim_now))
        # Prune points older than the configured trail window
        while trail_history[sid] and trail_history[sid][0][2] < cutoff:
            trail_history[sid].popleft()


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert '#RRGGBB' to (R, G, B) integers."""
    h = hex_color.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def add_trail_traces(
    fig:           go.Figure,
    trail_history: Dict[str, deque],
    scenario_color: str,
    trail_min:     int,
    sim_now:       datetime,
) -> None:
    """Add one fading trail trace per satellite to the Plotly figure.

    Each trail is drawn as a single Scattergeo line with an opacity gradient
    achieved by rendering N short line segments, each with decreasing opacity
    from oldest (nearly transparent) to newest (fully opaque).

    The trail is split into TRAIL_SEGMENTS segments.  The newest segment has
    opacity TRAIL_OPACITY_MAX and each older segment fades linearly to
    TRAIL_OPACITY_MIN.  Antimeridian crossings (|Δlon| > 180°) are broken
    with a None separator to prevent the line wrapping around the globe.

    Args:
        fig:            Plotly Figure to add traces to
        trail_history:  {sat_id: deque of (lat, lon, sim_time)}
        scenario_color: hex colour string for the current scenario
        trail_min:      current trail length setting in minutes
        sim_now:        current simulation time (for age-based fade)
    """
    TRAIL_SEGMENTS     = 8     # number of opacity bands
    TRAIL_OPACITY_MAX  = 0.90  # newest segment
    TRAIL_OPACITY_MIN  = 0.05  # oldest segment

    r, g, b = _hex_to_rgb(scenario_color)

    for sid, history in trail_history.items():
        points = list(history)   # list of (lat, lon, sim_time)
        if len(points) < 2:
            continue

        n = len(points)
        # Divide the trail into TRAIL_SEGMENTS chunks
        seg_size = max(1, n // TRAIL_SEGMENTS)

        for seg_idx in range(TRAIL_SEGMENTS):
            start = seg_idx * seg_size
            end   = start + seg_size + 1   # +1 for connectivity
            if seg_idx == TRAIL_SEGMENTS - 1:
                end = n                    # last segment goes to the end
            if start >= n - 1:
                break

            seg_points = points[start:end]
            if len(seg_points) < 2:
                continue

            # Opacity: linearly interpolated from TRAIL_OPACITY_MIN (oldest)
            # to TRAIL_OPACITY_MAX (newest)
            frac    = (seg_idx + 1) / TRAIL_SEGMENTS   # 0→1, newest = 1
            opacity = TRAIL_OPACITY_MIN + frac * (TRAIL_OPACITY_MAX - TRAIL_OPACITY_MIN)

            # Build lat/lon lists, inserting None at antimeridian crossings
            lats: List[Optional[float]] = []
            lons: List[Optional[float]] = []
            prev_lon = seg_points[0][1]
            for lat, lon, _t in seg_points:
                if lats and abs(lon - prev_lon) > 180.0:
                    lats.append(None)
                    lons.append(None)
                lats.append(lat)
                lons.append(lon)
                prev_lon = lon

            fig.add_trace(go.Scattergeo(
                lat  = lats,
                lon  = lons,
                mode = 'lines',
                line = dict(
                    width = 2,
                    color = f'rgba({r},{g},{b},{opacity:.3f})',
                ),
                showlegend = (seg_idx == TRAIL_SEGMENTS - 1 and
                              sid == list(trail_history.keys())[0]),
                name       = 'Trail' if (seg_idx == TRAIL_SEGMENTS - 1 and
                                         sid == list(trail_history.keys())[0]) else None,
                hoverinfo  = 'skip',
            ))


# ── sidebar controls ──────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown('## 🛰️ ILMOP Fleet Dashboard')
    st.markdown('**Sprint 7 · Deliverable 6**')
    st.divider()

    scenario_name = st.selectbox(
        'Constellation scenario',
        list(SCENARIOS.keys()),
        index=2,
        help='Select the active constellation scenario',
    )
    scenario_cfg = SCENARIOS[scenario_name]
    st.caption(scenario_cfg['description'])

    st.divider()

    gs_name = st.selectbox(
        'Ground station',
        list(GROUND_STATIONS.keys()),
        index=0,
    )
    gs = GROUND_STATIONS[gs_name]

    st.divider()

    show_tracks = st.toggle('Show ground tracks', value=True)
    show_trail  = st.toggle('Show position trail', value=True,
                            help='Fading trail of recent satellite positions')

    trail_min = 10
    if show_trail:
        trail_min = st.slider(
            'Trail length (minutes)',
            min_value=1, max_value=15, value=10, step=1,
            help='How many minutes of history to show behind each satellite',
        )

    show_passes = st.toggle('Show pass schedule', value=True)

    sim_speed = st.select_slider(
        'Simulation speed',
        options=[1, 10, 60, 300, 600],
        value=1,
        format_func=lambda v: f'x{v}',
        help='Simulated seconds per real second. Use x60 or higher to see the trail build quickly',
    )

    refresh_s = st.slider(
        'Refresh interval (s)', min_value=5, max_value=60, value=10
    )

    st.divider()
    if st.button('🔄 Refresh now'):
        st.rerun()

    if st.button('🗑️ Clear trail history'):
        if 'trail_history' in st.session_state:
            del st.session_state['trail_history']
        st.rerun()

    st.caption(f'Ground station: {gs_name}')
    st.caption(f'Lat {gs["lat"]:.3f}° | Lon {gs["lon"]:.3f}°')


# ── main layout ───────────────────────────────────────────────────────────────

now_utc = datetime.now(tz=timezone.utc)
MODEL_EPOCH = datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc)
# Accumulate simulated time per refresh so changing the speed never makes the
# clock jump: each rerun adds (real seconds elapsed x current speed).
_wall_now = time.time()
if 'sim_elapsed' not in st.session_state:
    st.session_state.sim_elapsed = 0.0
    st.session_state.last_wall   = _wall_now
st.session_state.sim_elapsed += (_wall_now - st.session_state.last_wall) * sim_speed
st.session_state.last_wall    = _wall_now
sim_elapsed = st.session_state.sim_elapsed
sim_now = MODEL_EPOCH + timedelta(seconds=sim_elapsed)

st.markdown(f"""
<h2 style='margin-bottom:4px'>🛰️ ILMOP Fleet Dashboard</h2>
<p style='color:#595959;margin-top:0'>
{scenario_name} &nbsp;|&nbsp; Ground station: {gs_name} &nbsp;|&nbsp;
Simulation time: <b>{sim_now.strftime('%H:%M:%S')} UTC</b>
</p>
""", unsafe_allow_html=True)

# Build exporters and compute positions
with st.spinner('Computing orbital positions…'):
    exporters = build_exporters(
        scenario_cfg, MODEL_EPOCH,
        gs['lat'], gs['lon'], gs['alt']
    )
    positions = get_current_positions(exporters, sim_now)
    if show_tracks:
        tracks = get_ground_tracks(exporters, sim_now)
    else:
        tracks = {}
    if show_passes:
        passes = forecast_passes(exporters, sim_now)
    else:
        passes = []

# ── trail state update ────────────────────────────────────────────────────────
# Initialise trail history in session_state on first run.
# The trail_scenario key detects when the user switches scenario, so we
# can reset history and avoid mixing trails from different constellations.

if 'trail_history' not in st.session_state:
    st.session_state.trail_history = {}
if st.session_state.get('trail_scenario') != scenario_name:
    st.session_state.trail_history  = {}
    st.session_state.trail_scenario = scenario_name

if show_trail:
    _update_trail(
        st.session_state.trail_history,
        positions,
        sim_now,
        max_points = TRAIL_MAX_POINTS,
        trail_min  = trail_min,
    )


# ── metric row ────────────────────────────────────────────────────────────────

n_sats     = len(positions)
n_visible  = sum(1 for p in positions if p['in_view'])
gdop       = compute_gdop(positions)
gdop_str   = f'{gdop:.2f}' if math.isfinite(gdop) else 'N/A (< 4 sats)'
next_pass  = passes[0] if passes else None
next_str   = (f"in {next_pass['minutes_away']:.0f} min ({next_pass['satellite']})"
              if next_pass else 'No pass in forecast window')

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric('Constellation size',    f'{n_sats} satellites')
col2.metric('Satellites in view',    f'{n_visible}',
            delta=f'+{n_visible}' if n_visible else None)
col3.metric('Simultaneous visible',  f'{n_visible}',
            help='Satellites above 10° elevation from selected ground station')
col4.metric('GDOP (current)',        gdop_str,
            help='Geometric Dilution of Precision. Valid only with ≥4 satellites')
col5.metric('Next contact',          next_str)

st.divider()


# ── world map ─────────────────────────────────────────────────────────────────

fig = go.Figure()

# 1. Ground tracks (full orbital period, dotted, low opacity)
if tracks:
    for sid, track in tracks.items():
        if not track:
            continue
        lats = [t[0] for t in track]
        lons = [t[1] for t in track]
        fig.add_trace(go.Scattergeo(
            lat  = lats, lon = lons,
            mode = 'lines',
            line = dict(width=0.5, color=scenario_cfg['color'], dash='dot'),
            opacity = 0.25,
            showlegend = False,
            hoverinfo = 'skip',
        ))

# 2. Ground-track trail (recent position history, fading colour)
if show_trail and st.session_state.trail_history:
    add_trail_traces(
        fig,
        st.session_state.trail_history,
        scenario_cfg['color'],
        trail_min,
        sim_now,
    )

# 3. Current satellite positions — below-horizon (dim)
vis_lats  = [p['lat'] for p in positions if     p['in_view']]
vis_lons  = [p['lon'] for p in positions if     p['in_view']]
vis_ids   = [p['id']  for p in positions if     p['in_view']]
vis_text  = [f"{p['id']}<br>El: {p['elevation_deg']:.1f}°<br>"
             f"Range: {p['range_km']:.0f} km<br>"
             f"Az: {p['azimuth_deg']:.1f}°"
             for p in positions if p['in_view']]

novis_lats = [p['lat'] for p in positions if not p['in_view']]
novis_lons = [p['lon'] for p in positions if not p['in_view']]
novis_ids  = [p['id']  for p in positions if not p['in_view']]
novis_text = [f"{p['id']}<br>Below horizon" for p in positions if not p['in_view']]

if novis_lats:
    fig.add_trace(go.Scattergeo(
        lat  = novis_lats, lon = novis_lons,
        mode = 'markers',
        marker = dict(size=8, color='#888888', symbol='circle',
                      line=dict(color='#444', width=1)),
        text      = novis_text,
        hovertemplate = '%{text}<extra></extra>',
        name      = 'Below horizon',
    ))

# 4. Current satellite positions — in-view (bright)
if vis_lats:
    fig.add_trace(go.Scattergeo(
        lat  = vis_lats, lon = vis_lons,
        mode = 'markers+text',
        marker = dict(size=14, color=scenario_cfg['color'], symbol='circle',
                      line=dict(color='white', width=2)),
        text          = vis_ids,
        textposition  = 'top center',
        textfont      = dict(size=11, color='white'),
        customdata    = vis_text,
        hovertemplate = '%{customdata}<extra></extra>',
        name          = 'In view',
    ))

# 5. Ground station marker
fig.add_trace(go.Scattergeo(
    lat  = [gs['lat']], lon = [gs['lon']],
    mode = 'markers+text',
    marker = dict(size=16, color='#FFD700', symbol='star',
                  line=dict(color='#1F3864', width=2)),
    text          = [gs_name],
    textposition  = 'bottom right',
    textfont      = dict(size=11, color='#FFD700'),
    hovertemplate = f'{gs_name}<br>{gs["lat"]:.3f}°N {gs["lon"]:.3f}°E<extra></extra>',
    name          = 'Ground station',
))

fig.update_layout(
    height = 520,
    margin = dict(l=0, r=0, t=0, b=0),
    paper_bgcolor = '#0D1117',
    geo = dict(
        showland       = True,  landcolor    = '#1C2333',
        showocean      = True,  oceancolor   = '#0D1117',
        showcoastlines = True,  coastlinecolor = '#2F4060',
        showcountries  = True,  countrycolor = '#1F2D45',
        showframe      = False,
        bgcolor        = '#0D1117',
        projection_type = 'natural earth',
    ),
    legend = dict(
        font=dict(color='white', size=11),
        bgcolor='rgba(0,0,0,0.5)',
        x=0.01, y=0.99,
    ),
)

st.plotly_chart(fig, width='stretch')


# ── trail info bar ────────────────────────────────────────────────────────────

if show_trail:
    n_trail_pts = sum(len(v) for v in st.session_state.trail_history.values())
    oldest = None
    for hist in st.session_state.trail_history.values():
        if hist:
            t = hist[0][2]
            if oldest is None or t < oldest:
                oldest = t
    age_str = ''
    if oldest:
        age_s = (sim_now - oldest).total_seconds()
        age_str = f' · oldest point {age_s/60:.1f} min ago'
    st.caption(
        f'🛤️  Trail: {trail_min} min window · '
        f'{n_trail_pts} stored points across {len(st.session_state.trail_history)} satellites'
        f'{age_str}'
    )


# ── lower panels ──────────────────────────────────────────────────────────────

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader('📡 Satellite visibility status')
    if positions:
        rows = []
        for p in sorted(positions, key=lambda x: -x['elevation_deg']):
            status = '✅ IN VIEW' if p['in_view'] else '⬇️ Below horizon'
            rows.append({
                'Satellite' : p['id'],
                'Status'    : status,
                'Elevation' : f"{p['elevation_deg']:.1f}°",
                'Range (km)': f"{p['range_km']:.0f}",
                'Azimuth'   : f"{p['azimuth_deg']:.1f}°",
                'Rng rate'  : f"{p['range_rate']:+.0f} m/s",
            })
        st.dataframe(rows, width='stretch', height=320)
    else:
        st.info('No satellite positions available — check orbital parameters.')

with col_right:
    if show_passes and passes:
        st.subheader('📅 Upcoming contact windows')
        pass_rows = []
        for p in passes[:10]:
            pass_rows.append({
                'Satellite'    : p['satellite'],
                'AOS (UTC)'    : p['aos'].strftime('%H:%M:%S'),
                'LOS (UTC)'    : p['los'].strftime('%H:%M:%S'),
                'Duration (min)': f"{p['duration_min']:.1f}",
                'Max El (°)'   : f"{p['max_el']:.1f}",
                'In (min)'     : f"{p['minutes_away']:.0f}",
            })
        st.dataframe(pass_rows, width='stretch', height=320)
    else:
        st.subheader('📊 Navigation geometry summary')
        st.metric('Range resolution Δr',
                  '14.99 m', help='c/(2B) at B = 10 MHz')
        st.metric('GDOP at current epoch', gdop_str)
        st.metric('Satellites in view', f'{n_visible} / {n_sats}')
        if n_visible > 0:
            max_el_p = max((p for p in positions if p['in_view']),
                           key=lambda p: p['elevation_deg'])
            st.metric('Best satellite',
                      f"{max_el_p['id']} at {max_el_p['elevation_deg']:.1f}°")


# ── coverage bar ──────────────────────────────────────────────────────────────

st.divider()
st.markdown(f"""
**Coverage summary:** {n_visible}/{n_sats} satellites currently visible from {gs_name}.
{"Full 3D position fix possible (≥4 simultaneous)." if n_visible >= 4
 else f"3D fix requires {max(0, 4-n_visible)} more satellite(s) simultaneously."}
Simulation time: **{sim_now.strftime('%Y-%m-%d %H:%M:%S')} UTC** ·
Refresh every **{refresh_s}s**
""")


# ── auto-refresh ──────────────────────────────────────────────────────────────

time.sleep(refresh_s)
st.rerun()
