"""
services/command/uplink_bridge.py

Joins the ground-side command service to a simulated satellite (S8-06).

WHAT IT DOES
────────────
CommandService.dispatch() needs a "sender": a function that transmits the
command.  make_sender() builds one that hands the command to a simulated
satellite's CommandReceiver.  run_uplink() then performs the full final
steps in one call:

    dispatch (claim, then send)  ->  satellite acknowledges
        accepted  -> command is CONFIRMED
        rejected  -> the sender raises, so the S8-05 logic marks the
                     command FAILED with the satellite's reason

Real uplink (a radio link, Kafka topics) replaces this later.  The
service code stays the same; only the sender changes.
"""

from typing import Callable, Dict, Optional

from shared.schemas.command_schema import Actor, Command, CommandAck
from services.command.service import CommandService, DispatchResult


class UplinkRejectedError(Exception):
    """Raised by the sender when the satellite turns the command away."""


def make_sender(
    receiver,
    acks: Optional[Dict[str, CommandAck]] = None,
) -> Callable[[Command], None]:
    """
    Build a sender function for CommandService.dispatch.

    receiver: a CommandReceiver (or anything with a receive(command) method).
    acks:     optional dict; the satellite's answer is stored in it under
              the command ID so the caller can read it afterwards.
    """

    def sender(command: Command) -> None:
        ack = receiver.receive(command)
        if acks is not None:
            acks[command.command_id] = ack
        if not ack.accepted:
            # Raising tells CommandService to mark the command FAILED and
            # to record this text as the reason.
            raise UplinkRejectedError(ack.reason)

    return sender


def run_uplink(
    service: CommandService,
    command_id: str,
    receiver,
    actor: Actor,
) -> DispatchResult:
    """
    Uplink an APPROVED command to a simulated satellite and confirm it.

    Returns the DispatchResult from the service.  If the satellite
    accepted, the stored command is CONFIRMED afterwards; if it rejected,
    the stored command is FAILED.
    """
    acks: Dict[str, CommandAck] = {}
    result = service.dispatch(command_id, actor, make_sender(receiver, acks))

    # Confirm only when this call actually sent the command AND the
    # satellite accepted it.  A duplicate dispatch (sent=False) must not
    # confirm anything.
    ack = acks.get(command_id)
    if result.sent and ack is not None and ack.accepted:
        confirmed = service.confirm(command_id, actor)
        return DispatchResult(sent=True, command=confirmed)

    return result
