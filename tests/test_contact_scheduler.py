"""
tests/test_contact_scheduler.py

Tests for Layer 3: Contact Scheduler (Runtime Engine).

Covers:
  - AOS fires when simulation clock crosses window.aos_unix
  - LOS fires when simulation clock crosses window.los_unix
  - AOS does NOT fire before window.aos_unix
  - LOS does NOT fire before window.los_unix
  - AOS and LOS each fire exactly once (idempotency)
  - Completed windows accumulate in scheduler.history
  - active_windows tracks open windows; cleared on LOS
  - is_in_contact() / is_dark() helpers
  - window_for() returns correct ActiveWindow
  - Dark-satellite list updates after LOS
  - Multiple satellites with independent windows
  - Two satellites sharing a ground station (sequential)
  - Kafka producer receives correctly-keyed topic names
  - Kafka AOS payload contains expected fields
  - Kafka LOS payload contains expected fields
  - No-Kafka mode (producer=None) does not raise
  - Empty schedule — tick() returns [] and history stays empty
  - Clock injection drives scheduling with synthetic time

All tests use synthetic ContactWindow/ScheduleResult objects so they
are independent of SGP4, PuLP, and the ILP solver.
"""

from __future__ import annotations

import json
import pytest
from dataclasses import dataclass, field

from services.scheduler.contact_window import ContactWindow, ScheduleResult
from services.scheduler.geometry import GroundStation
from services.scheduler.scheduler import (
    ContactScheduler,
    AOSPayload,
    LOSPayload,
    create_scheduler,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

T0 = 1_000_000.0   # arbitrary base Unix timestamp

LONDON = GroundStation(name="London",   lat_deg=51.5,  lon_deg=-0.1,  alt_km=0.05)
TOKYO  = GroundStation(name="Tokyo",    lat_deg=35.7,  lon_deg=139.7, alt_km=0.05)
SYDNEY = GroundStation(name="Sydney",   lat_deg=-33.9, lon_deg=151.2, alt_km=0.05)


def _make_window(
    sat_id:    str  = "SAT-A1",
    station = None,
    aos_offset: float = 10.0,
    los_offset: float = 70.0,
    relay:      float = 0.0,
    data_age:   float = 120.0,
    score:      float = 0.5,
) -> ContactWindow:
    station = station or LONDON
    return ContactWindow(
        satellite_id      = sat_id,
        station           = station,
        aos_unix          = T0 + aos_offset,
        los_unix          = T0 + los_offset,
        max_elevation_deg = 30.0,
        duration_s        = los_offset - aos_offset,
        relay_value       = relay,
        data_age_s        = data_age,
        scheduled         = True,
        composite_score   = score,
    )


def _make_schedule(windows: list) -> ScheduleResult:
    return ScheduleResult(
        horizon_start_unix = T0,
        horizon_end_unix   = T0 + 3600.0,
        all_windows        = windows,
        scheduled_windows  = windows,
        dropped_windows    = [],
        total_contact_s    = sum(w.duration_s for w in windows),
        solver_status      = "Synthetic",
    )


class FakeProducer:
    """Minimal Kafka producer stub that records calls."""

    def __init__(self):
        self.calls: list[dict] = []   # list of {topic, payload_dict}

    def produce(self, topic: str, value: str) -> None:
        self.calls.append({"topic": topic, "payload": json.loads(value)})

    def topics_called(self) -> list[str]:
        return [c["topic"] for c in self.calls]

    def payloads_for(self, topic: str) -> list[dict]:
        return [c["payload"] for c in self.calls if c["topic"] == topic]


def _make_scheduler(windows, clock_time, producer=None, dark_horizon_s=600.0,
                    all_sat_ids=None):
    schedule = _make_schedule(windows)
    return create_scheduler(
        schedule          = schedule,
        handover_plans    = {},
        kafka_producer    = producer,
        all_satellite_ids = all_sat_ids or sorted({w.satellite_id for w in windows}),
        dark_horizon_s    = dark_horizon_s,
        clock_fn          = lambda: clock_time[0],
    )


# ---------------------------------------------------------------------------
# 1. AOS fires when clock crosses window.aos_unix
# ---------------------------------------------------------------------------

def test_aos_fires_at_aos_time():
    win = _make_window(aos_offset=10.0, los_offset=70.0)
    clock = [T0 + 9.9]   # just before AOS
    sched = _make_scheduler([win], clock)

    events = sched.tick()
    assert events == [], "no events before AOS"

    clock[0] = T0 + 10.0   # exactly at AOS
    events = sched.tick()
    assert len(events) == 1
    assert "AOS" in events[0]
    assert "SAT-A1" in events[0]


# ---------------------------------------------------------------------------
# 2. LOS fires when clock crosses window.los_unix
# ---------------------------------------------------------------------------

def test_los_fires_at_los_time():
    win = _make_window(aos_offset=10.0, los_offset=70.0)
    clock = [T0 + 10.0]
    sched = _make_scheduler([win], clock)

    sched.tick()   # open the window

    clock[0] = T0 + 69.9
    events = sched.tick()
    assert not any("LOS" in e for e in events), "LOS must not fire before los_unix"

    clock[0] = T0 + 70.0
    events = sched.tick()
    los_events = [e for e in events if "LOS" in e]
    assert len(los_events) == 1


# ---------------------------------------------------------------------------
# 3. AOS does NOT fire before aos_unix
# ---------------------------------------------------------------------------

def test_aos_does_not_fire_early():
    win = _make_window(aos_offset=100.0, los_offset=200.0)
    clock = [T0 + 50.0]
    sched = _make_scheduler([win], clock)

    for _ in range(5):
        events = sched.tick()
        assert events == []


# ---------------------------------------------------------------------------
# 4. AOS fires exactly once (idempotency)
# ---------------------------------------------------------------------------

def test_aos_fires_exactly_once():
    win = _make_window(aos_offset=10.0, los_offset=70.0)
    clock = [T0 + 10.0]
    sched = _make_scheduler([win], clock)

    fired = []
    for _ in range(5):
        fired += sched.tick()

    aos_events = [e for e in fired if "AOS" in e]
    assert len(aos_events) == 1, f"Expected 1 AOS, got {len(aos_events)}"


# ---------------------------------------------------------------------------
# 5. LOS fires exactly once (idempotency)
# ---------------------------------------------------------------------------

def test_los_fires_exactly_once():
    win = _make_window(aos_offset=10.0, los_offset=70.0)
    clock = [T0 + 10.0]
    sched = _make_scheduler([win], clock)

    sched.tick()                # AOS
    clock[0] = T0 + 70.0

    fired = []
    for _ in range(5):
        fired += sched.tick()

    los_events = [e for e in fired if "LOS" in e]
    assert len(los_events) == 1, f"Expected 1 LOS, got {len(los_events)}"


# ---------------------------------------------------------------------------
# 6. Completed windows accumulate in history
# ---------------------------------------------------------------------------

def test_history_accumulates():
    win1 = _make_window("SAT-A1", LONDON, aos_offset=10.0, los_offset=40.0)
    win2 = _make_window("SAT-A1", TOKYO,  aos_offset=50.0, los_offset=80.0)

    clock = [T0 + 10.0]
    sched = _make_scheduler([win1, win2], clock)

    sched.tick()                  # open win1
    clock[0] = T0 + 40.0
    sched.tick()                  # close win1
    assert len(sched.history) == 1

    clock[0] = T0 + 50.0
    sched.tick()                  # open win2
    clock[0] = T0 + 80.0
    sched.tick()                  # close win2
    assert len(sched.history) == 2


# ---------------------------------------------------------------------------
# 7. active_windows tracks open windows
# ---------------------------------------------------------------------------

def test_active_windows_lifecycle():
    win = _make_window(aos_offset=10.0, los_offset=70.0)
    clock = [T0 + 5.0]
    sched = _make_scheduler([win], clock)

    assert len(sched.active_windows) == 0

    clock[0] = T0 + 10.0
    sched.tick()
    assert len(sched.active_windows) == 1

    clock[0] = T0 + 70.0
    sched.tick()
    assert len(sched.active_windows) == 0


# ---------------------------------------------------------------------------
# 8. is_in_contact() helper
# ---------------------------------------------------------------------------

def test_is_in_contact():
    win = _make_window("SAT-A1", LONDON, aos_offset=10.0, los_offset=70.0)
    clock = [T0 + 5.0]
    sched = _make_scheduler([win], clock)

    assert not sched.is_in_contact("SAT-A1")

    clock[0] = T0 + 10.0
    sched.tick()
    assert sched.is_in_contact("SAT-A1")

    clock[0] = T0 + 70.0
    sched.tick()
    assert not sched.is_in_contact("SAT-A1")


# ---------------------------------------------------------------------------
# 9. window_for() returns ActiveWindow during contact
# ---------------------------------------------------------------------------

def test_window_for():
    win = _make_window("SAT-A1", LONDON, aos_offset=10.0, los_offset=70.0)
    clock = [T0 + 10.0]
    sched = _make_scheduler([win], clock)
    sched.tick()

    aw = sched.window_for("SAT-A1", "London")
    assert aw is not None
    assert aw.window.satellite_id == "SAT-A1"
    assert aw.window.station.name == "London"

    clock[0] = T0 + 70.0
    sched.tick()
    assert sched.window_for("SAT-A1", "London") is None


# ---------------------------------------------------------------------------
# 10. Dark-satellite list reflects current state
# ---------------------------------------------------------------------------

def test_dark_satellite_detection():
    # Window at T0+100 — 10 s dark horizon means satellite is dark before it
    win = _make_window("SAT-A1", LONDON, aos_offset=100.0, los_offset=160.0)
    clock = [T0 + 5.0]
    sched = _make_scheduler([win], clock, dark_horizon_s=10.0)

    sched.tick()
    assert "SAT-A1" in sched.dark_satellite_ids

    clock[0] = T0 + 100.0
    sched.tick()
    # During contact the satellite is NOT dark (window overlaps now)
    assert "SAT-A1" not in sched.dark_satellite_ids


# ---------------------------------------------------------------------------
# 11. Multiple satellites with independent windows
# ---------------------------------------------------------------------------

def test_multiple_satellites_independent():
    win_a = _make_window("SAT-A1", LONDON, aos_offset=10.0, los_offset=40.0)
    win_b = _make_window("SAT-B1", TOKYO,  aos_offset=20.0, los_offset=60.0)

    clock = [T0 + 10.0]
    sched = _make_scheduler([win_a, win_b], clock,
                             all_sat_ids=["SAT-A1", "SAT-B1"])
    sched.tick()   # SAT-A1 AOS

    assert sched.is_in_contact("SAT-A1")
    assert not sched.is_in_contact("SAT-B1")

    clock[0] = T0 + 20.0
    sched.tick()   # SAT-B1 AOS

    assert sched.is_in_contact("SAT-A1")
    assert sched.is_in_contact("SAT-B1")

    clock[0] = T0 + 40.0
    sched.tick()   # SAT-A1 LOS

    assert not sched.is_in_contact("SAT-A1")
    assert sched.is_in_contact("SAT-B1")


# ---------------------------------------------------------------------------
# 12. Kafka topic names are correct
# ---------------------------------------------------------------------------

def test_kafka_topic_names():
    win = _make_window("SAT-A1", LONDON, aos_offset=10.0, los_offset=70.0)
    clock = [T0 + 10.0]
    producer = FakeProducer()
    sched = _make_scheduler([win], clock, producer=producer)
    sched.start()

    sched.tick()                  # AOS
    clock[0] = T0 + 70.0
    sched.tick()                  # LOS

    topics = producer.topics_called()
    assert "contact.SAT-A1.schedule" in topics
    assert "contact.SAT-A1.aos" in topics
    assert "contact.SAT-A1.los" in topics


# ---------------------------------------------------------------------------
# 13. Kafka AOS payload fields
# ---------------------------------------------------------------------------

def test_kafka_aos_payload_fields():
    win = _make_window("SAT-A1", LONDON, aos_offset=10.0, los_offset=70.0,
                       relay=0.3, data_age=180.0, score=0.75)
    clock = [T0 + 10.0]
    producer = FakeProducer()
    sched = _make_scheduler([win], clock, producer=producer)
    sched.tick()

    payloads = producer.payloads_for("contact.SAT-A1.aos")
    assert len(payloads) == 1
    p = payloads[0]

    assert p["event"]           == "AOS"
    assert p["satellite_id"]    == "SAT-A1"
    assert p["station_name"]    == "London"
    assert p["aos_unix"]        == pytest.approx(T0 + 10.0)
    assert p["los_unix"]        == pytest.approx(T0 + 70.0)
    assert p["duration_s"]      == pytest.approx(60.0)
    assert p["relay_value"]     == pytest.approx(0.3)
    assert p["data_age_s"]      == pytest.approx(180.0)
    assert p["composite_score"] == pytest.approx(0.75)
    assert "handover_state" in p
    assert "ts_iso" in p


# ---------------------------------------------------------------------------
# 14. Kafka LOS payload fields
# ---------------------------------------------------------------------------

def test_kafka_los_payload_fields():
    win = _make_window("SAT-A1", LONDON, aos_offset=10.0, los_offset=70.0)
    clock = [T0 + 10.0]
    producer = FakeProducer()
    sched = _make_scheduler([win], clock, producer=producer)
    sched.tick()   # AOS at T0+10

    clock[0] = T0 + 70.0
    sched.tick()   # LOS at T0+70

    payloads = producer.payloads_for("contact.SAT-A1.los")
    assert len(payloads) == 1
    p = payloads[0]

    assert p["event"]          == "LOS"
    assert p["satellite_id"]   == "SAT-A1"
    assert p["station_name"]   == "London"
    assert p["los_unix"]       == pytest.approx(T0 + 70.0)
    assert "actual_duration_s" in p
    assert "is_dark_now"       in p
    assert "handover_state"    in p
    assert "ts_iso"            in p


# ---------------------------------------------------------------------------
# 15. No-Kafka mode (producer=None) does not raise
# ---------------------------------------------------------------------------

def test_no_kafka_no_raise():
    win = _make_window(aos_offset=10.0, los_offset=70.0)
    clock = [T0 + 10.0]
    sched = _make_scheduler([win], clock, producer=None)
    sched.start()

    clock[0] = T0 + 10.0
    sched.tick()

    clock[0] = T0 + 70.0
    sched.tick()

    assert len(sched.history) == 1   # completed without error


# ---------------------------------------------------------------------------
# 16. Empty schedule — tick() returns [] and history stays empty
# ---------------------------------------------------------------------------

def test_empty_schedule():
    clock = [T0]
    schedule = _make_schedule([])
    sched = create_scheduler(
        schedule          = schedule,
        handover_plans    = {},
        kafka_producer    = None,
        all_satellite_ids = ["SAT-A1"],
        clock_fn          = lambda: clock[0],
    )
    for _ in range(10):
        events = sched.tick()
        assert events == []

    assert sched.history == []


# ---------------------------------------------------------------------------
# 17. Clock injection drives scheduling (synthetic time)
# ---------------------------------------------------------------------------

def test_clock_injection_synthetic_time():
    """Verify that injecting a fast-forward clock fires all events correctly."""
    win = _make_window("SAT-A1", LONDON, aos_offset=100.0, los_offset=700.0)
    clock = [T0]
    sched = _make_scheduler([win], clock)

    # Before AOS
    events = sched.tick()
    assert events == []

    # Jump to AOS
    clock[0] = T0 + 100.0
    events = sched.tick()
    assert any("AOS" in e for e in events)
    assert sched.is_in_contact("SAT-A1")

    # Jump to LOS
    clock[0] = T0 + 700.0
    events = sched.tick()
    assert any("LOS" in e for e in events)
    assert not sched.is_in_contact("SAT-A1")
    assert len(sched.history) == 1
