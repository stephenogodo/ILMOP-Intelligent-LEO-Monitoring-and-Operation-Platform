"""
tests/test_handover.py

Test suite for Layer 4 — Handover State Machine
(services/scheduler/handover.py)

Scenarios covered
-----------------
1.  Single window → IDLE → ACQUIRING → ACTIVE → RELEASED → IDLE
2.  Two windows with a gap → RELEASED/IDLE between them
3.  Two windows overlapping beyond margin → HANDING_OVER transition
4.  Two windows overlapping below margin → treated as gap
5.  Three windows, mixed gap + overlap
6.  No windows → empty event list
7.  Windows for mixed satellites → correct satellite filtered
8.  get_active_station — before first window
9.  get_active_station — during first window
10. get_active_station — in gap between windows
11. get_active_station — during handover overlap
12. get_active_station — after last window
13. build_constellation_handover_plans — returns one plan per satellite
14. Events are in strict chronological order
15. Plan stores the original contact_windows list
16. Overlap margin boundary: exactly at margin triggers HANDING_OVER
17. Single-window plan has exactly 5 events (IDLE/ACQUIRING/ACTIVE/RELEASED/IDLE)
18. Gap scenario: from_station and to_station correct in RELEASED event
"""

from __future__ import annotations

import pytest
from services.scheduler.contact_window import ContactWindow
from services.scheduler.geometry import GroundStation
from services.scheduler.handover import (
    HandoverState,
    HandoverEvent,
    HandoverPlan,
    build_handover_plan,
    build_constellation_handover_plans,
    get_active_station,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

T0 = 1_700_000_000.0   # arbitrary epoch

def make_station(name: str) -> GroundStation:
    return GroundStation(name=name, lat_deg=0.0, lon_deg=0.0, alt_km=0.0)


def make_window(
    sat: str,
    station: GroundStation,
    aos_offset: float,
    los_offset: float,
) -> ContactWindow:
    """Helper: ContactWindow with AOS/LOS relative to T0."""
    return ContactWindow(
        satellite_id = sat,
        station      = station,
        aos_unix     = T0 + aos_offset,
        los_unix     = T0 + los_offset,
        duration_s   = los_offset - aos_offset,
        max_elevation_deg = 45.0,
        scheduled    = True,
    )


GS_A = make_station("Alpha")
GS_B = make_station("Beta")
GS_C = make_station("Gamma")

SAT1 = "SAT-1"
SAT2 = "SAT-2"


# ---------------------------------------------------------------------------
# 1. Single window
# ---------------------------------------------------------------------------

class TestSingleWindow:
    """Five events: IDLE, ACQUIRING, ACTIVE, RELEASED, IDLE."""

    @pytest.fixture
    def plan(self):
        w = make_window(SAT1, GS_A, 0, 600)
        return build_handover_plan(SAT1, [w])

    def test_event_count(self, plan):
        assert len(plan.events) == 5   # Scenario 17

    def test_state_sequence(self, plan):
        states = [e.state for e in plan.events]
        assert states == [
            HandoverState.IDLE,
            HandoverState.ACQUIRING,
            HandoverState.ACTIVE,
            HandoverState.RELEASED,
            HandoverState.IDLE,
        ]   # Scenario 1

    def test_acquiring_to_station(self, plan):
        acq = next(e for e in plan.events if e.state == HandoverState.ACQUIRING)
        assert acq.to_station == GS_A

    def test_released_from_station(self, plan):
        rel = next(e for e in plan.events if e.state == HandoverState.RELEASED)
        assert rel.from_station == GS_A
        assert rel.to_station is None

    def test_events_chronological(self, plan):   # Scenario 14
        times = [e.epoch_unix for e in plan.events]
        assert times == sorted(times)


# ---------------------------------------------------------------------------
# 2. Two windows with a gap
# ---------------------------------------------------------------------------

class TestTwoWindowsGap:
    """Windows 0–600 s and 1200–1800 s → 600-s gap → no HANDING_OVER."""

    @pytest.fixture
    def plan(self):
        ws = [
            make_window(SAT1, GS_A, 0,    600),
            make_window(SAT1, GS_B, 1200, 1800),
        ]
        return build_handover_plan(SAT1, ws, overlap_margin_s=30.0)

    def test_no_handing_over(self, plan):   # Scenario 2
        states = [e.state for e in plan.events]
        assert HandoverState.HANDING_OVER not in states

    def test_released_after_first_window(self, plan):
        released_events = [e for e in plan.events if e.state == HandoverState.RELEASED]
        # First RELEASED should be at LOS of first window (T0+600)
        first_rel = released_events[0]
        assert first_rel.epoch_unix == pytest.approx(T0 + 600)
        assert first_rel.from_station == GS_A

    def test_acquiring_second_window(self, plan):
        acq_events = [e for e in plan.events if e.state == HandoverState.ACQUIRING]
        # Second ACQUIRING for GS_B at T0+1200
        second_acq = acq_events[-1]
        assert second_acq.to_station == GS_B
        assert second_acq.epoch_unix == pytest.approx(T0 + 1200)

    def test_final_state_idle(self, plan):
        assert plan.events[-1].state == HandoverState.IDLE

    def test_events_chronological(self, plan):
        times = [e.epoch_unix for e in plan.events]
        assert times == sorted(times)


# ---------------------------------------------------------------------------
# 3. Two windows overlapping beyond margin → HANDING_OVER
# ---------------------------------------------------------------------------

class TestTwoWindowsOverlapAboveMargin:
    """Windows 0–700 s and 600–1300 s → 100-s overlap > 30-s margin."""

    @pytest.fixture
    def plan(self):
        ws = [
            make_window(SAT1, GS_A, 0,   700),
            make_window(SAT1, GS_B, 600, 1300),
        ]
        return build_handover_plan(SAT1, ws, overlap_margin_s=30.0)

    def test_handing_over_present(self, plan):   # Scenario 3
        states = [e.state for e in plan.events]
        assert HandoverState.HANDING_OVER in states

    def test_handing_over_epoch(self, plan):
        ho = next(e for e in plan.events if e.state == HandoverState.HANDING_OVER)
        assert ho.epoch_unix == pytest.approx(T0 + 600)   # start of overlap

    def test_handing_over_stations(self, plan):
        ho = next(e for e in plan.events if e.state == HandoverState.HANDING_OVER)
        assert ho.from_station == GS_A
        assert ho.to_station == GS_B

    def test_active_after_handover(self, plan):
        ho_idx = next(
            i for i, e in enumerate(plan.events)
            if e.state == HandoverState.HANDING_OVER
        )
        after = plan.events[ho_idx + 1]
        assert after.state == HandoverState.ACTIVE
        assert after.to_station == GS_B
        assert after.epoch_unix == pytest.approx(T0 + 700)  # old LOS

    def test_events_chronological(self, plan):
        times = [e.epoch_unix for e in plan.events]
        assert times == sorted(times)


# ---------------------------------------------------------------------------
# 4. Two windows overlapping below margin → treated as gap
# ---------------------------------------------------------------------------

class TestTwoWindowsOverlapBelowMargin:
    """Windows 0–620 s and 600–1200 s → 20-s overlap < 30-s margin → gap."""

    @pytest.fixture
    def plan(self):
        ws = [
            make_window(SAT1, GS_A, 0,   620),
            make_window(SAT1, GS_B, 600, 1200),
        ]
        return build_handover_plan(SAT1, ws, overlap_margin_s=30.0)

    def test_no_handing_over(self, plan):   # Scenario 4
        states = [e.state for e in plan.events]
        assert HandoverState.HANDING_OVER not in states

    def test_released_present(self, plan):
        states = [e.state for e in plan.events]
        assert HandoverState.RELEASED in states


# ---------------------------------------------------------------------------
# 5. Three windows: gap then overlap
# ---------------------------------------------------------------------------

class TestThreeWindowsMixed:
    """
    Window A: 0–600  (GS_A)
    Window B: 1200–2000 (GS_B) — gap from A
    Window C: 1950–2500 (GS_C) — 50 s overlap with B > 30 s margin
    """

    @pytest.fixture
    def plan(self):
        ws = [
            make_window(SAT1, GS_A, 0,    600),
            make_window(SAT1, GS_B, 1200, 2000),
            make_window(SAT1, GS_C, 1950, 2500),
        ]
        return build_handover_plan(SAT1, ws, overlap_margin_s=30.0)

    def test_released_after_a(self, plan):   # Scenario 5
        released_events = [e for e in plan.events if e.state == HandoverState.RELEASED]
        assert any(e.from_station == GS_A for e in released_events)

    def test_handing_over_b_to_c(self, plan):
        ho_events = [e for e in plan.events if e.state == HandoverState.HANDING_OVER]
        assert len(ho_events) == 1
        assert ho_events[0].from_station == GS_B
        assert ho_events[0].to_station == GS_C

    def test_final_state_idle(self, plan):
        assert plan.events[-1].state == HandoverState.IDLE

    def test_events_chronological(self, plan):
        times = [e.epoch_unix for e in plan.events]
        assert times == sorted(times)


# ---------------------------------------------------------------------------
# 6. No windows → empty event list
# ---------------------------------------------------------------------------

def test_no_windows():   # Scenario 6
    plan = build_handover_plan(SAT1, [])
    assert plan.events == []
    assert plan.contact_windows == []


# ---------------------------------------------------------------------------
# 7. Mixed satellite windows → filter correct
# ---------------------------------------------------------------------------

def test_mixed_satellites_filtered():   # Scenario 7
    ws = [
        make_window(SAT1, GS_A, 0,    600),
        make_window(SAT2, GS_B, 0,    600),   # different sat
        make_window(SAT1, GS_A, 1200, 1800),
    ]
    plan = build_handover_plan(SAT1, ws)
    sat_ids = {w.satellite_id for w in plan.contact_windows}
    assert sat_ids == {SAT1}
    assert all(e.satellite_id == SAT1 for e in plan.events)


# ---------------------------------------------------------------------------
# 8–12. get_active_station queries
# ---------------------------------------------------------------------------

@pytest.fixture
def two_window_plan():
    """Two windows with gap: A=0–600, B=1200–1800."""
    ws = [
        make_window(SAT1, GS_A, 0,    600),
        make_window(SAT1, GS_B, 1200, 1800),
    ]
    return build_handover_plan(SAT1, ws, overlap_margin_s=30.0)


def test_active_station_before_first_window(two_window_plan):   # Scenario 8
    result = get_active_station(two_window_plan, T0 - 60)
    assert result is None


def test_active_station_during_first_window(two_window_plan):   # Scenario 9
    result = get_active_station(two_window_plan, T0 + 300)
    assert result == GS_A


def test_active_station_in_gap(two_window_plan):   # Scenario 10
    result = get_active_station(two_window_plan, T0 + 900)
    assert result is None


def test_active_station_during_second_window(two_window_plan):
    result = get_active_station(two_window_plan, T0 + 1500)
    assert result == GS_B


def test_active_station_after_last_window(two_window_plan):   # Scenario 12
    result = get_active_station(two_window_plan, T0 + 2000)
    assert result is None


def test_active_station_during_handover():   # Scenario 11
    """During HANDING_OVER the incoming station is reported."""
    ws = [
        make_window(SAT1, GS_A, 0,   700),
        make_window(SAT1, GS_B, 600, 1300),
    ]
    plan = build_handover_plan(SAT1, ws, overlap_margin_s=30.0)
    result = get_active_station(plan, T0 + 650)   # inside overlap (600–700)
    assert result == GS_B


# ---------------------------------------------------------------------------
# 13. build_constellation_handover_plans
# ---------------------------------------------------------------------------

def test_constellation_plans_keys():   # Scenario 13
    ws = [
        make_window(SAT1, GS_A, 0,   600),
        make_window(SAT2, GS_B, 100, 700),
    ]
    plans = build_constellation_handover_plans([SAT1, SAT2], ws)
    assert set(plans.keys()) == {SAT1, SAT2}
    assert isinstance(plans[SAT1], HandoverPlan)
    assert isinstance(plans[SAT2], HandoverPlan)


def test_constellation_plans_satellite_without_windows():
    ws = [make_window(SAT1, GS_A, 0, 600)]
    plans = build_constellation_handover_plans([SAT1, SAT2], ws)
    assert plans[SAT2].events == []


# ---------------------------------------------------------------------------
# 15. Plan stores original contact_windows
# ---------------------------------------------------------------------------

def test_plan_stores_contact_windows():   # Scenario 15
    ws = [
        make_window(SAT1, GS_A, 0, 600),
        make_window(SAT1, GS_B, 1200, 1800),
    ]
    plan = build_handover_plan(SAT1, ws)
    assert len(plan.contact_windows) == 2
    assert plan.contact_windows[0].station == GS_A


# ---------------------------------------------------------------------------
# 16. Overlap margin boundary — exactly at margin triggers HANDING_OVER
# ---------------------------------------------------------------------------

def test_overlap_exactly_at_margin():   # Scenario 16
    margin = 30.0
    # overlap of exactly 30 s (600 – 570 = 30)
    ws = [
        make_window(SAT1, GS_A, 0,   600),
        make_window(SAT1, GS_B, 570, 1200),
    ]
    plan = build_handover_plan(SAT1, ws, overlap_margin_s=margin)
    states = [e.state for e in plan.events]
    assert HandoverState.HANDING_OVER in states


# ---------------------------------------------------------------------------
# 18. RELEASED event from_station
# ---------------------------------------------------------------------------

def test_released_event_from_station():   # Scenario 18
    ws = [
        make_window(SAT1, GS_A, 0,    600),
        make_window(SAT1, GS_B, 1200, 1800),
    ]
    plan = build_handover_plan(SAT1, ws)
    released = [e for e in plan.events if e.state == HandoverState.RELEASED]
    # First RELEASED is from GS_A
    assert released[0].from_station == GS_A
    assert released[0].to_station is None
    # Final RELEASED is from GS_B
    assert released[-1].from_station == GS_B
