"""
services/command/kafka_uplink.py

The ground side of the command loop over Kafka (Sprint 9).

THE TWO HALVES OF A COMMAND'S JOURNEY
─────────────────────────────────────
Sprint 8 sent a command straight into a satellite object and read the
answer from the return value.  With the satellite in another process the
journey has two separate halves, tied together only by Kafka:

    1. SEND     make_bus_sender()   publishes the Command to
                                    commands.{sat}.uplinked
       (the command now stays UPLINKED, waiting)

    2. CONFIRM  AckListener         reads commands.{sat}.acks and turns the
                                    satellite's answer into CONFIRMED or FAILED

WHO IS ALLOWED TO DO WHAT
─────────────────────────
The sender is passed to CommandService.dispatch, which claims the command
(APPROVED -> UPLINKED) before calling it.  So at most one caller ever
publishes a given command, exactly as in Sprint 8.

The AckListener acts as a SYSTEM actor.  The only things it can do are
confirm or fail a command.  It can NOT approve one: approval is human-only
and enforced in lifecycle.py, whoever the caller is.

BAD OR ODD ACKNOWLEDGEMENTS
───────────────────────────
Kafka can deliver a message twice, and anything can be published to a
topic.  The listener therefore never trusts or crashes on what it reads:

    malformed      not valid JSON for a CommandAck           -> counted, skipped
    unknown        names a command this service never stored -> counted, skipped
    mismatched     the satellite ID does not match the stored
                   command's satellite                       -> counted, skipped
    duplicate      the command is already in the state the
                   acknowledgement asks for                   -> counted, no change
    conflicting    contradicts the stored state, such as a
                   rejection for a CONFIRMED command          -> counted, no change
    confirmed / failed  the normal outcomes                   -> counted

LIMITS
──────
A lost acknowledgement leaves the command UPLINKED forever.  That is the
visible failure described in ADR-021; a timeout that marks such commands
FAILED is still future work.
"""

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from pydantic import ValidationError

from services.command.lifecycle import ApprovalError, InvalidTransitionError
from services.command.service import CommandNotFoundError, CommandService
from shared.config import settings
from shared.schemas.command_schema import Actor, ActorKind, Command, CommandAck

log = logging.getLogger(__name__)

# The two topic stages this module uses.
UPLINKED_STAGE = "uplinked"
ACKS_STAGE = "acks"

# The identity the acknowledgement listener acts under in command histories.
ACK_LISTENER_ACTOR = Actor(name="ack-listener", kind=ActorKind.SYSTEM)


# ── Sending ──────────────────────────────────────────────────────────────────

def make_bus_sender(publisher) -> Callable[[Command], None]:
    """
    Build the `sender` function that CommandService.dispatch() expects, which
    publishes the command to its satellite's `uplinked` topic.

    If publishing fails (broker down, timeout) the sender raises, and
    dispatch() then marks the command FAILED with the reason, so a command
    is never silently lost.
    """

    def sender(command: Command) -> None:
        publisher.publish(
            settings.commands_topic(command.satellite_id, UPLINKED_STAGE),
            command.satellite_id,                       # key: keeps one satellite's commands in order
            command.model_dump_json().encode("utf-8"),
        )

    return sender


# ── Confirming ───────────────────────────────────────────────────────────────

@dataclass
class AckStats:
    """Counters, one per outcome described in the module docstring."""
    confirmed: int = 0
    failed: int = 0
    duplicate: int = 0
    conflicting: int = 0
    unknown: int = 0
    mismatched: int = 0
    malformed: int = 0


class AckListener:
    """
    Reads acknowledgements and updates the stored commands.

        listener = AckListener(service, subscriber)
        listener.drain()                       # handle everything waiting
        listener.run(stop_event)               # or loop until told to stop
    """

    def __init__(self, service: CommandService, subscriber,
                 actor: Actor = ACK_LISTENER_ACTOR) -> None:
        self._service = service
        self._subscriber = subscriber
        self._actor = actor
        self.stats = AckStats()

    # ── Subscribing helper ───────────────────────────────────────────────────

    @staticmethod
    def topic_for(satellite_id: str) -> str:
        """The acks topic of one satellite."""
        return settings.commands_topic(satellite_id, ACKS_STAGE)

    # ── Running ──────────────────────────────────────────────────────────────

    def poll_once(self, timeout_s: float = 0.0) -> bool:
        """Handle at most one message.  Returns True if a message was read."""
        message = self._subscriber.poll(timeout_s)
        if message is None:
            return False
        self._handle(message.value)
        return True

    def drain(self, max_messages: int = 1000) -> int:
        """Handle every message waiting right now (up to a limit)."""
        handled = 0
        while handled < max_messages and self.poll_once(0.0):
            handled += 1
        return handled

    def run(self, stop: threading.Event, poll_timeout_s: float = 1.0) -> None:
        """Loop until `stop` is set.  Never raises on a bad message."""
        while not stop.is_set():
            try:
                self.poll_once(poll_timeout_s)
            except Exception:                # a listener must outlive any single failure
                log.exception("ack listener: unexpected error, continuing")

    # ── The decision table ───────────────────────────────────────────────────

    def _handle(self, raw: bytes) -> None:
        try:
            ack = CommandAck.model_validate_json(raw)
        except (ValidationError, ValueError, TypeError) as exc:
            self.stats.malformed += 1
            log.warning("ack listener: malformed acknowledgement skipped: %s", exc)
            return

        command = self._service.get(ack.command_id)
        if command is None:
            self.stats.unknown += 1
            log.warning("ack listener: acknowledgement for unknown command %s",
                        ack.command_id)
            return

        if command.satellite_id != ack.satellite_id:
            self.stats.mismatched += 1
            log.warning("ack listener: command %s is for %s but the acknowledgement "
                        "came from %s; ignored", ack.command_id,
                        command.satellite_id, ack.satellite_id)
            return

        try:
            if ack.accepted:
                before = command.status
                updated = self._service.confirm(ack.command_id, self._actor)
                self._count_outcome(before, updated, confirmed=True)
            else:
                before = command.status
                updated = self._service.fail(
                    ack.command_id, self._actor,
                    f"rejected by satellite: {ack.reason}")
                self._count_outcome(before, updated, confirmed=False)
        except (InvalidTransitionError, ApprovalError, CommandNotFoundError) as exc:
            # For example a rejection for a command that is already CONFIRMED.
            self.stats.conflicting += 1
            log.warning("ack listener: acknowledgement contradicts stored state "
                        "for %s (%s); ignored", ack.command_id, exc)

    def _count_outcome(self, before, after, confirmed: bool) -> None:
        """
        confirm()/fail() return the stored command, and a repeat returns it
        unchanged.  If the status did not change, this was a duplicate.
        """
        if after.status is before:
            self.stats.duplicate += 1
        elif confirmed:
            self.stats.confirmed += 1
        else:
            self.stats.failed += 1
