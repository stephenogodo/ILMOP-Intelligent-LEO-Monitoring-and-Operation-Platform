"""
services/dashboard/coverage_engine.py

ILMOP Coverage Statistics Engine
==================================

Pure-Python coverage computation logic, separated from the Streamlit UI.
Import this module for computation; import coverage_statistics.py only
to run the dashboard.

Used by:
    services/dashboard/pages/coverage_statistics.py  (Streamlit page)
    tests/test_coverage_statistics.py                (test suite)
"""

import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

from services.otfs.orbital_profile import OrbitalProfileExporter

# ── constellation and ground station definitions ──────────────────────────────

GROUND_STATIONS = {
    # LEO circular stations (Scenarios 1–3)
    'Cambridge, UK'    : {'lat':  52.205, 'lon':   0.119, 'alt':  20.0},
    'Lagos, Nigeria'   : {'lat':   6.454, 'lon':   3.395, 'alt':  41.0},
    'Houston, USA'     : {'lat':  29.760, 'lon': -95.370, 'alt':  15.0},
    'Tokyo, Japan'     : {'lat':  35.690, 'lon': 139.692, 'alt':  40.0},
    'Sydney, Australia': {'lat': -33.865, 'lon': 151.210, 'alt':  20.0},
    # Molniya HEO stations (Scenario 4) — high-latitude apogee visibility
    'Svalbard, Norway' : {'lat':  78.229, 'lon':  15.608, 'alt':  24.0},
    'Fairbanks, Alaska': {'lat':  64.838, 'lon':-147.716, 'alt': 136.0},
}

SCENARIOS = {
    'Scenario 1': {
        'n_planes': 1, 'sats_per_plane': 1,
        'altitude_km': 550.0, 'inclination_deg': 51.6,
        'raan_base': 45.0, 'raan_step': 90.0,
        'eccentricity': 0.001, 'arg_perigee': 0.0,
        'color': '#1D9E75', 'label': '1 sat / 1 plane',
    },
    'Scenario 2': {
        'n_planes': 1, 'sats_per_plane': 6,
        'altitude_km': 550.0, 'inclination_deg': 53.0,
        'raan_base': 45.0, 'raan_step': 0.0,
        'eccentricity': 0.001, 'arg_perigee': 0.0,
        'color': '#3B8BD4', 'label': '6 sats / 1 plane',
    },
    'Scenario 3': {
        'n_planes': 4, 'sats_per_plane': 6,
        'altitude_km': 550.0, 'inclination_deg': 53.0,
        'raan_base': 45.0, 'raan_step': 90.0,
        'eccentricity': 0.001, 'arg_perigee': 0.0,
        'color': '#E85D24', 'label': '24 sats / 4 planes',
    },
    'Scenario 4': {
        'n_planes': 1, 'sats_per_plane': 6,
        'altitude_km': 20200.0, 'inclination_deg': 63.4,
        'raan_base': 0.0, 'raan_step': 60.0,
        'eccentricity': 0.74, 'arg_perigee': 270.0,
        'color': '#B034C5', 'label': '6 Molniya HEO',
    },
}

EPOCH = datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc)


# ── pass detection ────────────────────────────────────────────────────────────

def detect_passes(sim_counts: List[int], step_s: float) -> List[dict]:
    """
    Identify individual contact windows from the simultaneous count series.

    Parameters
    ----------
    sim_counts : List[int]
        Number of satellites simultaneously in view at each time step.
    step_s : float
        Time step between consecutive counts in seconds.

    Returns
    -------
    List[dict] with keys: start_step, end_step, duration_s, peak_count.
    """
    passes = []
    in_pass, start = False, 0
    for i, c in enumerate(sim_counts):
        if c > 0 and not in_pass:
            in_pass, start = True, i
        elif c == 0 and in_pass:
            passes.append({
                'start_step' : start,
                'end_step'   : i,
                'duration_s' : (i - start) * step_s,
                'peak_count' : max(sim_counts[start:i]),
            })
            in_pass = False
    if in_pass:
        passes.append({
            'start_step' : start,
            'end_step'   : len(sim_counts) - 1,
            'duration_s' : (len(sim_counts) - 1 - start) * step_s,
            'peak_count' : max(sim_counts[start:]),
        })
    return passes


# ── coverage statistics ───────────────────────────────────────────────────────

def compute_coverage_stats(
    scenario_cfg : dict,
    gs           : dict,
    window_min   : int   = 120,
    step_s       : float = 60.0,
    min_el_deg   : float = 10.0,
) -> dict:
    """
    Compute full coverage statistics for one scenario / ground station pair.

    Parameters
    ----------
    scenario_cfg : dict
        Constellation parameters (n_planes, sats_per_plane, altitude_km,
        inclination_deg, raan_base, raan_step, eccentricity, arg_perigee).
    gs : dict
        Ground station with keys {lat, lon, alt}.
    window_min : int
        Observation window duration in minutes. Default 120.
    step_s : float
        Time step in seconds. Default 60 s.
    min_el_deg : float
        Minimum elevation for contact. Default 10°.

    Returns
    -------
    dict containing:
        n_satellites, n_steps, window_min, contact_fraction,
        n_passes, mean_pass_min, max_pass_min,
        mean_revisit_min, max_revisit_min,
        mean_simultaneous, mean_sim_visible, peak_simultaneous,
        fix_fraction, sim_counts, passes.
    """
    n_planes       = scenario_cfg['n_planes']
    sats_per_plane = scenario_cfg['sats_per_plane']
    duration_s     = window_min * 60.0
    n_steps        = int(duration_s / step_s) + 1

    # Build per-satellite in-view boolean time series
    sat_inview: Dict[str, List[bool]] = {}
    for p in range(n_planes):
        raan = scenario_cfg['raan_base'] + p * scenario_cfg['raan_step']
        for s in range(sats_per_plane):
            ma  = s * (360.0 / sats_per_plane)
            sid = f'P{p+1}S{s+1}'
            try:
                exp = OrbitalProfileExporter(
                    satellite_id     = sid,
                    ground_lat_deg   = gs['lat'],
                    ground_lon_deg   = gs['lon'],
                    ground_alt_m     = gs['alt'],
                    altitude_km      = scenario_cfg['altitude_km'],
                    inclination_deg  = scenario_cfg['inclination_deg'],
                    raan_deg         = raan,
                    mean_anomaly_deg = ma,
                    eccentricity     = scenario_cfg['eccentricity'],
                    arg_perigee_deg  = scenario_cfg['arg_perigee'],
                    min_elevation_deg= min_el_deg,
                    epoch            = EPOCH,
                )
                frames = exp.export(EPOCH, duration_s=duration_s, step_s=step_s)
                sat_inview[sid] = [f.in_view for f in frames]
            except Exception:
                sat_inview[sid] = [False] * n_steps

    # Per-step simultaneous count
    n_actual   = min(len(v) for v in sat_inview.values()) if sat_inview else 0
    sim_counts = [
        sum(1 for iv in sat_inview.values() if i < len(iv) and iv[i])
        for i in range(n_actual)
    ]

    # Contact fraction
    visible_steps = sum(1 for c in sim_counts if c > 0)
    contact_frac  = visible_steps / max(n_actual, 1)

    # Peak and mean simultaneous
    peak_sim           = max(sim_counts) if sim_counts else 0
    mean_sim           = sum(sim_counts) / max(n_actual, 1)
    vis_only           = [c for c in sim_counts if c > 0]
    mean_sim_visible   = sum(vis_only) / max(len(vis_only), 1) if vis_only else 0.0

    # Pass detection
    passes   = detect_passes(sim_counts, step_s)
    n_passes = len(passes)
    durations = [p['duration_s'] for p in passes]
    mean_pass_min = (sum(durations) / max(len(durations), 1)) / 60.0
    max_pass_min  = (max(durations) if durations else 0) / 60.0

    # Revisit times (gaps between consecutive passes)
    gaps = [
        (passes[i]['start_step'] - passes[i-1]['end_step']) * step_s
        for i in range(1, len(passes))
        if (passes[i]['start_step'] - passes[i-1]['end_step']) > 0
    ]
    mean_revisit_min = (sum(gaps) / max(len(gaps), 1)) / 60.0 if gaps else None
    max_revisit_min  = (max(gaps) / 60.0)                      if gaps else None

    # 3D fix fraction (epochs with ≥4 simultaneous)
    fix_epochs = sum(1 for c in sim_counts if c >= 4)
    fix_frac   = fix_epochs / max(n_actual, 1)

    return {
        'n_satellites'      : n_planes * sats_per_plane,
        'n_steps'           : n_actual,
        'window_min'        : window_min,
        'contact_fraction'  : round(contact_frac * 100, 1),
        'n_passes'          : n_passes,
        'mean_pass_min'     : round(mean_pass_min, 1),
        'max_pass_min'      : round(max_pass_min, 1),
        'mean_revisit_min'  : round(mean_revisit_min, 1) if mean_revisit_min else None,
        'max_revisit_min'   : round(max_revisit_min, 1)  if max_revisit_min  else None,
        'mean_simultaneous' : round(mean_sim, 2),
        'mean_sim_visible'  : round(mean_sim_visible, 2),
        'peak_simultaneous' : peak_sim,
        'fix_fraction'      : round(fix_frac * 100, 1),
        'sim_counts'        : sim_counts,
        'passes'            : passes,
    }
