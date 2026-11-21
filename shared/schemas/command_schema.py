"""
shared/schemas/command_schema.py

Pydantic v2 data contract for spacecraft commands (Sprint 8, S8-03 / S8-04).

WHAT THIS FILE CONTAINS
───────────────────────
Only data definitions, no behaviour:

    CommandStatus   the states a command can be in
    ActorKind       who is acting: a human OPERATOR or an automated SYSTEM
    Actor           a named actor of one of those kinds
    StatusChange    one entry in a command's audit trail
    Command         the command itself

The rules that decide which command is valid live in
services/command/authoring.py, and the rules that decide which state change
is allowed live in services/command/lifecycle.py.  Keeping the data separate
from the rules means other services (API, dashboard, uplink) can import the
schema without pulling in any logic.

IMMUTABILITY
────────────
Command is "frozen": once created it cannot be edited in place.  A state
change produces a NEW Command object.  This keeps the audit trail
trustworthy, because nothing can quietly rewrite history.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CommandStatus(str, Enum):
    """
    Every state a command can be in.

    Normal path:   DRAFT -> APPROVED -> UPLINKED -> CONFIRMED
    Ends early:    DRAFT -> REJECTED  (operator declined)
    Ends badly:    UPLINKED -> FAILED (uplink or execution failed)
                   APPROVED -> FAILED (could not be uplinked)

    Inheriting from str makes the value serialise to plain JSON text such as
    "DRAFT", which is what will travel over Kafka and the API.
    """

    DRAFT     = "DRAFT"       # written and validated, waiting for a human
    APPROVED  = "APPROVED"    # a human operator signed it off
    UPLINKED  = "UPLINKED"    # sent to the satellite during a contact window
    CONFIRMED = "CONFIRMED"   # satellite acknowledged execution (final)
    FAILED    = "FAILED"      # uplink or execution failed (final)
    REJECTED  = "REJECTED"    # operator declined the draft (final)


class ActorKind(str, Enum):
    """
    Who is performing an action.

    The distinction matters for one rule: only an OPERATOR (a person) may
    approve a command.  Automation (SYSTEM) may author, uplink and confirm,
    but never approve.
    """

    OPERATOR = "OPERATOR"
    SYSTEM   = "SYSTEM"


class Actor(BaseModel):
    """
    The identity behind an action.

    For now this is just a name and a kind.  Real authentication (proving
    the person is who they claim) arrives with S8-08.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, description="Operator login or service name")
    kind: ActorKind


class StatusChange(BaseModel):
    """
    One line in a command's audit trail: who moved it to which state, when,
    and why.  The first entry records the creation as DRAFT.
    """

    model_config = ConfigDict(frozen=True)

    status:    CommandStatus
    actor:     Actor
    timestamp: datetime
    reason:    Optional[str] = None   # free text, e.g. why it was rejected


class Command(BaseModel):
    """
    One spacecraft command and its full history.

    Schema version: 1.0
    """

    model_config = ConfigDict(frozen=True)

    # Unique ID generated when the command is authored.
    command_id: str

    # Target satellite, e.g. "SAT-001" or "SAT-MA1".
    satellite_id: str

    # One of the names in services/command/catalogue.py.
    command_type: str

    # The values that go with the command, e.g. {"rate_mbps": 50.0}.
    parameters: Dict[str, Any] = Field(default_factory=dict)

    # Current state.  Always equal to history[-1].status.
    status: CommandStatus

    # Optional key a caller supplies so the same request is not executed
    # twice.  S8-05 will enforce it; for now it is only stored.
    idempotency_key: Optional[str] = None

    # Who first wrote the command and when.
    created_by: Actor
    created_at: datetime

    # Audit trail, oldest first.  Never empty: the first entry is the
    # creation as DRAFT.
    history: List[StatusChange]


class CommandAck(BaseModel):
    """
    The satellite's answer to a command it received (Sprint 8, S8-06).

    Both sides use this: the simulated satellite produces it, and the
    ground-side bridge reads it to decide whether to confirm the command.

    accepted:  the satellite took the command in.  False means it was
               turned away (see reason), and nothing was executed.
    executed:  True only the FIRST time the command ran.  A repeat of an
               already-executed command is accepted but not executed again.
    """

    model_config = ConfigDict(frozen=True)

    command_id:   str
    satellite_id: str
    accepted:     bool
    executed:     bool
    reason:       str
    timestamp:    datetime
