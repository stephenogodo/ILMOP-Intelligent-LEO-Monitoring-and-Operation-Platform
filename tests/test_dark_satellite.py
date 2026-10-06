"""
tests/test_dark_satellite.py

Test suite for Layer 5 — Dark Satellite Detection
(services/scheduler/dark_satellite.py)

Scenarios covered
-----------------
1.  is_dark — no windows at all → True
2.  is_dark — window fully inside horizon → False
3.  is_dark — window starts before epoch, ends inside horizon → False
4.  is_dark — window starts inside horizon, ends after → False
5.  is_dark — window exactly spans horizon → False
6.  is_dark — window entirely before epoch → True
7.  is_dark — window entirely after horizon end → True
8.  is_dark — window for different satellite ignored → True
9.  is_dark — zero-second horizon, window at epoch → False
10. find_dark_satellites — mixed fleet, some dark some not
11. find_dark_satellites — all dark
12. find_dark_satellites — none dark
13. find_dark_satellites — preserves satellite_ids order
14. compute_dark_intervals — no windows → one big dark interval
15. compute_dark_intervals — single window in middle → two dark intervals
16. compute_dark_intervals — window at horizon start → trailing dark only
17. compute_dark_intervals — window at horizon end → leading dark only
18. compute_dark_intervals — full coverage → no dark intervals
19. compute_dark_intervals — two windows with gap → dark intervals correct
20. compute_dark_intervals — overlapping windows are merged
21. compute_dark_intervals — dark interval duration_s correct
22. compute_dark_intervals — windows for other satellites ignored
23. compute_dark_intervals — horizon_end <= horizon_start → empty list
24. compute_dark_intervals — window extends beyond horizon clipped correctly
"""

from __future__ import annotations

import pytest
from services.scheduler.contact_window import ContactWindow
from services.scheduler.geometry import GroundStation
from services.scheduler.dark_satellite import (
    DarkInterval,
    is_dark,
    find_dark_satellites,
    compute_dark_intervals,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

T0 = 1_700_000_000.0  # arbitrary base epoch

GS_A = GroundStation(name="Alpha", lat_deg=51.5, lon_deg=-0.1, alt_km=0.0)
GS_B = GroundStation(name="Beta",  lat_deg=55.9, lon_deg=-3.2, alt_km=0.0)

SAT1 = "SAT-1"
SAT2 = "SAT-2"
SAT3 = "SAT-3"


def make_window(
    sat: str,
    station: GroundStation,
    aos_offset: float,
    los_offset: float,
) -> ContactWindow:
    return ContactWindow(
        satellite_id      = sat,
        station           = station,
        aos_unix          = T0 + aos_offset,
        los_unix          = T0 + los_offset,
        duration_s        = los_offset - aos_offset,
        max_elevation_deg = 45.0,
        scheduled         = True,
    )


# ---------------------------------------------------------------------------
# is_dark — point-in-time checks
# ---------------------------------------------------------------------------

def test_is_dark_no_windows():                          # Scenario 1
    assert is_dark(SAT1, T0, [], horizon_s=600) is True


def test_is_dark_window_fully_inside_horizon():         # Scenario 2
    w = make_window(SAT1, GS_A, 100, 400)
    assert is_dark(SAT1, T0, [w], horizon_s=600) is False


def test_is_dark_window_starts_before_epoch():          # Scenario 3
    w = make_window(SAT1, GS_A, -200, 300)
    assert is_dark(SAT1, T0, [w], horizon_s=600) is False


def test_is_dark_window_starts_inside_ends_after():     # Scenario 4
    w = make_window(SAT1, GS_A, 500, 900)
    assert is_dark(SAT1, T0, [w], horizon_s=600) is False


def test_is_dark_window_exactly_spans_horizon():        # Scenario 5
    w = make_window(SAT1, GS_A, 0, 600)
    assert is_dark(SAT1, T0, [w], horizon_s=600) is False


def test_is_dark_window_entirely_before_epoch():        # Scenario 6
    w = make_window(SAT1, GS_A, -500, -100)
    assert is_dark(SAT1, T0, [w], horizon_s=600) is True


def test_is_dark_window_entirely_after_horizon():       # Scenario 7
    w = make_window(SAT1, GS_A, 700, 1000)
    assert is_dark(SAT1, T0, [w], horizon_s=600) is True


def test_is_dark_different_satellite_ignored():         # Scenario 8
    w = make_window(SAT2, GS_A, 100, 400)
    assert is_dark(SAT1, T0, [w], horizon_s=600) is True


def test_is_dark_zero_horizon_window_at_epoch():        # Scenario 9
    # horizon_s=0 → horizon_end == epoch_unix; window must have los > epoch
    w = make_window(SAT1, GS_A, -10, 10)
    assert is_dark(SAT1, T0, [w], horizon_s=0) is False


# ---------------------------------------------------------------------------
# find_dark_satellites
# ---------------------------------------------------------------------------

def test_find_dark_mixed():                             # Scenario 10
    ws = [
        make_window(SAT1, GS_A, 100, 400),  # SAT1 visible
        make_window(SAT3, GS_B, 100, 400),  # SAT3 visible
        # SAT2 has no window → dark
    ]
    dark = find_dark_satellites([SAT1, SAT2, SAT3], T0, ws, horizon_s=600)
    assert dark == [SAT2]


def test_find_dark_all_dark():                          # Scenario 11
    dark = find_dark_satellites([SAT1, SAT2], T0, [], horizon_s=600)
    assert dark == [SAT1, SAT2]


def test_find_dark_none_dark():                         # Scenario 12
    ws = [
        make_window(SAT1, GS_A, 0, 600),
        make_window(SAT2, GS_B, 0, 600),
    ]
    dark = find_dark_satellites([SAT1, SAT2], T0, ws, horizon_s=600)
    assert dark == []


def test_find_dark_preserves_order():                   # Scenario 13
    # All dark; order must match satellite_ids
    ids = [SAT3, SAT1, SAT2]
    dark = find_dark_satellites(ids, T0, [], horizon_s=600)
    assert dark == ids


# ---------------------------------------------------------------------------
# compute_dark_intervals
# ---------------------------------------------------------------------------

HORIZON_START = T0
HORIZON_END   = T0 + 3600  # 1-hour horizon


def test_dark_intervals_no_windows():                   # Scenario 14
    intervals = compute_dark_intervals(SAT1, [], HORIZON_START, HORIZON_END)
    assert len(intervals) == 1
    assert intervals[0].start_unix == pytest.approx(HORIZON_START)
    assert intervals[0].end_unix   == pytest.approx(HORIZON_END)
    assert intervals[0].duration_s == pytest.approx(3600.0)


def test_dark_intervals_single_window_in_middle():      # Scenario 15
    ws = [make_window(SAT1, GS_A, 1200, 2400)]
    intervals = compute_dark_intervals(SAT1, ws, HORIZON_START, HORIZON_END)
    assert len(intervals) == 2
    assert intervals[0].end_unix   == pytest.approx(T0 + 1200)
    assert intervals[1].start_unix == pytest.approx(T0 + 2400)


def test_dark_intervals_window_at_start():              # Scenario 16
    ws = [make_window(SAT1, GS_A, 0, 1200)]
    intervals = compute_dark_intervals(SAT1, ws, HORIZON_START, HORIZON_END)
    assert len(intervals) == 1
    assert intervals[0].start_unix == pytest.approx(T0 + 1200)
    assert intervals[0].end_unix   == pytest.approx(HORIZON_END)


def test_dark_intervals_window_at_end():                # Scenario 17
    ws = [make_window(SAT1, GS_A, 2400, 3600)]
    intervals = compute_dark_intervals(SAT1, ws, HORIZON_START, HORIZON_END)
    assert len(intervals) == 1
    assert intervals[0].start_unix == pytest.approx(HORIZON_START)
    assert intervals[0].end_unix   == pytest.approx(T0 + 2400)


def test_dark_intervals_full_coverage():                # Scenario 18
    ws = [make_window(SAT1, GS_A, 0, 3600)]
    intervals = compute_dark_intervals(SAT1, ws, HORIZON_START, HORIZON_END)
    assert intervals == []


def test_dark_intervals_two_windows_with_gap():         # Scenario 19
    ws = [
        make_window(SAT1, GS_A, 0,    900),
        make_window(SAT1, GS_B, 1800, 3600),
    ]
    intervals = compute_dark_intervals(SAT1, ws, HORIZON_START, HORIZON_END)
    assert len(intervals) == 1
    assert intervals[0].start_unix == pytest.approx(T0 + 900)
    assert intervals[0].end_unix   == pytest.approx(T0 + 1800)
    assert intervals[0].duration_s == pytest.approx(900.0)


def test_dark_intervals_overlapping_windows_merged():   # Scenario 20
    # Windows 0–1200 and 900–2400 overlap; merged → 0–2400
    ws = [
        make_window(SAT1, GS_A, 0,    1200),
        make_window(SAT1, GS_B, 900,  2400),
    ]
    intervals = compute_dark_intervals(SAT1, ws, HORIZON_START, HORIZON_END)
    # Only one dark interval: 2400–3600
    assert len(intervals) == 1
    assert intervals[0].start_unix == pytest.approx(T0 + 2400)


def test_dark_intervals_duration_correct():             # Scenario 21
    ws = [make_window(SAT1, GS_A, 600, 1200)]
    intervals = compute_dark_intervals(SAT1, ws, HORIZON_START, HORIZON_END)
    total_dark = sum(i.duration_s for i in intervals)
    assert total_dark == pytest.approx(3600 - 600)   # 3000 s


def test_dark_intervals_other_satellites_ignored():     # Scenario 22
    ws = [make_window(SAT2, GS_A, 0, 3600)]  # full coverage for SAT2
    intervals = compute_dark_intervals(SAT1, ws, HORIZON_START, HORIZON_END)
    # SAT1 has no windows → full dark
    assert len(intervals) == 1
    assert intervals[0].duration_s == pytest.approx(3600.0)


def test_dark_intervals_invalid_horizon():              # Scenario 23
    intervals = compute_dark_intervals(SAT1, [], HORIZON_END, HORIZON_START)
    assert intervals == []


def test_dark_intervals_window_beyond_horizon_clipped(): # Scenario 24
    # Window extends well beyond horizon_end
    ws = [make_window(SAT1, GS_A, 1800, 7200)]
    intervals = compute_dark_intervals(SAT1, ws, HORIZON_START, HORIZON_END)
    assert len(intervals) == 1
    assert intervals[0].end_unix == pytest.approx(T0 + 1800)
