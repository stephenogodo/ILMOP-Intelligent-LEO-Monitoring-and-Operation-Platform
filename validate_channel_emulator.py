"""
validate_channel_emulator.py

Sprint 6 Deliverable 2 — Validation Script
============================================

Runs the OTFS LEO satellite channel emulator end-to-end over a real
orbital pass and prints a time-stamped table of all four channel
components.  Confirms the physics is correct by checking that:

  1. FSPL is lowest at maximum elevation (shortest range) and rises
     toward the horizon — expected ~155 dB at zenith, ~161 dB at 30°.

  2. Rician K-factor peaks near zenith and falls toward the horizon.

  3. Doppler shifts from negative (satellite approaching) through zero
     (nearest point) to positive (satellite receding) across the pass.

  4. SNR is highest near zenith and falls toward the horizon.

  5. Composite path gain magnitude |α(t)| is non-zero throughout.

Run from the ILMOP project root:

    python validate_channel_emulator.py

No external services (Kafka, TimescaleDB, Docker) are required.
"""

import math
import sys
from datetime import datetime, timezone

# ── path setup ───────────────────────────────────────────────────────────────
# Allow running from project root without installing the package.
sys.path.insert(0, '.')

from services.otfs.orbital_profile import OrbitalProfileExporter
from services.otfs.channel_emulator import ChannelEmulatorConfig, OTFSChannelEmulator

# ── configuration ─────────────────────────────────────────────────────────────

# Cambridge ground station (ILMOP default)
GROUND_LAT  =  52.205
GROUND_LON  =   0.119
GROUND_ALT  =  20.0    # metres

# 550 km LEO circular orbit, ISS-like inclination
ALTITUDE_KM =  550.0
INCL_DEG    =   51.6
RAAN_DEG    =   45.0   # chosen to produce a visible pass over Cambridge

# S-band downlink
CARRIER_HZ  = 2.4e9

# Simulation epoch — one full orbital period search window (192 minutes)
EPOCH       = datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc)
DURATION_S  = 192 * 60   # 2 × orbital period to guarantee a pass
STEP_S      = 10          # 10-second steps for readable output

# ── helpers ──────────────────────────────────────────────────────────────────

SEP  = '─' * 100
HSEP = '═' * 100

def header(title: str) -> None:
    print(f'\n{HSEP}')
    print(f'  {title}')
    print(HSEP)

def section(title: str) -> None:
    print(f'\n{SEP}')
    print(f'  {title}')
    print(SEP)

def pass_header() -> None:
    print(f"\n  {'Time (UTC)':<12} {'El°':>5} {'Az°':>6} "
          f"{'Range km':>9} {'FSPL dB':>8} {'K (lin)':>8} "
          f"{'Doppler Hz':>11} {'SNR dB':>7} {'|α|':>10}")
    print(f"  {'-'*11} {'-'*5} {'-'*6} "
          f"{'-'*9} {'-'*8} {'-'*8} "
          f"{'-'*11} {'-'*7} {'-'*10}")

# ── main validation ───────────────────────────────────────────────────────────

def main() -> None:

    header('ILMOP — Sprint 6 Deliverable 2 Validation')
    header('OTFS LEO Satellite Channel Emulator')
    print(f'\n  Ground station : Cambridge ({GROUND_LAT}°N, {GROUND_LON}°E, {GROUND_ALT} m)')
    print(f'  Orbit          : {ALTITUDE_KM} km circular, {INCL_DEG}° inclination')
    print(f'  Carrier        : {CARRIER_HZ/1e9:.1f} GHz (S-band)')
    print(f'  Epoch          : {EPOCH.isoformat()}')
    print(f'  Search window  : {DURATION_S//60} minutes at {STEP_S}-second steps')

    # ── Step 1: Build orbital profile ────────────────────────────────────────
    section('Step 1 — Building orbital profile (SGP4)')

    exporter = OrbitalProfileExporter(
        satellite_id      = 'SAT-A1',
        ground_lat_deg    = GROUND_LAT,
        ground_lon_deg    = GROUND_LON,
        ground_alt_m      = GROUND_ALT,
        altitude_km       = ALTITUDE_KM,
        inclination_deg   = INCL_DEG,
        raan_deg          = RAAN_DEG,
        mean_anomaly_deg  = 0.0,
        min_elevation_deg = 10.0,
        epoch             = EPOCH,
    )

    full_profile    = exporter.export(EPOCH, duration_s=DURATION_S, step_s=STEP_S)
    in_view_profile = [f for f in full_profile if f.in_view]

    print(f'\n  Total frames exported : {len(full_profile)}')
    print(f'  In-view frames (≥10°): {len(in_view_profile)}')

    if not in_view_profile:
        print('\n  ERROR: No in-view frames found in the search window.')
        print('  Try adjusting RAAN_DEG or extending DURATION_S.')
        sys.exit(1)

    pass_start = in_view_profile[0].timestamp
    pass_end   = in_view_profile[-1].timestamp
    duration   = (pass_end - pass_start).total_seconds()
    max_el_frame = max(in_view_profile, key=lambda f: f.elevation_deg)

    print(f'\n  Pass start     : {pass_start.strftime("%H:%M:%S")} UTC')
    print(f'  Pass end       : {pass_end.strftime("%H:%M:%S")} UTC')
    print(f'  Pass duration  : {duration:.0f} seconds ({duration/60:.1f} minutes)')
    print(f'  Max elevation  : {max_el_frame.elevation_deg:.1f}° at '
          f'{max_el_frame.timestamp.strftime("%H:%M:%S")} UTC')
    print(f'  Range at max el: {max_el_frame.range_m/1000:.1f} km')
    print('\n  ✓ Contact window identified from SGP4 orbital mechanics')

    # ── Step 2: Run channel emulator ─────────────────────────────────────────
    section('Step 2 — Running OTFS channel emulator (all 4 components)')

    config = ChannelEmulatorConfig(
        carrier_freq_hz  = CARRIER_HZ,
        bandwidth_hz     = 10e6,
        tx_power_w       = 5.0,
        tx_gain_dbi      = 6.0,
        rx_gain_dbi      = 3.0,
        noise_temp_k     = 290.0,
        atm_loss_db      = 0.5,
        k_factor_zenith  = 20.0,
        k_factor_horizon = 6.0,
        rng_seed         = 42,
    )

    emulator       = OTFSChannelEmulator(config)
    channel_frames = emulator.compute(in_view_profile)

    print(f'\n  Config:')
    print(f'    Carrier frequency : {CARRIER_HZ/1e9:.1f} GHz')
    print(f'    Bandwidth         : {config.bandwidth_hz/1e6:.0f} MHz')
    print(f'    Tx power          : {config.tx_power_w:.1f} W')
    print(f'    Tx gain           : {config.tx_gain_dbi:.1f} dBi')
    print(f'    Rx gain           : {config.rx_gain_dbi:.1f} dBi')
    print(f'    Noise temperature : {config.noise_temp_k:.0f} K')
    print(f'    Atm loss          : {config.atm_loss_db:.1f} dB')
    print(f'    K @ zenith        : {config.k_factor_zenith:.0f} dB')
    print(f'    K @ horizon (10°) : {config.k_factor_horizon:.0f} dB')
    print(f'\n  Channel frames computed: {len(channel_frames)}')
    print('\n  ✓ All four channel components computed per frame')

    # ── Step 3: Print channel table ──────────────────────────────────────────
    section('Step 3 — Channel profile across the pass')

    pass_header()
    for cf in channel_frames:
        t       = cf.timestamp.strftime('%H:%M:%S')
        el      = cf.elevation_deg
        az      = cf.azimuth_deg
        rng_km  = cf.range_m / 1000.0
        fspl    = cf.fspl_db
        k       = cf.k_factor
        dop     = cf.doppler_hz
        snr     = cf.snr_db
        alpha   = abs(cf.path_gain)
        print(f"  {t:<12} {el:>5.1f} {az:>6.1f} "
              f"{rng_km:>9.1f} {fspl:>8.2f} {k:>8.2f} "
              f"{dop:>+11.1f} {snr:>7.2f} {alpha:>10.3e}")

    # ── Step 4: Physics validation ───────────────────────────────────────────
    section('Step 4 — Physics validation checks')

    # Extract values for checks
    max_el_cf  = max(channel_frames, key=lambda f: f.elevation_deg)
    min_el_cf  = min(channel_frames, key=lambda f: f.elevation_deg)
    first_cf   = channel_frames[0]
    last_cf    = channel_frames[-1]
    mid_idx    = len(channel_frames) // 2
    mid_cf     = channel_frames[mid_idx]

    fspl_values   = [f.fspl_db     for f in channel_frames]
    k_values      = [f.k_factor    for f in channel_frames]
    doppler_vals  = [f.doppler_hz  for f in channel_frames]
    snr_values    = [f.snr_db      for f in channel_frames]

    results = []

    # Check 1: FSPL range
    fspl_at_max_el = max_el_cf.fspl_db
    fspl_at_min_el = min_el_cf.fspl_db
    ok = 150.0 <= fspl_at_max_el <= 158.0
    results.append((
        ok,
        f'FSPL at max elevation ({max_el_cf.elevation_deg:.1f}°) = '
        f'{fspl_at_max_el:.2f} dB  [expected 150–158 dB for 550 km LEO at S-band]'
    ))

    # Check 2: FSPL higher at lower elevation
    ok = fspl_at_min_el > fspl_at_max_el
    results.append((
        ok,
        f'FSPL at low elevation ({min_el_cf.elevation_deg:.1f}°) '
        f'{fspl_at_min_el:.2f} dB > FSPL at max elevation {fspl_at_max_el:.2f} dB  '
        f'[path loss grows toward horizon — correct]'
    ))

    # Check 3: K-factor monotone trend
    k_at_max = max_el_cf.k_factor
    k_at_min = min_el_cf.k_factor
    ok = k_at_max > k_at_min
    results.append((
        ok,
        f'Rician K at zenith-ish ({max_el_cf.elevation_deg:.1f}°) = {k_at_max:.2f} > '
        f'K at horizon ({min_el_cf.elevation_deg:.1f}°) = {k_at_min:.2f}  '
        f'[LOS strengthens at high elevation — correct]'
    ))

    # Check 4: Doppler sign change across pass
    has_neg    = any(d < -100 for d in doppler_vals)
    has_pos    = any(d >  100 for d in doppler_vals)
    ok = has_neg and has_pos
    results.append((
        ok,
        f'Doppler spans negative ({min(doppler_vals):+.0f} Hz) → positive '
        f'({max(doppler_vals):+.0f} Hz) across the pass  '
        f'[approaching then receding — correct]'
    ))

    # Check 5: Doppler magnitude within expected LEO S-band range
    max_abs_dop = max(abs(d) for d in doppler_vals)
    ok = max_abs_dop <= 65_000.0
    results.append((
        ok,
        f'Max |Doppler| = {max_abs_dop:.0f} Hz  '
        f'[expected ≤ 60,042 Hz for 7,500 m/s at S-band — correct]'
    ))

    # Check 6: SNR highest near zenith
    snr_at_max = max_el_cf.snr_db
    snr_at_min = min_el_cf.snr_db
    ok = snr_at_max > snr_at_min
    results.append((
        ok,
        f'SNR at max elevation = {snr_at_max:.2f} dB > '
        f'SNR at min elevation = {snr_at_min:.2f} dB  '
        f'[closer range → less path loss → better SNR — correct]'
    ))

    # Check 7: All path gains non-zero
    all_nonzero = all(abs(f.path_gain) > 0 for f in channel_frames)
    ok = all_nonzero
    results.append((
        ok,
        f'All {len(channel_frames)} path gain values |α(t)| > 0  '
        f'[channel always has a physical value — correct]'
    ))

    # Check 8: range_rate_ms preserved in ChannelFrame
    ok = all(f.range_rate_ms == orb.range_rate_ms
             for f, orb in zip(channel_frames, in_view_profile))
    results.append((
        ok,
        f'range_rate_ms preserved in all ChannelFrames  '
        f'[navigation validator can read Doppler pseudorange correction — correct]'
    ))

    print()
    all_passed = True
    for passed, message in results:
        icon = '✓' if passed else '✗'
        print(f'  {icon}  {message}')
        if not passed:
            all_passed = False

    # ── Step 5: Summary statistics ───────────────────────────────────────────
    section('Step 5 — Summary statistics')

    def stats(label, values, unit=''):
        mn  = min(values)
        mx  = max(values)
        avg = sum(values) / len(values)
        print(f'  {label:<28} min={mn:>9.2f}  max={mx:>9.2f}  '
              f'mean={avg:>9.2f}  {unit}')

    print()
    stats('FSPL',                   fspl_values,  'dB')
    stats('Rician K-factor (lin)',  k_values,     '')
    stats('Doppler shift',          doppler_vals, 'Hz')
    stats('SNR (pre-fading)',        snr_values,   'dB')
    stats('|path_gain|',
          [abs(f.path_gain) for f in channel_frames], '')

    # ── Final result ──────────────────────────────────────────────────────────
    section('Validation result')
    print()
    if all_passed:
        print(f'  ALL {len(results)}/{len(results)} PHYSICS CHECKS PASSED')
        print()
        print('  The OTFS channel emulator correctly implements all four')
        print('  components of the LEO satellite channel model:')
        print()
        print('    Component 1 — FSPL(t)          from ILMOP range')
        print('    Component 2 — Rician K(θ(t))   from ILMOP elevation')
        print('    Component 3 — Doppler ν(t)      from ILMOP range rate')
        print('    Component 4 — AWGN thermal noise from system config')
        print()
        print('  Combined path gain:')
        print('    α(t) = √(P_tx · G_tx · G_rx / FSPL(t)) · h_Rician(K(θ(t)))')
    else:
        failed = sum(1 for ok, _ in results if not ok)
        print(f'  {failed}/{len(results)} CHECKS FAILED — review output above')
        sys.exit(1)

    print(f'\n{HSEP}\n')


if __name__ == '__main__':
    main()
