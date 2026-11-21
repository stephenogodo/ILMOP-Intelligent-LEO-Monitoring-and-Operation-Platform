"""
tests/test_command_loop.py

End-to-end test of the whole command path (S8-03 to S8-06):

    author -> approve -> dispatch (at-most-once) -> satellite executes
           -> acknowledgement -> CONFIRMED

It uses a real TelemetryGenerator, so the command's effect is checked in
the telemetry records the simulator produces.
"""

import pytest

from shared.schemas.command_schema import Actor, ActorKind, CommandStatus
from services.command.service import CommandService
from services.command.uplink_bridge import run_uplink
from services.satellite_simulator.telemetry import TelemetryGenerator

OPERATOR = Actor(name="stephen", kind=ActorKind.OPERATOR)
UPLINK   = Actor(name="uplink-service", kind=ActorKind.SYSTEM)


@pytest.fixture
def generator():
    """A fresh simulated satellite, with saved sim state cleared before and after."""
    gen = TelemetryGenerator(satellite_id="SAT-001")
    gen.clear_state()
    yield gen
    gen.clear_state()


def _approved(service, command_type, params, satellite_id="SAT-001", key=None):
    """Submit and approve a command; return its ID."""
    result = service.submit(satellite_id, command_type, params, OPERATOR, key)
    service.approve(result.command.command_id, OPERATOR)
    return result.command.command_id


def test_full_loop_confirms_the_command(generator):
    service = CommandService()
    generator.satellite.in_contact = True
    cid = _approved(service, "SET_SAFE_MODE", {"enabled": True})

    result = run_uplink(service, cid, generator.command_receiver, UPLINK)

    assert result.sent is True
    stored = service.get(cid)
    assert stored.status is CommandStatus.CONFIRMED
    # The audit trail shows every stage, in order.
    assert [h.status for h in stored.history] == [
        CommandStatus.DRAFT, CommandStatus.APPROVED,
        CommandStatus.UPLINKED, CommandStatus.CONFIRMED,
    ]


def test_command_effect_appears_in_telemetry(generator):
    service = CommandService()
    generator.satellite.in_contact = True
    cid = _approved(service, "SET_SAFE_MODE", {"enabled": True})
    run_uplink(service, cid, generator.command_receiver, UPLINK)

    # The next telemetry record must report safe mode.
    assert generator.generate().safe_mode is True


def test_rejected_outside_contact_marks_the_command_failed(generator):
    service = CommandService()
    generator.satellite.in_contact = False
    cid = _approved(service, "SET_SAFE_MODE", {"enabled": True})

    result = run_uplink(service, cid, generator.command_receiver, UPLINK)

    assert result.sent is False
    stored = service.get(cid)
    assert stored.status is CommandStatus.FAILED
    assert "no contact" in stored.history[-1].reason
    assert generator.satellite.safe_mode is False


def test_resubmitted_command_succeeds_in_the_next_contact(generator):
    service = CommandService()
    generator.satellite.in_contact = False
    first = _approved(service, "SET_SAFE_MODE", {"enabled": True})
    run_uplink(service, first, generator.command_receiver, UPLINK)   # fails

    generator.satellite.in_contact = True                            # next pass
    second = _approved(service, "SET_SAFE_MODE", {"enabled": True})  # new command
    run_uplink(service, second, generator.command_receiver, UPLINK)

    assert service.get(first).status  is CommandStatus.FAILED
    assert service.get(second).status is CommandStatus.CONFIRMED
    assert generator.satellite.safe_mode is True


def test_repeated_uplink_attempt_executes_only_once(generator):
    service = CommandService()
    generator.satellite.in_contact = True
    cid = _approved(service, "REBOOT_SUBSYSTEM", {"subsystem": "OBC"})

    run_uplink(service, cid, generator.command_receiver, UPLINK)
    again = run_uplink(service, cid, generator.command_receiver, UPLINK)

    assert again.sent is False                                  # ground did not resend
    assert generator.command_receiver.reboot_counts == {"OBC": 1}


def test_retried_submission_does_not_create_a_second_command(generator):
    service = CommandService()
    a = service.submit("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 80}, OPERATOR, "req-7")
    b = service.submit("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 80}, OPERATOR, "req-7")
    assert b.created is False
    assert a.command.command_id == b.command.command_id


def test_downlink_rate_command_changes_later_telemetry(generator):
    service = CommandService()
    generator.satellite.in_contact = True
    cid = _approved(service, "SET_DOWNLINK_RATE", {"rate_mbps": 10.0})
    run_uplink(service, cid, generator.command_receiver, UPLINK)

    # The contact model now draws downlink rates around 10 Mbps, not 120.
    assert generator._contact.downlink_mean_mbps == 10.0


def test_command_for_another_satellite_is_refused(generator):
    service = CommandService()
    generator.satellite.in_contact = True
    cid = _approved(service, "SET_SAFE_MODE", {"enabled": True}, satellite_id="SAT-002")

    run_uplink(service, cid, generator.command_receiver, UPLINK)

    assert service.get(cid).status is CommandStatus.FAILED
    assert generator.satellite.safe_mode is False
