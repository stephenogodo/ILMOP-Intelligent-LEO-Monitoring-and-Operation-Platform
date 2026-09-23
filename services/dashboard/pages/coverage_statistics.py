"""
services/dashboard/pages/coverage_statistics.py

ILMOP Coverage Statistics — Sprint 6, Deliverable 6
=====================================================

Computes and displays coverage statistics for all four constellation
scenarios from any ground station:

    - Contact fraction       : % of observation window with ≥1 satellite visible
    - Mean pass duration     : average contact window length (minutes)
    - Mean revisit time      : average gap between consecutive passes (minutes)
    - Max revisit time       : worst-case gap (minutes) — critical for operations
    - Mean simultaneous sats : average number visible at the same time
    - Peak simultaneous sats : maximum ever seen simultaneously
    - Pass count             : total contact windows in the observation window

These statistics directly support the thesis navigation accuracy claims
by showing the contact availability that underpins the pseudorange
accumulation strategy across Scenarios 1→2→3.

Run from the ILMOP project root:
    streamlit run services/dashboard/pages/coverage_statistics.py

Dependencies: streamlit, plotly, sgp4, numpy
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

st.set_page_config(
    page_title  = 'ILMOP Coverage Statistics',
    page_icon   = '📊',
    layout      = 'wide',
    initial_sidebar_state = 'expanded',
)

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from services.dashboard.coverage_engine import (
    compute_coverage_stats,
    detect_passes as _detect_passes,
    GROUND_STATIONS,
    SCENARIOS,
    EPOCH,
)

# ── constants already imported from coverage_engine ──────────────────────────

GROUND_STATIONS = {
    'Cambridge, UK'    : {'lat':  52.205, 'lon':   0.119, 'alt':  20.0},
    'Lagos, Nigeria'   : {'lat':   6.454, 'lon':   3.395, 'alt':  41.0},
    'Houston, USA'     : {'lat':  29.760, 'lon': -95.370, 'alt':  15.0},
    'Tokyo, Japan'     : {'lat':  35.690, 'lon': 139.692, 'alt':  40.0},
    'Sydney, Australia': {'lat': -33.865, 'lon': 151.210, 'alt':  20.0},
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


# ── coverage computation ───────────────────────────────────────────────────────

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
        Constellation parameters (n_planes, sats_per_plane, etc.)
    gs : dict
        Ground station {lat, lon, alt}
    window_min : int
        Observation window duration in minutes. Default 120.
    step_s : float
        Time step in seconds. Default 60 s.
    min_el_deg : float
        Minimum elevation for contact. Default 10°.

    Returns
    -------
    dict with all coverage statistics.
    """
    n_planes       = scenario_cfg['n_planes']
    sats_per_plane = scenario_cfg['sats_per_plane']
    duration_s     = window_min * 60.0
    n_steps        = int(duration_s / step_s) + 1

    # Build per-satellite in-view time series
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
    n_actual = min(len(v) for v in sat_inview.values())
    sim_counts = [
        sum(1 for iv in sat_inview.values() if i < len(iv) and iv[i])
        for i in range(n_actual)
    ]

    # Contact fraction
    visible_steps  = sum(1 for c in sim_counts if c > 0)
    contact_frac   = visible_steps / max(n_actual, 1)

    # Peak simultaneous
    peak_sim = max(sim_counts) if sim_counts else 0

    # Mean simultaneous (over all steps)
    mean_sim = sum(sim_counts) / max(n_actual, 1)

    # Mean simultaneous (over visible steps only)
    vis_only = [c for c in sim_counts if c > 0]
    mean_sim_visible = sum(vis_only) / max(len(vis_only), 1) if vis_only else 0.0

    # Pass detection — identify individual contact windows
    passes = _detect_passes(sim_counts, step_s)
    n_passes = len(passes)

    # Mean and max pass duration
    durations = [p['duration_s'] for p in passes]
    mean_pass_min = (sum(durations) / max(len(durations), 1)) / 60.0
    max_pass_min  = (max(durations) if durations else 0) / 60.0

    # Revisit times (gaps between passes)
    gaps = []
    for i in range(1, len(passes)):
        gap_s = passes[i]['start_step'] * step_s - passes[i-1]['end_step'] * step_s
        if gap_s > 0:
            gaps.append(gap_s)
    mean_revisit_min = (sum(gaps) / max(len(gaps), 1)) / 60.0 if gaps else float('nan')
    max_revisit_min  = (max(gaps) / 60.0) if gaps else float('nan')

    # 3D fix fraction (epochs with ≥4 simultaneous)
    fix_epochs  = sum(1 for c in sim_counts if c >= 4)
    fix_frac    = fix_epochs / max(n_actual, 1)

    return {
        'n_satellites'        : n_planes * sats_per_plane,
        'n_steps'             : n_actual,
        'window_min'          : window_min,
        'contact_fraction'    : round(contact_frac * 100, 1),
        'n_passes'            : n_passes,
        'mean_pass_min'       : round(mean_pass_min, 1),
        'max_pass_min'        : round(max_pass_min, 1),
        'mean_revisit_min'    : round(mean_revisit_min, 1) if not math.isnan(mean_revisit_min) else None,
        'max_revisit_min'     : round(max_revisit_min, 1)  if not math.isnan(max_revisit_min)  else None,
        'mean_simultaneous'   : round(mean_sim, 2),
        'mean_sim_visible'    : round(mean_sim_visible, 2),
        'peak_simultaneous'   : peak_sim,
        'fix_fraction'        : round(fix_frac * 100, 1),
        'sim_counts'          : sim_counts,
        'passes'              : passes,
    }


def _detect_passes(sim_counts: List[int], step_s: float) -> List[dict]:
    """Identify individual contact windows from the simultaneous count series."""
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


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown('## 📊 Coverage Statistics')
    st.markdown('**Sprint 6 · Deliverable 6**')
    st.divider()

    gs_name = st.selectbox('Ground station', list(GROUND_STATIONS.keys()))
    gs = GROUND_STATIONS[gs_name]

    window_min = st.slider('Observation window (min)', 60, 480, 120, 30)
    step_s     = st.selectbox('Time step', [30, 60, 120], index=1,
                               format_func=lambda s: f'{s} seconds')
    min_el     = st.slider('Min elevation (°)', 5, 20, 10)

    st.divider()
    scenarios_selected = st.multiselect(
        'Scenarios to compute',
        list(SCENARIOS.keys()),
        default=list(SCENARIOS.keys()),
    )

    compute = st.button('▶ Compute statistics', type='primary')
    st.divider()
    st.caption(f'{gs_name}')
    st.caption(f'Lat {gs["lat"]:.3f}° | Lon {gs["lon"]:.3f}°')


# ── main ──────────────────────────────────────────────────────────────────────

st.markdown('## 📊 ILMOP Coverage Statistics')
st.markdown(
    f'Ground station: **{gs_name}** · '
    f'Window: **{window_min} min** · '
    f'Step: **{step_s} s** · '
    f'Min elevation: **{min_el}°**'
)
st.divider()

if not compute and 'coverage_results' not in st.session_state:
    st.info('Select scenarios and click **▶ Compute statistics** to begin.')
    st.stop()

if compute:
    results = {}
    prog = st.progress(0, text='Computing orbital mechanics…')
    for i, sc_name in enumerate(scenarios_selected):
        prog.progress((i + 1) / len(scenarios_selected),
                      text=f'Computing {sc_name}…')
        results[sc_name] = compute_coverage_stats(
            SCENARIOS[sc_name], gs,
            window_min=window_min,
            step_s=float(step_s),
            min_el_deg=float(min_el),
        )
    prog.empty()
    st.session_state['coverage_results'] = results
    st.session_state['coverage_gs']      = gs_name
    st.session_state['coverage_window']  = window_min

results = st.session_state.get('coverage_results', {})
if not results:
    st.stop()


# ── summary table ─────────────────────────────────────────────────────────────

st.subheader('📋 Summary statistics')

table_rows = []
for sc_name, r in results.items():
    revisit   = f"{r['mean_revisit_min']} min" if r['mean_revisit_min'] else 'N/A'
    max_rev   = f"{r['max_revisit_min']} min"  if r['max_revisit_min']  else 'N/A'
    table_rows.append({
        'Scenario'            : sc_name,
        'Satellites'          : r['n_satellites'],
        'Contact %'           : f"{r['contact_fraction']}%",
        'Passes'              : r['n_passes'],
        'Mean pass (min)'     : r['mean_pass_min'],
        'Max pass (min)'      : r['max_pass_min'],
        'Mean revisit (min)'  : revisit,
        'Max revisit (min)'   : max_rev,
        'Mean sim. sats'      : r['mean_simultaneous'],
        'Peak sim. sats'      : r['peak_simultaneous'],
        '3D fix %'            : f"{r['fix_fraction']}%",
    })

st.dataframe(table_rows, use_container_width=True)
st.divider()


# ── bar charts ────────────────────────────────────────────────────────────────

col1, col2 = st.columns(2)

with col1:
    st.subheader('Contact fraction per scenario')
    fig_cf = go.Figure()
    for sc_name, r in results.items():
        color = SCENARIOS[sc_name]['color']
        fig_cf.add_trace(go.Bar(
            x    = [SCENARIOS[sc_name]['label']],
            y    = [r['contact_fraction']],
            name = sc_name,
            marker_color = color,
            text = [f"{r['contact_fraction']}%"],
            textposition = 'outside',
        ))
    fig_cf.update_layout(
        yaxis_title = 'Contact fraction (%)',
        yaxis_range = [0, 105],
        showlegend  = False,
        height      = 320,
        margin      = dict(t=20, b=20),
    )
    st.plotly_chart(fig_cf, use_container_width=True)

with col2:
    st.subheader('Mean revisit time per scenario')
    fig_rv = go.Figure()
    for sc_name, r in results.items():
        if r['mean_revisit_min'] is None:
            continue
        color = SCENARIOS[sc_name]['color']
        fig_rv.add_trace(go.Bar(
            x    = [SCENARIOS[sc_name]['label']],
            y    = [r['mean_revisit_min']],
            name = sc_name,
            marker_color = color,
            text = [f"{r['mean_revisit_min']} min"],
            textposition = 'outside',
        ))
    fig_rv.update_layout(
        yaxis_title = 'Mean revisit time (min)',
        showlegend  = False,
        height      = 320,
        margin      = dict(t=20, b=20),
    )
    st.plotly_chart(fig_rv, use_container_width=True)

st.divider()


# ── simultaneous visibility timeline ─────────────────────────────────────────

st.subheader('📡 Simultaneous visibility timeline')

fig_tl = go.Figure()
times_min = [i * step_s / 60.0 for i in range(
    max(len(r['sim_counts']) for r in results.values())
)]

for sc_name, r in results.items():
    counts = r['sim_counts']
    t      = [i * step_s / 60.0 for i in range(len(counts))]
    fig_tl.add_trace(go.Scatter(
        x    = t, y = counts,
        mode = 'lines',
        name = f"{sc_name} ({SCENARIOS[sc_name]['label']})",
        line = dict(color=SCENARIOS[sc_name]['color'], width=2),
        fill = 'tozeroy',
        fillcolor = SCENARIOS[sc_name]['color'].replace('#', 'rgba(') + ',0.08)',
    ))

# 3D fix threshold line
fig_tl.add_hline(
    y=4, line_dash='dash', line_color='#FFD700', line_width=1,
    annotation_text='3D fix threshold (4 sats)',
    annotation_position='bottom right',
    annotation_font_color='#FFD700',
)

fig_tl.update_layout(
    xaxis_title = 'Simulation time (minutes)',
    yaxis_title = 'Simultaneous satellites in view',
    height      = 320,
    margin      = dict(t=20, b=20),
    legend      = dict(x=0.01, y=0.99),
)
st.plotly_chart(fig_tl, use_container_width=True)

st.divider()


# ── progressive improvement summary ──────────────────────────────────────────

st.subheader('📈 Progressive improvement: Scenario 1 → 2 → 3')
if all(k in results for k in ['Scenario 1', 'Scenario 2', 'Scenario 3']):
    r1, r2, r3 = results['Scenario 1'], results['Scenario 2'], results['Scenario 3']
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric('Contact fraction',
                  f"{r1['contact_fraction']}%",
                  label_visibility='visible')
        st.caption('Scenario 1 (1 satellite)')
    with c2:
        delta2 = round(r2['contact_fraction'] - r1['contact_fraction'], 1)
        st.metric('Contact fraction',
                  f"{r2['contact_fraction']}%",
                  delta=f"+{delta2}%")
        st.caption('Scenario 2 (6 satellites)')
    with c3:
        delta3 = round(r3['contact_fraction'] - r1['contact_fraction'], 1)
        st.metric('Contact fraction',
                  f"{r3['contact_fraction']}%",
                  delta=f"+{delta3}% vs S1")
        st.caption('Scenario 3 (24 satellites)')

    improvement = round(r3['contact_fraction'] / max(r1['contact_fraction'], 0.1), 1)
    st.info(
        f"**Coverage improvement Scenario 1 → 3: {improvement}×** · "
        f"Mean simultaneous: {r1['mean_simultaneous']} → {r2['mean_simultaneous']} "
        f"→ {r3['mean_simultaneous']} · "
        f"Peak simultaneous: {r1['peak_simultaneous']} → {r2['peak_simultaneous']} "
        f"→ {r3['peak_simultaneous']}"
    )
else:
    st.info('Select Scenarios 1, 2, and 3 to see the progressive improvement comparison.')

st.divider()
st.caption(
    f'Computed for **{st.session_state.get("coverage_gs", gs_name)}** · '
    f'Window: **{st.session_state.get("coverage_window", window_min)} min** · '
    f'Epoch: {EPOCH.strftime("%Y-%m-%d %H:%M UTC")}'
)
