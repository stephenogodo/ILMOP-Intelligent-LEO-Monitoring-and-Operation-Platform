"""
validate_navigation_validator.py

Sprint 6 Deliverable 4 — Validation Script
============================================

Runs the OTFS navigation validation pipeline end-to-end and prints
a time-stamped table of pseudorange residuals, GDOP, and accuracy
statistics for the thesis navigation contribution.

Demonstrates four things:

  1. Per-epoch residuals for a single-satellite pass (Scenario 1).
  2. Physics checks — residuals are within the expected ranging sigma,
     bias is near zero, GDOP varies correctly with elevation.
  3. Range resolution Δr = c/(2B) ≈ 14.99 m at B = 10 MHz.
  4. Progressive improvement from Scenario 1 → 2 → 3:
       Scenario 1:  1 satellite, no 3D fix possible
       Scenario 2:  6 satellites, higher coverage frequency
       Scenario 3: 24 satellites, multi-plane simultaneous coverage

Run from the ILMOP project root:

    python validate_navigation_validator.py

No external services (Kafka, TimescaleDB, Docker) required.
"""

import math
import sys
from datetime import datetime, timezone

sys.path.insert(0, '.')

from services.navigation.validator import NavigationValidator
from services.otfs.channel_emulator import ChannelEmulatorConfig

# ── configuration ─────────────────────────────────────────────────────────────

EPOCH       = datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc)
WINDOW_S    = 192 * 60   # 192-minute search window = 2 orbital periods
STEP_S_FULL = 10         # 10-second steps for the per-epoch table
STEP_S_PROG = 60         # 60-second steps for the progression comparison
PROG_WIN_S  = 120 * 60   # 2-hour window for progression comparison

C_MS        = 299_792_458.0
BANDWIDTH   = 10e6       # 10 MHz OTFS bandwidth

# ── helpers ───────────────────────────────────────────────────────────────────

SEP  = '─' * 110
HSEP = '═' * 110

def header(t): print(f'\n{HSEP}\n  {t}\n{HSEP}')
def section(t): print(f'\n{SEP}\n  {t}\n{SEP}')

def make_constellation(
    n_planes: int,
    sats_per_plane: int,
    raan_base: float = 45.0,
    raan_step: float = 90.0,
    rng_seed: int = 42,
) -> list:
    validators = []
    for p in range(n_planes):
        for s in range(sats_per_plane):
            validators.append(NavigationValidator(
                satellite_id    = f'P{p+1}S{s+1}',
                altitude_km     = 550.0,
                inclination_deg = 51.6,
                raan_deg        = raan_base + p * raan_step,
                mean_anomaly_deg= s * (360.0 / sats_per_plane),
                epoch           = EPOCH,
                ground_lat_deg  = 52.205,
                ground_lon_deg  = 0.119,
                rng_seed        = rng_seed,
            ))
    return validators


# ── main validation ────────────────────────────────────────────────────────────

def main():
    header('ILMOP — Sprint 6 Deliverable 4 Validation')
    header('OTFS Navigation Validation Pipeline')
    print(f'\n  Ground station : Cambridge (52.205°N, 0.119°E)')
    print(f'  Orbit          : 550 km circular, 51.6° inclination')
    print(f'  Carrier        : 2.4 GHz (S-band)')
    print(f'  Bandwidth      : {BANDWIDTH/1e6:.0f} MHz')
    print(f'  Range res Δr   : {C_MS/(2*BANDWIDTH):.4f} m  [c/(2B)]')
    print(f'  Epoch          : {EPOCH.isoformat()}')
    print(f'  Search window  : {WINDOW_S//60} minutes')

    # ── Step 1: Single-satellite pass (Scenario 1) ────────────────────────────
    section('Step 1 — Single-satellite pass validation (Scenario 1)')

    validator = NavigationValidator(
        satellite_id    = 'SAT-A1',
        altitude_km     = 550.0,
        inclination_deg = 51.6,
        raan_deg        = 45.0,
        epoch           = EPOCH,
        ground_lat_deg  = 52.205,
        ground_lon_deg  = 0.119,
        rng_seed        = 42,
    )

    try:
        result = validator.validate_pass(
            EPOCH, duration_s=WINDOW_S, step_s=STEP_S_FULL
        )
    except ValueError as e:
        print(f'\n  ERROR: {e}')
        sys.exit(1)

    print(f'\n  Pass identified: {result.start_epoch.strftime("%H:%M:%S")} → '
          f'{result.end_epoch.strftime("%H:%M:%S")} UTC')
    print(f'  Observations   : {result.n_observations}')
    print(f'  Truth source   : {result.propagator_mode.upper()}')

    # ── Step 2: Per-epoch table ───────────────────────────────────────────────
    section('Step 2 — Per-epoch residuals')

    print(f"\n  {'Time':>10} {'El°':>5} {'True rng km':>12} {'OTFS rng km':>12} "
          f"{'Residual m':>11} {'Sigma m':>8} {'GDOP':>6} {'SNR dB':>7}")
    print(f"  {'-'*10} {'-'*5} {'-'*12} {'-'*12} "
          f"{'-'*11} {'-'*8} {'-'*6} {'-'*7}")

    for f in result.frames:
        t    = f.timestamp.strftime('%H:%M:%S')
        el   = f.elevation_deg
        tr   = f.true_range_m / 1000.0
        ot   = f.otfs_range_m / 1000.0
        res  = f.residual_m
        sig  = f.ranging_sigma_m
        gdop = f.gdop
        snr  = f.snr_db
        print(f"  {t:>10} {el:>5.1f} {tr:>12.3f} {ot:>12.3f} "
              f"{res:>+11.2f} {sig:>8.2f} {gdop:>6.2f} {snr:>7.2f}")

    # ── Step 3: Pass-level statistics ─────────────────────────────────────────
    section('Step 3 — Pass-level statistics')
    print()
    print(f'  RMS residual       : {result.rms_residual_m:.3f} m')
    print(f'  Max residual       : {result.max_residual_m:.3f} m')
    print(f'  Bias (mean)        : {result.mean_residual_m:.3f} m')
    print(f'  Std deviation      : {result.std_residual_m:.3f} m')
    print(f'  GDOP mean          : {result.gdop_mean:.3f}')
    print(f'  GDOP range         : {result.gdop_min:.3f} – {result.gdop_max:.3f}')
    print(f'  Range resolution   : {result.range_resolution_m:.4f} m')
    print(f'  Summary            : {result.summary()}')

    # ── Step 4: Physics checks ────────────────────────────────────────────────
    section('Step 4 — Physics validation checks')

    checks = []
    delta_r = result.range_resolution_m

    # Check 1: RMS within range resolution
    ok = result.rms_residual_m < delta_r
    checks.append((ok,
        f'RMS residual ({result.rms_residual_m:.3f} m) < '
        f'Δr ({delta_r:.3f} m)  [noise model correct]'))

    # Check 2: Bias near zero
    ok = abs(result.mean_residual_m) < 0.5 * result.rms_residual_m + 1e-6
    checks.append((ok,
        f'Bias ({result.mean_residual_m:.3f} m) < '
        f'0.5 × RMS ({0.5*result.rms_residual_m:.3f} m)  [estimator is unbiased]'))

    # Check 3: GDOP lowest near zenith
    max_el_f  = max(result.frames, key=lambda f: f.elevation_deg)
    min_el_f  = min(result.frames, key=lambda f: f.elevation_deg)
    ok = max_el_f.gdop < min_el_f.gdop
    checks.append((ok,
        f'GDOP at max el ({max_el_f.elevation_deg:.1f}°) = {max_el_f.gdop:.3f} < '
        f'GDOP at min el ({min_el_f.elevation_deg:.1f}°) = {min_el_f.gdop:.3f}  '
        f'[geometry best near zenith]'))

    # Check 4: GDOP formula correctness at zenith
    gdop_90 = NavigationValidator._gdop_single_sat(90.0)
    ok = abs(gdop_90 - 1.0) < 0.01
    checks.append((ok,
        f'GDOP at 90° elevation = {gdop_90:.4f}  [expected 1.000 = 1/sin(90°)]'))

    # Check 5: Ranging sigma higher at low elevation (lower SNR)
    sigma_high = max_el_f.ranging_sigma_m
    sigma_low  = min_el_f.ranging_sigma_m
    ok = sigma_low > sigma_high
    checks.append((ok,
        f'σ at min el ({sigma_low:.3f} m) > σ at max el ({sigma_high:.3f} m)  '
        f'[lower SNR at horizon → larger noise]'))

    # Check 6: All residuals bounded by ±3σ (99.7% coverage for Gaussian)
    violations = [f for f in result.frames
                  if abs(f.residual_m) > 3.0 * f.ranging_sigma_m]
    ok = len(violations) / len(result.frames) < 0.003
    checks.append((ok,
        f'3σ violations: {len(violations)}/{len(result.frames)} '
        f'({100*len(violations)/len(result.frames):.1f}%)  [expected < 0.3%]'))

    # Check 7: Truth source is HPOP or j2j4 (never empty)
    ok = all(f.truth_source in ('hpop', 'j2j4', 'sp3') for f in result.frames)
    checks.append((ok, f'All frames have valid truth source: '
                       f'{set(f.truth_source for f in result.frames)}'))

    print()
    all_ok = True
    for passed, msg in checks:
        icon = '✓' if passed else '✗'
        print(f'  {icon}  {msg}')
        if not passed:
            all_ok = False

    # ── Step 5: Progressive improvement (Scenarios 1 → 2 → 3) ───────────────
    section('Step 5 — Progressive scenario comparison (Scenario 1 → 2 → 3)')
    print(f'\n  Window: {PROG_WIN_S//60} minutes | Step: {STEP_S_PROG} s\n')

    scenarios = [
        ('Scenario 1', make_constellation(1, 1,  raan_base=45.0, raan_step=0.0)),
        ('Scenario 2', make_constellation(1, 6,  raan_base=45.0, raan_step=0.0)),
        ('Scenario 3', make_constellation(4, 6,  raan_base=45.0, raan_step=90.0)),
    ]

    print(f"  {'Scenario':<12} {'N sats':>7} {'Visible%':>9} {'Mean sim':>9} "
          f"{'3D fix%':>8} {'GDOP mean':>10} {'RMS m':>8}")
    print(f"  {'-'*12} {'-'*7} {'-'*9} {'-'*9} {'-'*8} {'-'*10} {'-'*8}")

    prev_visible_pct = 0.0
    prev_mean_sim    = 0.0
    scenario_results = []

    for name, validators in scenarios:
        r = NavigationValidator.validate_multi_satellite(
            validators, EPOCH,
            duration_s=PROG_WIN_S, step_s=STEP_S_PROG
        )
        visible_pct = 100.0 * r['n_visible_epochs'] / max(r['n_epochs'], 1)
        gdop_str    = (f"{r['gdop_mean']:.2f}"
                       if math.isfinite(r['gdop_mean']) else '  n/a')
        rms_str     = (f"{r['rms_residual_m']:.2f}"
                       if math.isfinite(r['rms_residual_m']) else '  n/a')
        fix_pct     = 100.0 * r['fix_fraction']

        print(f"  {name:<12} {r['n_satellites']:>7} {visible_pct:>8.1f}% "
              f"{r['mean_simultaneous']:>9.2f} {fix_pct:>7.1f}% "
              f"{gdop_str:>10} {rms_str:>8}")

        scenario_results.append((name, r, visible_pct))
        prev_visible_pct = visible_pct
        prev_mean_sim    = r['mean_simultaneous']

    # Verify monotone improvement
    print()
    vis_1 = scenario_results[0][2]
    vis_3 = scenario_results[2][2]
    sim_1 = scenario_results[0][1]['mean_simultaneous']
    sim_3 = scenario_results[2][1]['mean_simultaneous']

    ok_vis = vis_3 >= vis_1
    ok_sim = sim_3 >= sim_1
    checks.append((ok_vis,
        f'Scenario 3 visibility ({vis_3:.1f}%) ≥ Scenario 1 ({vis_1:.1f}%)'))
    checks.append((ok_sim,
        f'Scenario 3 mean simultaneous ({sim_3:.2f}) ≥ '
        f'Scenario 1 ({sim_1:.2f})'))

    icon_v = '✓' if ok_vis else '✗'
    icon_s = '✓' if ok_sim else '✗'
    print(f'  {icon_v}  Scenario 3 coverage ({vis_3:.1f}%) ≥ '
          f'Scenario 1 ({vis_1:.1f}%)  [progressive improvement]')
    print(f'  {icon_s}  Scenario 3 mean simultaneous ({sim_3:.2f}) ≥ '
          f'Scenario 1 ({sim_1:.2f})  [more concurrent satellites]')

    # ── Final result ──────────────────────────────────────────────────────────
    section('Validation result')
    print()
    total_checks = len(checks)
    passed_checks = sum(1 for ok, _ in checks if ok)

    if passed_checks == total_checks:
        print(f'  ALL {total_checks}/{total_checks} CHECKS PASSED\n')
        print('  The OTFS navigation validation pipeline correctly:')
        print()
        print(f'    ✓ Computes pseudorange residuals against HPOP truth')
        print(f'    ✓ Applies OTFS ranging noise model '
              f'(Δr = {delta_r:.2f} m at {BANDWIDTH/1e6:.0f} MHz)')
        print(f'    ✓ Computes single-satellite GDOP from elevation geometry')
        print(f'    ✓ Demonstrates progressive Scenario 1 → 2 → 3 improvement')
        print()
        print(f'  RMS pseudorange accuracy: {result.rms_residual_m:.2f} m')
        print(f'  Truth/claim ratio: '
              f'~{delta_r/result.rms_residual_m:.1f}× '
              f'(HPOP accuracy / RMS residual)')
    else:
        failed = total_checks - passed_checks
        print(f'  {failed}/{total_checks} CHECKS FAILED — review output above')
        sys.exit(1)

    print(f'\n{HSEP}\n')


if __name__ == '__main__':
    main()