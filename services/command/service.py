"""
services/command/service.py

Exactly-once command handling (Sprint 8, S8-05).

This module joins the pieces built earlier:

    authoring.py   validates a request and creates a DRAFT   (S8-03)
    lifecycle.py   the allowed state changes and approval    (S8-04)
    store.py       remembers commands and idempotency keys   (S8-05)

and adds the two protections against duplicates:

    1. submit()    same idempotency key -> same command, never a second one
    2. dispatch()  a command is handed to the sender AT MOST ONCE

"Exactly once" over a network is not truly possible, so the goal is
"effectively once": retries are allowed but harmless.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from shared.schemas.command_schema import Actor, Command, CommandStatus
from services.command import lifecycle
from services.command.authoring import author_command
from services.command.store import InMemoryCommandStore


class IdempotencyConflictError(Exception):
    """
    Raised when an idempotency key is reused for a DIFFERENT request.

    Quietly returning the old command would hide that the caller asked for
    something new, so this is reported as an error.
    """


class CommandNotFoundError(Exception):
    """Raised when a command ID is not in the store."""


@dataclass(frozen=True)
class SubmitResult:
    """Outcome of submit(): the command, and whether it was newly created."""
    command: Command
    created: bool


@dataclass(frozen=True)
class DispatchResult:
    """
    Outcome of dispatch().

    sent:    True only for the one call that actually invoked the sender.
    command: the command as stored after the call.
    reason:  why nothing was sent, when sent is False.
    """
    sent:    bool
    command: Command
    reason:  Optional[str] = None


def request_fingerprint(
    satellite_id: str,
    command_type: str,
    parameters: Dict[str, Any],
) -> str:
    """
    A short hash identifying the CONTENT of a request.

    Two requests with the same satellite, type and parameters produce the
    same fingerprint, whatever order the parameters were written in
    (sort_keys makes the JSON text canonical).
    """
    canonical = json.dumps(
        {"s": satellite_id, "t": command_type, "p": parameters},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CommandService:
    """Entry point for creating, approving, uplinking and confirming commands."""

    def __init__(self, store: Optional[InMemoryCommandStore] = None) -> None:
        self._store = store or InMemoryCommandStore()

    # ── Step 1: submit ────────────────────────────────────────────────────────

    def submit(
        self,
        satellite_id: str,
        command_type: str,
        parameters: Dict[str, Any],
        author: Actor,
        idempotency_key: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> SubmitResult:
        """
        Create a DRAFT, or return the earlier one if this is a repeat.

        Validation errors raise CommandValidationError as before.
        Reusing a key with different content raises IdempotencyConflictError.
        """
        # Validate and build the new DRAFT first.  An invalid request is
        # rejected here whether or not its key was seen before.
        candidate = author_command(
            satellite_id, command_type, parameters, author,
            idempotency_key=idempotency_key, now=now,
        )

        # Atomically store it, or get back the earlier command for this key.
        stored = self._store.create_if_key_absent(candidate)

        if stored.command_id == candidate.command_id:
            return SubmitResult(command=stored, created=True)

        # The key was already used.  Same content = harmless retry.
        same = (
            request_fingerprint(stored.satellite_id, stored.command_type, stored.parameters)
            == request_fingerprint(satellite_id, command_type, parameters)
        )
        if not same:
            raise IdempotencyConflictError(
                f"idempotency key '{idempotency_key}' was already used "
                f"for a different request (command {stored.command_id})"
            )
        return SubmitResult(command=stored, created=False)

    # ── Step 2: approve ───────────────────────────────────────────────────────

    def approve(
        self,
        command_id: str,
        approver: Actor,
        require_second_person: bool = False,
        now: Optional[datetime] = None,
    ) -> Command:
        """
        Approve a stored DRAFT (human operators only, see lifecycle.approve).

        Uses compare-and-swap, so two simultaneous approvals cannot both
        win: the loser gets InvalidTransitionError.
        """
        command = self._require(command_id)
        updated = lifecycle.approve(command, approver, require_second_person, now)
        if not self._store.compare_and_save(updated, CommandStatus.DRAFT):
            raise lifecycle.InvalidTransitionError(
                f"command {command_id} is no longer DRAFT"
            )
        return updated

    def reject(
        self,
        command_id: str,
        operator: Actor,
        reason: str,
        now: Optional[datetime] = None,
    ) -> Command:
        """
        Reject a stored DRAFT (human operators only, a reason is required).

        Uses compare-and-swap like approve(), so a command that was approved
        a moment earlier cannot also be rejected.
        """
        command = self._require(command_id)
        updated = lifecycle.reject(command, operator, reason, now)
        if not self._store.compare_and_save(updated, CommandStatus.DRAFT):
            raise lifecycle.InvalidTransitionError(
                f"command {command_id} is no longer DRAFT"
            )
        return updated

    # ── Step 3: dispatch (the at-most-once guard) ─────────────────────────────

    def dispatch(
        self,
        command_id: str,
        actor: Actor,
        sender: Callable[[Command], None],
        now: Optional[datetime] = None,
    ) -> DispatchResult:
        """
        Hand an APPROVED command to `sender`, at most once.

        sender is any function that transmits the command; it may raise if
        the transmission fails.

        Order of operations (this is the whole point):
          1. CLAIM: atomically move APPROVED -> UPLINKED in the store.
          2. Only the caller that won the claim calls the sender.
          3. Everyone else is told it was a duplicate and nothing is sent.

        Claiming BEFORE sending means a crash in between leaves the command
        marked UPLINKED but unsent.  That is deliberate (see the design
        note): the operator sees it never got confirmed.  Sending first
        could transmit twice, which is worse for commands such as reboots.
        """
        command = self._require(command_id)

        # A repeat request: the command has already moved past APPROVED.
        # Do nothing and say why, without calling the sender.
        if command.status in (
            CommandStatus.UPLINKED, CommandStatus.CONFIRMED, CommandStatus.FAILED
        ):
            return DispatchResult(
                sent=False, command=command,
                reason=f"already {command.status.value}",
            )

        # Any other status (DRAFT, REJECTED) is not allowed to uplink;
        # lifecycle raises InvalidTransitionError for those.
        claimed = lifecycle.mark_uplinked(command, actor, now)

        # Step 1: the claim.  Exactly one racing caller gets True.
        if not self._store.compare_and_save(claimed, CommandStatus.APPROVED):
            current = self._require(command_id)
            return DispatchResult(
                sent=False, command=current,
                reason="another caller claimed this command first",
            )

        # Step 2: we won, so we alone call the sender.
        try:
            sender(claimed)
        except Exception as exc:
            # Transmission failed: record it so the operator can investigate.
            failed = lifecycle.fail(claimed, actor, f"uplink error: {exc}", now)
            self._store.compare_and_save(failed, CommandStatus.UPLINKED)
            return DispatchResult(sent=False, command=failed, reason=f"uplink error: {exc}")

        return DispatchResult(sent=True, command=claimed)

    # ── Step 4: confirm ───────────────────────────────────────────────────────

    def confirm(
        self,
        command_id: str,
        actor: Actor,
        now: Optional[datetime] = None,
    ) -> Command:
        """
        Record that the satellite acknowledged an UPLINKED command.

        A repeated confirmation (for example a redelivered acknowledgement)
        returns the command unchanged instead of raising.
        """
        command = self._require(command_id)
        if command.status is CommandStatus.CONFIRMED:
            return command

        updated = lifecycle.confirm(command, actor, now)
        if not self._store.compare_and_save(updated, CommandStatus.UPLINKED):
            return self._require(command_id)
        return updated

    # ── Reading ───────────────────────────────────────────────────────────────

    def get(self, command_id: str) -> Optional[Command]:
        """Return a stored command, or None."""
        return self._store.get(command_id)

    def _require(self, command_id: str) -> Command:
        """Return a stored command or raise CommandNotFoundError."""
        command = self._store.get(command_id)
        if command is None:
            raise CommandNotFoundError(f"unknown command '{command_id}'")
        return command
