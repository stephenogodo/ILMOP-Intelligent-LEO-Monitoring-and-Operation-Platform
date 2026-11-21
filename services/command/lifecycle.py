"""
services/command/lifecycle.py

The command approval gate and state machine (Sprint 8, S8-04).

THE RULE THIS MODULE ENFORCES
─────────────────────────────
A command moves through its states in a fixed order and cannot skip one.
The only legal moves are listed in ALLOWED_TRANSITIONS below.  Anything
else raises InvalidTransitionError.

THE HUMAN-ONLY RULE
───────────────────
approve() refuses an actor of kind SYSTEM.  The ConOps states that no
automation may approve a command, so the rule is enforced in code and not
only written down.

HOW CHANGES WORK
────────────────
Commands are immutable.  Every function here returns a NEW Command with the
new status and one more audit entry.  The original is untouched.
"""

from datetime import datetime, timezone
from typing import Dict, Optional, Set

from shared.schemas.command_schema import (
    Actor,
    ActorKind,
    Command,
    CommandStatus,
    StatusChange,
)

S = CommandStatus   # short alias to keep the table readable

# From-state -> the set of states it may move to.
# Final states (CONFIRMED, FAILED, REJECTED) map to an empty set.
ALLOWED_TRANSITIONS: Dict[CommandStatus, Set[CommandStatus]] = {
    S.DRAFT:     {S.APPROVED, S.REJECTED},
    S.APPROVED:  {S.UPLINKED, S.FAILED},
    S.UPLINKED:  {S.CONFIRMED, S.FAILED},
    S.CONFIRMED: set(),
    S.FAILED:    set(),
    S.REJECTED:  set(),
}


class InvalidTransitionError(Exception):
    """Raised when a command is asked to make a move that is not allowed."""


class ApprovalError(Exception):
    """Raised when an approval or rejection breaks an approval rule."""


def _transition(
    command: Command,
    new_status: CommandStatus,
    actor: Actor,
    reason: Optional[str],
    now: Optional[datetime],
) -> Command:
    """
    Move a command to new_status and return the updated copy.

    This is the single place where the transition table is checked, so no
    public function below can bypass it.
    """
    if new_status not in ALLOWED_TRANSITIONS[command.status]:
        raise InvalidTransitionError(
            f"cannot move command {command.command_id} "
            f"from {command.status.value} to {new_status.value}"
        )

    entry = StatusChange(
        status=new_status,
        actor=actor,
        timestamp=now or datetime.now(timezone.utc),
        reason=reason,
    )

    # model_copy builds a new object; the original command is not modified.
    return command.model_copy(
        update={
            "status":  new_status,
            "history": [*command.history, entry],
        }
    )


def approve(
    command: Command,
    approver: Actor,
    require_second_person: bool = False,
    now: Optional[datetime] = None,
) -> Command:
    """
    Approve a DRAFT command.  Only a human operator may do this.

    require_second_person:
        When True, the person who authored the command may not approve it
        (a "two-person rule").  Default False because the project currently
        has a single operator.  S8-08 will tie this to real identities.
    """
    # Human-only rule: automation can never approve.
    if approver.kind is not ActorKind.OPERATOR:
        raise ApprovalError("only a human operator may approve a command")

    # Optional two-person rule.
    if require_second_person and approver.name == command.created_by.name:
        raise ApprovalError("the author may not approve their own command")

    return _transition(command, S.APPROVED, approver, "approved", now)


def reject(
    command: Command,
    operator: Actor,
    reason: str,
    now: Optional[datetime] = None,
) -> Command:
    """
    Reject a DRAFT command.  Only a human operator may do this, and a reason
    is required so the audit trail explains the decision.
    """
    if operator.kind is not ActorKind.OPERATOR:
        raise ApprovalError("only a human operator may reject a command")
    if not reason or not reason.strip():
        raise ApprovalError("a reason is required to reject a command")

    return _transition(command, S.REJECTED, operator, reason.strip(), now)


def mark_uplinked(
    command: Command,
    actor: Actor,
    now: Optional[datetime] = None,
) -> Command:
    """
    Record that an APPROVED command was sent to the satellite.

    Normally called by the uplink service (a SYSTEM actor).  Because the
    transition table only allows APPROVED -> UPLINKED, an unapproved
    command can never be uplinked.
    """
    return _transition(command, S.UPLINKED, actor, "uplinked", now)


def confirm(
    command: Command,
    actor: Actor,
    now: Optional[datetime] = None,
) -> Command:
    """Record that the satellite acknowledged executing the command."""
    return _transition(command, S.CONFIRMED, actor, "execution confirmed", now)


def fail(
    command: Command,
    actor: Actor,
    reason: str,
    now: Optional[datetime] = None,
) -> Command:
    """
    Record that the command failed, from APPROVED or UPLINKED.

    A reason is required so the failure can be investigated later.
    """
    if not reason or not reason.strip():
        raise ApprovalError("a reason is required to mark a command as failed")

    return _transition(command, S.FAILED, actor, reason.strip(), now)
