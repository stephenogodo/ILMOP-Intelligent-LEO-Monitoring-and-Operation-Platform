"""
services/satellite_simulator/command_receiver.py

The simulated satellite's command receiver (Sprint 8, S8-06).

WHAT THIS IS
────────────
The part of the simulated spacecraft that listens for uplinked commands,
decides whether to accept them, runs them, and answers with an
acknowledgement (CommandAck).

THE RULES IT FOLLOWS, IN ORDER
──────────────────────────────
    1. Already executed this command ID?   -> return the earlier answer,
                                              run nothing  (duplicate guard)
    2. Is the command for this satellite?  -> else reject
    3. Is the satellite in contact?        -> else reject ("no contact")
    4. Did it arrive with status UPLINKED? -> else reject
    5. Is the command type known?          -> else reject
    6. Execute it and remember the ID, then acknowledge.

Rule 1 is the satellite-side half of exactly-once delivery.  The ground
side (services/command/service.py) never SENDS a command twice; this makes
sure the satellite would never EXECUTE it twice even if it were sent twice.

HOW THE STATE IS SHARED
───────────────────────
The receiver does not own the satellite.  It is given the same Satellite
object and contact model that TelemetryGenerator updates every tick, so a
command's effect (for example safe mode) shows up in the next telemetry
record with no extra plumbing.
"""

from datetime import datetime, timezone
from typing import Dict, Optional

from shared.schemas.command_schema import Command, CommandAck, CommandStatus


class CommandReceiver:
    """Receives, checks, executes and acknowledges uplinked commands."""

    def __init__(self, satellite, contact_model) -> None:
        # Shared, live objects (see module docstring).
        self._sat     = satellite
        self._contact = contact_model

        # command_id -> the acknowledgement returned the first time.
        # This is the satellite's memory of what it has already executed.
        self._executed: Dict[str, CommandAck] = {}

        # Settings that commands can change.  They live on the receiver so
        # tests and the dashboard can read them back.
        self.telemetry_interval_s: float = 1.0
        self.reboot_counts: Dict[str, int] = {}

        # How many repeats were recognised and ignored (useful in tests).
        self.duplicates_ignored: int = 0

        # Table of command type -> the method that executes it.
        # Adding a command later means adding one entry and one method.
        self._handlers = {
            "SET_SAFE_MODE":          self._do_set_safe_mode,
            "SET_DOWNLINK_RATE":      self._do_set_downlink_rate,
            "SET_TELEMETRY_INTERVAL": self._do_set_telemetry_interval,
            "REBOOT_SUBSYSTEM":       self._do_reboot_subsystem,
        }

    # ── Public entry point ────────────────────────────────────────────────────

    def receive(self, command: Command, now: Optional[datetime] = None) -> CommandAck:
        """
        Handle one uplinked command and return the acknowledgement.

        Never raises for a bad command: a rejection is an acknowledgement
        with accepted=False, because a real satellite answers rather than
        crashing.
        """
        now = now or datetime.now(timezone.utc)

        # Rule 1: duplicate guard.  Return the ORIGINAL acknowledgement so
        # the answer is identical however many times the command arrives.
        earlier = self._executed.get(command.command_id)
        if earlier is not None:
            self.duplicates_ignored += 1
            return earlier

        # Rule 2: addressed to this satellite?
        if command.satellite_id != self._sat.satellite_id:
            return self._reject(command, "wrong satellite", now)

        # Rule 3: uplink only works during a contact window.
        if not self._sat.in_contact:
            return self._reject(command, "no contact", now)

        # Rule 4: the ground must have approved and uplinked it.
        if command.status is not CommandStatus.UPLINKED:
            return self._reject(
                command, f"command status is {command.status.value}, not UPLINKED", now
            )

        # Rule 5: do we know this command type?
        handler = self._handlers.get(command.command_type)
        if handler is None:
            return self._reject(command, f"unknown command type {command.command_type}", now)

        # Rule 6: execute, remember, acknowledge.
        handler(command.parameters)
        ack = CommandAck(
            command_id=command.command_id,
            satellite_id=self._sat.satellite_id,
            accepted=True,
            executed=True,
            reason="executed",
            timestamp=now,
        )
        self._executed[command.command_id] = ack
        return ack

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _reject(self, command: Command, reason: str, now: datetime) -> CommandAck:
        """
        Build a rejection.  It is NOT remembered in the executed table, so
        the same command can be accepted later (for example once contact
        is established), if the ground side chooses to resend it.
        """
        return CommandAck(
            command_id=command.command_id,
            satellite_id=self._sat.satellite_id,
            accepted=False,
            executed=False,
            reason=reason,
            timestamp=now,
        )

    # ── Command handlers ──────────────────────────────────────────────────────
    # Each receives the already-validated parameters from the ground side.

    def _do_set_safe_mode(self, params: Dict) -> None:
        # The flag is read into the next telemetry record.
        self._sat.safe_mode = bool(params["enabled"])

    def _do_set_downlink_rate(self, params: Dict) -> None:
        # Changes the mean the contact model draws downlink rates around.
        self._contact.downlink_mean_mbps = float(params["rate_mbps"])

    def _do_set_telemetry_interval(self, params: Dict) -> None:
        # Stored and reported; the simulator's tick loop does not use it yet.
        self.telemetry_interval_s = float(params["interval_s"])

    def _do_reboot_subsystem(self, params: Dict) -> None:
        # The simulator has no real subsystem restart, so record that it
        # happened.  This is the visible evidence the command ran.
        name = params["subsystem"]
        self.reboot_counts[name] = self.reboot_counts.get(name, 0) + 1
