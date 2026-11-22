"""
services/satellite_simulator/command_listener.py

The satellite side of the command loop over Kafka (Sprint 9).

WHAT IT DOES
────────────
It sits between Kafka and the CommandReceiver from Sprint 8:

    commands.{sat}.uplinked  --read-->  CommandReceiver.receive(command)
    commands.{sat}.acks      <-publish-  the CommandAck it returns

The receiver already makes every decision (right satellite? in contact?
status UPLINKED? known type?) and already refuses to run the same command
twice.  The listener adds no decisions of its own; it only moves messages.

WHERE IT RUNS
─────────────
Inside the telemetry producer process, once per tick (see
services/kafka_producer/producer.py).  That is deliberate: the receiver and
the telemetry generator share one Satellite object, so a command's effect
(for example safe mode) appears in the next telemetry record, and contact
windows are the same ones the telemetry reports.

MESSAGES IT CANNOT USE
──────────────────────
Anything that is not a valid Command is counted and skipped.  There is no
way to acknowledge it, because there is no command ID to acknowledge.

If publishing the acknowledgement fails, the command HAS been executed
(the receiver remembers it), but the ground never hears.  The listener
counts it and carries on.  The ground side then leaves the command UPLINKED,
the visible failure ADR-021 accepts.
"""

import logging
from dataclasses import dataclass

from pydantic import ValidationError

from services.messaging.bus import PublishError
from shared.config import settings
from shared.schemas.command_schema import Command

log = logging.getLogger(__name__)

UPLINKED_STAGE = "uplinked"
ACKS_STAGE = "acks"


@dataclass
class ListenerStats:
    received: int = 0       # valid commands handed to the receiver
    acknowledged: int = 0   # acknowledgements published
    malformed: int = 0      # messages that were not a valid Command
    ack_failures: int = 0   # acknowledgements that could not be published


class CommandListener:
    """Feeds uplinked commands to a CommandReceiver and publishes its answers."""

    def __init__(self, receiver, subscriber, publisher, satellite_id: str) -> None:
        self._receiver = receiver
        self._subscriber = subscriber
        self._publisher = publisher
        self._satellite_id = satellite_id
        self._acks_topic = settings.commands_topic(satellite_id, ACKS_STAGE)
        self.stats = ListenerStats()

    @staticmethod
    def uplinked_topic(satellite_id: str) -> str:
        """The topic this satellite reads its commands from."""
        return settings.commands_topic(satellite_id, UPLINKED_STAGE)

    def poll_once(self, timeout_s: float = 0.0) -> bool:
        """Handle at most one message.  Returns True if a message was read."""
        message = self._subscriber.poll(timeout_s)
        if message is None:
            return False

        try:
            command = Command.model_validate_json(message.value)
        except (ValidationError, ValueError, TypeError) as exc:
            self.stats.malformed += 1
            log.warning("command listener: malformed command skipped: %s", exc)
            return True

        self.stats.received += 1
        # The receiver never raises for a bad command: a rejection is an
        # acknowledgement with accepted=False.
        ack = self._receiver.receive(command)

        try:
            self._publisher.publish(
                self._acks_topic, self._satellite_id,
                ack.model_dump_json().encode("utf-8"),
            )
            self.stats.acknowledged += 1
        except PublishError as exc:
            self.stats.ack_failures += 1
            log.error("command listener: command %s was handled but its "
                      "acknowledgement could not be published: %s",
                      command.command_id, exc)
        return True

    def drain(self, max_messages: int = 100) -> int:
        """Handle every message waiting right now (up to a limit)."""
        handled = 0
        while handled < max_messages and self.poll_once(0.0):
            handled += 1
        return handled
