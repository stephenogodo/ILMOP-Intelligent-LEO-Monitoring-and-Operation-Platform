"""
tests/test_command_lifecycle.py

Tests for the command state machine and approval gate (S8-04).
"""

from datetime import datetime, timedelta, timezone

import pytest

from shared.schemas.command_schema import Actor, ActorKind, CommandStatus
from services.command.authoring import author_command
from services.command.lifecycle import (
    ALLOWED_TRANSITIONS,
    ApprovalError,
    InvalidTransitionError,
    approve,
    confirm,
    fail,
    mark_uplinked,
    reject,
)

OPERATOR = Actor(name="stephen", kind=ActorKind.OPERATOR)
OTHER_OP = Actor(name="amara",   kind=ActorKind.OPERATOR)
UPLINK   = Actor(name="uplink-service", kind=ActorKind.SYSTEM)
T0       = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)


def _draft():
    """A fresh valid DRAFT command, authored by OPERATOR at T0."""
    return author_command(
        "SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 50}, OPERATOR, now=T0
    )


# ── The happy path ────────────────────────────────────────────────────────────

class TestHappyPath:

    def test_full_path_to_confirmed(self):
        cmd = _draft()
        cmd = approve(cmd, OPERATOR, now=T0 + timedelta(minutes=1))
        cmd = mark_uplinked(cmd, UPLINK, now=T0 + timedelta(minutes=2))
        cmd = confirm(cmd, UPLINK, now=T0 + timedelta(minutes=3))
        assert cmd.status is CommandStatus.CONFIRMED

    def test_history_records_every_step_in_order(self):
        cmd = _draft()
        cmd = approve(cmd, OPERATOR)
        cmd = mark_uplinked(cmd, UPLINK)
        cmd = confirm(cmd, UPLINK)
        assert [h.status for h in cmd.history] == [
            CommandStatus.DRAFT,
            CommandStatus.APPROVED,
            CommandStatus.UPLINKED,
            CommandStatus.CONFIRMED,
        ]

    def test_history_records_who_acted(self):
        cmd = approve(_draft(), OPERATOR)
        assert cmd.history[-1].actor == OPERATOR

    def test_status_always_matches_last_history_entry(self):
        cmd = approve(_draft(), OPERATOR)
        assert cmd.status is cmd.history[-1].status

    def test_original_command_is_not_modified(self):
        original = _draft()
        approve(original, OPERATOR)
        assert original.status is CommandStatus.DRAFT
        assert len(original.history) == 1


# ── The human-only rule ───────────────────────────────────────────────────────

class TestHumanApproval:

    def test_system_actor_cannot_approve(self):
        with pytest.raises(ApprovalError):
            approve(_draft(), UPLINK)

    def test_system_actor_cannot_reject(self):
        with pytest.raises(ApprovalError):
            reject(_draft(), UPLINK, "no")

    def test_author_may_self_approve_by_default(self):
        assert approve(_draft(), OPERATOR).status is CommandStatus.APPROVED

    def test_two_person_rule_blocks_self_approval(self):
        with pytest.raises(ApprovalError):
            approve(_draft(), OPERATOR, require_second_person=True)

    def test_two_person_rule_allows_a_different_operator(self):
        cmd = approve(_draft(), OTHER_OP, require_second_person=True)
        assert cmd.status is CommandStatus.APPROVED


# ── Illegal transitions ───────────────────────────────────────────────────────

class TestIllegalTransitions:

    def test_cannot_uplink_a_draft(self):
        # The key safety property: an unapproved command can never be sent.
        with pytest.raises(InvalidTransitionError):
            mark_uplinked(_draft(), UPLINK)

    def test_cannot_confirm_a_draft(self):
        with pytest.raises(InvalidTransitionError):
            confirm(_draft(), UPLINK)

    def test_cannot_approve_twice(self):
        cmd = approve(_draft(), OPERATOR)
        with pytest.raises(InvalidTransitionError):
            approve(cmd, OPERATOR)

    def test_cannot_approve_after_rejection(self):
        cmd = reject(_draft(), OPERATOR, "wrong satellite")
        with pytest.raises(InvalidTransitionError):
            approve(cmd, OPERATOR)

    def test_final_states_accept_no_further_moves(self):
        for status in (CommandStatus.CONFIRMED, CommandStatus.FAILED, CommandStatus.REJECTED):
            assert ALLOWED_TRANSITIONS[status] == set()

    def test_every_status_is_in_the_table(self):
        assert set(ALLOWED_TRANSITIONS) == set(CommandStatus)


# ── Rejection and failure ─────────────────────────────────────────────────────

class TestRejectionAndFailure:

    def test_reject_records_the_reason(self):
        cmd = reject(_draft(), OPERATOR, "wrong satellite")
        assert cmd.status is CommandStatus.REJECTED
        assert cmd.history[-1].reason == "wrong satellite"

    def test_reject_requires_a_reason(self):
        with pytest.raises(ApprovalError):
            reject(_draft(), OPERATOR, "   ")

    def test_fail_from_uplinked(self):
        cmd = mark_uplinked(approve(_draft(), OPERATOR), UPLINK)
        cmd = fail(cmd, UPLINK, "no acknowledgement before LOS")
        assert cmd.status is CommandStatus.FAILED

    def test_fail_from_approved(self):
        cmd = fail(approve(_draft(), OPERATOR), UPLINK, "contact window missed")
        assert cmd.status is CommandStatus.FAILED

    def test_fail_requires_a_reason(self):
        cmd = approve(_draft(), OPERATOR)
        with pytest.raises(ApprovalError):
            fail(cmd, UPLINK, "")

    def test_cannot_fail_a_draft(self):
        with pytest.raises(InvalidTransitionError):
            fail(_draft(), UPLINK, "should not be possible")
