"""
tests/test_command_service.py

Tests for exactly-once command handling (S8-05):
idempotent submission and the at-most-once uplink guard.
"""

import threading

import pytest

from shared.schemas.command_schema import Actor, ActorKind, CommandStatus
from services.command.authoring import CommandValidationError
from services.command.lifecycle import ApprovalError, InvalidTransitionError
from services.command.service import (
    CommandNotFoundError,
    CommandService,
    IdempotencyConflictError,
    request_fingerprint,
)

OPERATOR = Actor(name="stephen", kind=ActorKind.OPERATOR)
UPLINK   = Actor(name="uplink-service", kind=ActorKind.SYSTEM)


class RecordingSender:
    """A fake uplink that remembers every command it was asked to send."""

    def __init__(self, fail_with=None):
        self.sent = []
        self.fail_with = fail_with

    def __call__(self, command):
        if self.fail_with:
            raise self.fail_with
        self.sent.append(command.command_id)


def _approved(service, key=None):
    """Submit and approve a command; return its ID."""
    result = service.submit(
        "SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 50}, OPERATOR,
        idempotency_key=key,
    )
    service.approve(result.command.command_id, OPERATOR)
    return result.command.command_id


# ── Fingerprint ───────────────────────────────────────────────────────────────

class TestFingerprint:

    def test_same_content_same_fingerprint(self):
        a = request_fingerprint("SAT-001", "SET_SAFE_MODE", {"enabled": True})
        b = request_fingerprint("SAT-001", "SET_SAFE_MODE", {"enabled": True})
        assert a == b

    def test_parameter_order_does_not_matter(self):
        a = request_fingerprint("SAT-001", "X", {"a": 1, "b": 2})
        b = request_fingerprint("SAT-001", "X", {"b": 2, "a": 1})
        assert a == b

    def test_different_content_different_fingerprint(self):
        a = request_fingerprint("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 50})
        b = request_fingerprint("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 60})
        assert a != b


# ── Idempotent submission ─────────────────────────────────────────────────────

class TestSubmit:

    def test_first_submission_is_created(self):
        r = CommandService().submit(
            "SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR, "k1"
        )
        assert r.created is True

    def test_retry_with_same_key_returns_the_original(self):
        service = CommandService()
        first = service.submit("SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR, "k1")
        retry = service.submit("SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR, "k1")
        assert retry.created is False
        assert retry.command.command_id == first.command.command_id

    def test_same_key_different_content_is_a_conflict(self):
        service = CommandService()
        service.submit("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 50}, OPERATOR, "k1")
        with pytest.raises(IdempotencyConflictError):
            service.submit("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 60}, OPERATOR, "k1")

    def test_requests_without_keys_are_independent(self):
        service = CommandService()
        a = service.submit("SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR)
        b = service.submit("SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR)
        assert a.command.command_id != b.command.command_id

    def test_invalid_request_is_rejected_even_with_a_key(self):
        with pytest.raises(CommandValidationError):
            CommandService().submit("SAT-001", "SELF_DESTRUCT", {}, OPERATOR, "k1")


# ── Approval through the service ──────────────────────────────────────────────

class TestApprove:

    def test_approve_updates_the_stored_command(self):
        service = CommandService()
        cid = _approved(service)
        assert service.get(cid).status is CommandStatus.APPROVED

    def test_approving_twice_is_refused(self):
        service = CommandService()
        cid = _approved(service)
        with pytest.raises(InvalidTransitionError):
            service.approve(cid, OPERATOR)

    def test_system_actor_cannot_approve(self):
        service = CommandService()
        r = service.submit("SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR)
        with pytest.raises(ApprovalError):
            service.approve(r.command.command_id, UPLINK)

    def test_unknown_command_raises(self):
        with pytest.raises(CommandNotFoundError):
            CommandService().approve("nope", OPERATOR)


# ── Dispatch: the at-most-once guard ──────────────────────────────────────────

class TestDispatch:

    def test_approved_command_is_sent_once(self):
        service, sender = CommandService(), RecordingSender()
        cid = _approved(service)
        result = service.dispatch(cid, UPLINK, sender)
        assert result.sent is True
        assert sender.sent == [cid]
        assert service.get(cid).status is CommandStatus.UPLINKED

    def test_repeated_dispatch_does_not_send_again(self):
        service, sender = CommandService(), RecordingSender()
        cid = _approved(service)
        service.dispatch(cid, UPLINK, sender)
        again = service.dispatch(cid, UPLINK, sender)
        assert again.sent is False
        assert "already UPLINKED" in again.reason
        assert sender.sent == [cid]            # still just one transmission

    def test_unapproved_draft_cannot_be_dispatched(self):
        service, sender = CommandService(), RecordingSender()
        r = service.submit("SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR)
        with pytest.raises(InvalidTransitionError):
            service.dispatch(r.command.command_id, UPLINK, sender)
        assert sender.sent == []

    def test_sender_error_marks_the_command_failed(self):
        service = CommandService()
        sender = RecordingSender(fail_with=RuntimeError("link down"))
        cid = _approved(service)
        result = service.dispatch(cid, UPLINK, sender)
        assert result.sent is False
        assert service.get(cid).status is CommandStatus.FAILED
        assert "link down" in service.get(cid).history[-1].reason

    def test_failed_command_is_not_retried_by_a_repeat_dispatch(self):
        service = CommandService()
        cid = _approved(service)
        service.dispatch(cid, UPLINK, RecordingSender(fail_with=RuntimeError("x")))
        good_sender = RecordingSender()
        again = service.dispatch(cid, UPLINK, good_sender)
        assert again.sent is False
        assert good_sender.sent == []

    def test_many_threads_dispatching_send_exactly_once(self):
        """20 threads race to uplink one command: the sender runs once."""
        service, sender = CommandService(), RecordingSender()
        cid = _approved(service)
        results = []

        def attempt():
            results.append(service.dispatch(cid, UPLINK, sender))

        threads = [threading.Thread(target=attempt) for _ in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert len(sender.sent) == 1                      # sent exactly once
        assert sum(1 for r in results if r.sent) == 1     # one winner
        assert service.get(cid).status is CommandStatus.UPLINKED


# ── Confirmation ──────────────────────────────────────────────────────────────

class TestConfirm:

    def test_confirm_after_uplink(self):
        service = CommandService()
        cid = _approved(service)
        service.dispatch(cid, UPLINK, RecordingSender())
        assert service.confirm(cid, UPLINK).status is CommandStatus.CONFIRMED

    def test_repeated_confirmation_is_harmless(self):
        service = CommandService()
        cid = _approved(service)
        service.dispatch(cid, UPLINK, RecordingSender())
        service.confirm(cid, UPLINK)
        again = service.confirm(cid, UPLINK)       # redelivered acknowledgement
        assert again.status is CommandStatus.CONFIRMED
        assert len([h for h in again.history if h.status is CommandStatus.CONFIRMED]) == 1

    def test_cannot_confirm_a_command_that_was_never_uplinked(self):
        service = CommandService()
        cid = _approved(service)
        with pytest.raises(InvalidTransitionError):
            service.confirm(cid, UPLINK)
