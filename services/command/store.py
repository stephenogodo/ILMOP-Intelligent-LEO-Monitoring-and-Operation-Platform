"""
services/command/store.py

Where commands are remembered (Sprint 8, S8-05).

WHY A STORE
───────────
Exactly-once handling needs memory: "have I seen this idempotency key
before?" and "is this command already uplinked?".  The store is that memory.

THE ONE SPECIAL OPERATION: compare_and_save
───────────────────────────────────────────
Most stores only offer "save".  This one also offers compare-and-swap:

    "replace the stored command with this new version, but ONLY IF the
     stored command is still in the status I expect."

If two callers race, one swap succeeds and the other is told it failed.
That single guarantee is what stops a command being uplinked twice.

IN-MEMORY FOR NOW
─────────────────
InMemoryCommandStore keeps everything in a dict guarded by a lock.  It
forgets everything on restart.  A database-backed store can replace it later
by providing the same methods; the callers do not change.
"""

import threading
from typing import Dict, Optional

from shared.schemas.command_schema import Command, CommandStatus


class InMemoryCommandStore:
    """Thread-safe, in-memory command store."""

    def __init__(self) -> None:
        # command_id -> Command
        self._commands: Dict[str, Command] = {}

        # idempotency_key -> command_id
        self._by_key: Dict[str, str] = {}

        # One lock protects both dicts.  Every public method takes it, so
        # no two operations can interleave and corrupt the state.
        self._lock = threading.Lock()

    # ── Reading ───────────────────────────────────────────────────────────────

    def get(self, command_id: str) -> Optional[Command]:
        """Return the stored command, or None if the ID is unknown."""
        with self._lock:
            return self._commands.get(command_id)

    def find_by_idempotency_key(self, key: str) -> Optional[Command]:
        """Return the command first created under this key, or None."""
        with self._lock:
            command_id = self._by_key.get(key)
            return self._commands.get(command_id) if command_id else None

    # ── Writing ───────────────────────────────────────────────────────────────

    def create_if_key_absent(self, command: Command) -> Command:
        """
        Store a new command, unless its idempotency key is already in use.

        Returns the command now stored under that key: the new one if the
        key was free, otherwise the EARLIER one.  The caller compares the
        returned command's ID with its own to see which happened.

        The check and the insert happen under one lock, so two simultaneous
        submissions with the same key cannot both be created.
        """
        with self._lock:
            key = command.idempotency_key
            if key is not None and key in self._by_key:
                return self._commands[self._by_key[key]]

            self._commands[command.command_id] = command
            if key is not None:
                self._by_key[key] = command.command_id
            return command

    def compare_and_save(
        self,
        updated: Command,
        expected_status: CommandStatus,
    ) -> bool:
        """
        Replace a stored command with `updated`, only if the stored command
        is still in `expected_status`.

        Returns True if the swap happened, False if it did not (because the
        command is unknown or someone else already changed its status).
        """
        with self._lock:
            current = self._commands.get(updated.command_id)
            if current is None or current.status is not expected_status:
                return False
            self._commands[updated.command_id] = updated
            return True
