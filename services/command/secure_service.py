"""
services/command/secure_service.py

The authenticated front door for commands (Sprint 8, S8-08).

WHAT IT DOES
────────────
Wraps the existing CommandService.  Its methods take a session TOKEN instead
of an Actor.  For each call it:

    1. resolves the token to a verified identity   (authentication)
    2. checks the identity holds the needed role   (authorisation)
    3. calls the existing CommandService, passing the VERIFIED Actor
    4. records the attempt in the security audit log, allowed or denied

Because the Actor is built from the token and never supplied by the caller,
it cannot be forged.  The unwrapped CommandService is now internal plumbing;
the API and dashboard should only use this class.

ROLES NEEDED
────────────
    submit      AUTHOR
    approve     APPROVER, and the account must be a human OPERATOR
    reject      APPROVER, and the account must be a human OPERATOR
    uplink      UPLINKER
"""

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from shared.schemas.command_schema import ActorKind, Command
from services.command.auth import (
    AuthenticationError,
    AuthorizationError,
    AuthService,
    Role,
    VerifiedIdentity,
)
from services.command.service import CommandService, DispatchResult, SubmitResult
from services.command.uplink_bridge import run_uplink


@dataclass(frozen=True)
class AuditEvent:
    """One line of the security audit log."""

    timestamp:  datetime
    who:        str            # account name, or "(unauthenticated)"
    action:     str            # e.g. "approve"
    command_id: Optional[str]
    allowed:    bool
    detail:     str            # the reason for a denial, or "ok"


class SecureCommandService:
    """Token-based, role-checked access to the command path."""

    def __init__(
        self,
        auth: AuthService,
        service: Optional[CommandService] = None,
        require_second_person: bool = False,
    ) -> None:
        self._auth    = auth
        self._service = service or CommandService()

        # When True, nobody may approve a command they authored.  Meaningful
        # now because names are verified.  Off by default (one operator).
        self._second_person = require_second_person

        self._audit: List[AuditEvent] = []
        self._audit_lock = threading.Lock()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _log(self, who: str, action: str, command_id: Optional[str],
             allowed: bool, detail: str) -> None:
        with self._audit_lock:
            self._audit.append(AuditEvent(
                timestamp=datetime.now(timezone.utc),
                who=who, action=action, command_id=command_id,
                allowed=allowed, detail=detail,
            ))

    def _identify(self, token: str, action: str, role: Role,
                  command_id: Optional[str] = None,
                  human_only: bool = False) -> VerifiedIdentity:
        """
        Authenticate the token and authorise the action, logging a denial.

        Raises AuthenticationError or AuthorizationError on failure.
        """
        try:
            identity = self._auth.resolve(token)
        except AuthenticationError as exc:
            self._log("(unauthenticated)", action, command_id, False, str(exc))
            raise

        try:
            identity.require(role)
            if human_only and identity.actor.kind is not ActorKind.OPERATOR:
                raise AuthorizationError(
                    f"'{identity.actor.name}' is not a human operator account"
                )
        except AuthorizationError as exc:
            self._log(identity.actor.name, action, command_id, False, str(exc))
            raise

        return identity

    # ── Operations ────────────────────────────────────────────────────────────

    def submit(
        self,
        token: str,
        satellite_id: str,
        command_type: str,
        parameters: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> SubmitResult:
        """Author a command.  Requires AUTHOR."""
        who = self._identify(token, "submit", Role.AUTHOR)
        result = self._service.submit(
            satellite_id, command_type, parameters, who.actor, idempotency_key
        )
        self._log(who.actor.name, "submit", result.command.command_id, True, "ok")
        return result

    def approve(self, token: str, command_id: str) -> Command:
        """Approve a draft.  Requires APPROVER and a human OPERATOR account."""
        who = self._identify(token, "approve", Role.APPROVER, command_id, human_only=True)
        command = self._service.approve(
            command_id, who.actor, require_second_person=self._second_person
        )
        self._log(who.actor.name, "approve", command_id, True, "ok")
        return command

    def reject(self, token: str, command_id: str, reason: str) -> Command:
        """Reject a draft.  Requires APPROVER and a human OPERATOR account."""
        who = self._identify(token, "reject", Role.APPROVER, command_id, human_only=True)
        updated = self._service.reject(command_id, who.actor, reason)
        self._log(who.actor.name, "reject", command_id, True, reason)
        return updated

    def uplink(self, token: str, command_id: str, receiver) -> DispatchResult:
        """
        Uplink an approved command to a satellite receiver and confirm it.
        Requires UPLINKER.
        """
        who = self._identify(token, "uplink", Role.UPLINKER, command_id)
        result = run_uplink(self._service, command_id, receiver, who.actor)
        self._log(who.actor.name, "uplink", command_id, True, result.reason or "ok")
        return result

    def uplink_async(self, token: str, command_id: str,
                     sender: Callable[[Command], None]) -> DispatchResult:
        """
        Uplink an approved command through `sender` WITHOUT waiting for the
        satellite (for example a sender that publishes to Kafka).  Requires
        UPLINKER.

        The command is claimed (APPROVED -> UPLINKED) and sent at most once.
        It stays UPLINKED until the satellite's acknowledgement arrives and
        the acknowledgement listener confirms or fails it.  If the sender
        raises, the command is marked FAILED straight away.
        """
        who = self._identify(token, "uplink", Role.UPLINKER, command_id)
        result = self._service.dispatch(command_id, who.actor, sender)
        self._log(who.actor.name, "uplink", command_id, True, result.reason or "sent")
        return result

    # ── Reading ───────────────────────────────────────────────────────────────

    def get(self, token: str, command_id: str) -> Optional[Command]:
        """Read a command.  Any valid session may read."""
        try:
            self._auth.resolve(token)
        except AuthenticationError as exc:
            self._log("(unauthenticated)", "get", command_id, False, str(exc))
            raise
        return self._service.get(command_id)

    def audit_log(self) -> List[AuditEvent]:
        """A copy of the security audit log, oldest first."""
        with self._audit_lock:
            return list(self._audit)
