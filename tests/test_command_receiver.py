"""
tests/test_command_receiver.py

Tests for the simulated satellite's command receiver (S8-06).
The receiver is tested on its own, using a real Satellite object and a
tiny stand-in for the contact model, so no simulator run is needed.
"""

from types import SimpleNamespace

from shared.schemas.command_schema import Actor, ActorKind, CommandStatus
from services.command import lifecycle
from services.command.authoring import author_command
from services.satellite_simulator.command_receiver import CommandReceiver
from services.satellite_simulator.satellite import Satellite

OPERATOR = Actor(name="stephen", kind=ActorKind.OPERATOR)
UPLINK   = Actor(name="uplink-service", kind=ActorKind.SYSTEM)


def _receiver(in_contact=True, satellite_id="SAT-001"):
    """A receiver on a satellite that is (or is not) in contact."""
    sat = Satellite(satellite_id=satellite_id)
    sat.in_contact = in_contact
    contact = SimpleNamespace(downlink_mean_mbps=120.0)
    return CommandReceiver(sat, contact), sat, contact


def _uplinked(command_type="SET_SAFE_MODE", params=None, satellite_id="SAT-001"):
    """A command that has been authored, approved and marked UPLINKED."""
    params = {"enabled": True} if params is None else params
    cmd = author_command(satellite_id, command_type, params, OPERATOR)
    return lifecycle.mark_uplinked(lifecycle.approve(cmd, OPERATOR), UPLINK)


class TestAcceptance:

    def test_valid_command_in_contact_is_accepted_and_executed(self):
        rx, _, _ = _receiver()
        ack = rx.receive(_uplinked())
        assert ack.accepted and ack.executed and ack.reason == "executed"

    def test_ack_identifies_command_and_satellite(self):
        rx, _, _ = _receiver()
        cmd = _uplinked()
        ack = rx.receive(cmd)
        assert ack.command_id == cmd.command_id
        assert ack.satellite_id == "SAT-001"


class TestRejection:

    def test_rejected_when_not_in_contact(self):
        rx, sat, _ = _receiver(in_contact=False)
        ack = rx.receive(_uplinked())
        assert not ack.accepted and ack.reason == "no contact"
        assert sat.safe_mode is False          # nothing was executed

    def test_rejected_when_addressed_to_another_satellite(self):
        rx, _, _ = _receiver()
        ack = rx.receive(_uplinked(satellite_id="SAT-002"))
        assert not ack.accepted and ack.reason == "wrong satellite"

    def test_rejected_when_not_uplinked_status(self):
        # A command that skipped approval and uplink must be refused.
        rx, sat, _ = _receiver()
        draft = author_command("SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR)
        assert draft.status is CommandStatus.DRAFT
        ack = rx.receive(draft)
        assert not ack.accepted and "not UPLINKED" in ack.reason
        assert sat.safe_mode is False

    def test_rejected_unknown_command_type(self):
        rx, _, _ = _receiver()
        cmd = _uplinked()
        bogus = cmd.model_copy(update={"command_type": "SELF_DESTRUCT"})
        ack = rx.receive(bogus)
        assert not ack.accepted and "unknown command type" in ack.reason

    def test_rejection_is_not_remembered_so_a_resend_can_succeed(self):
        rx, sat, _ = _receiver(in_contact=False)
        cmd = _uplinked()
        assert not rx.receive(cmd).accepted
        sat.in_contact = True                  # contact established
        assert rx.receive(cmd).executed        # same command now runs


class TestExecution:

    def test_safe_mode_is_set(self):
        rx, sat, _ = _receiver()
        rx.receive(_uplinked("SET_SAFE_MODE", {"enabled": True}))
        assert sat.safe_mode is True

    def test_safe_mode_can_be_cleared(self):
        rx, sat, _ = _receiver()
        sat.safe_mode = True
        rx.receive(_uplinked("SET_SAFE_MODE", {"enabled": False}))
        assert sat.safe_mode is False

    def test_downlink_rate_changes_the_contact_model(self):
        rx, _, contact = _receiver()
        rx.receive(_uplinked("SET_DOWNLINK_RATE", {"rate_mbps": 75.0}))
        assert contact.downlink_mean_mbps == 75.0

    def test_telemetry_interval_is_stored(self):
        rx, _, _ = _receiver()
        rx.receive(_uplinked("SET_TELEMETRY_INTERVAL", {"interval_s": 10}))
        assert rx.telemetry_interval_s == 10.0

    def test_reboots_are_counted_per_subsystem(self):
        rx, _, _ = _receiver()
        rx.receive(_uplinked("REBOOT_SUBSYSTEM", {"subsystem": "ADCS"}))
        rx.receive(_uplinked("REBOOT_SUBSYSTEM", {"subsystem": "ADCS"}))
        rx.receive(_uplinked("REBOOT_SUBSYSTEM", {"subsystem": "EPS"}))
        assert rx.reboot_counts == {"ADCS": 2, "EPS": 1}


class TestDuplicateGuard:

    def test_same_command_is_executed_only_once(self):
        # One command delivered three times must run only once.
        rx, _, _ = _receiver()
        cmd = _uplinked("REBOOT_SUBSYSTEM", {"subsystem": "OBC"})
        rx.receive(cmd)
        rx.receive(cmd)
        rx.receive(cmd)
        assert rx.reboot_counts == {"OBC": 1}

    def test_repeat_returns_the_original_acknowledgement(self):
        rx, _, _ = _receiver()
        cmd = _uplinked()
        first, second = rx.receive(cmd), rx.receive(cmd)
        assert second == first
        assert second.executed is True          # it is the FIRST answer, replayed

    def test_repeat_is_counted_as_ignored(self):
        rx, _, _ = _receiver()
        cmd = _uplinked()
        rx.receive(cmd)
        rx.receive(cmd)
        assert rx.duplicates_ignored == 1

    def test_repeat_is_ignored_even_after_contact_is_lost(self):
        rx, sat, _ = _receiver()
        cmd = _uplinked()
        first = rx.receive(cmd)
        sat.in_contact = False
        assert rx.receive(cmd) == first         # still answered from memory
