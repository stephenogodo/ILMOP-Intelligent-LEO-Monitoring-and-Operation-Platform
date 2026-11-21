"""
services/command/authoring.py

Command authoring and validation (Sprint 8, S8-03).

WHAT THIS MODULE DOES
─────────────────────
Takes a request ("send SET_DOWNLINK_RATE with rate_mbps=50 to SAT-001"),
checks it against the catalogue, and either:

    * returns a new Command in the DRAFT state, or
    * raises CommandValidationError listing EVERY problem found.

Nothing is created when validation fails, so a malformed command can never
reach the approval step.  This is the "rejected before approval" rule.

There is no I/O here (no Kafka, no database), so the module is fast to test
and safe to call from anywhere: an API route, the dashboard, or an
automated policy.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from shared.schemas.command_schema import (
    Actor,
    Command,
    CommandStatus,
    StatusChange,
)
from services.command.catalogue import CATALOGUE, ParameterSpec

# Satellite IDs in ILMOP look like SAT-001, SAT-MA1, SAT-LB6.
# This rejects empty strings, lower case, spaces and other typos.
_SATELLITE_ID_PATTERN = re.compile(r"^SAT-[A-Z0-9]+$")


class CommandValidationError(ValueError):
    """
    Raised when a command request is invalid.

    errors holds one readable message per problem, so the operator can fix
    everything in one go instead of one error at a time.
    """

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _check_parameter(name: str, value: Any, spec: ParameterSpec) -> Optional[str]:
    """
    Check ONE parameter value against its rule.

    Returns an error message, or None if the value is fine.
    """
    if spec.kind == "bool":
        # Must be a real True/False.  (1 and 0 are not accepted as booleans.)
        if not isinstance(value, bool):
            return f"parameter '{name}' must be true or false"
        return None

    if spec.kind == "float":
        # Python quirk: bool is a subclass of int, so isinstance(True, int)
        # is True.  Without this explicit test, rate_mbps=True would be
        # accepted as the number 1.  Reject booleans first.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"parameter '{name}' must be a number"
        if spec.minimum is not None and value < spec.minimum:
            return f"parameter '{name}' is {value}, below the minimum {spec.minimum}"
        if spec.maximum is not None and value > spec.maximum:
            return f"parameter '{name}' is {value}, above the maximum {spec.maximum}"
        return None

    if spec.kind == "choice":
        if value not in spec.choices:
            allowed = ", ".join(spec.choices)
            return f"parameter '{name}' must be one of: {allowed}"
        return None

    # A catalogue entry with an unknown kind is a programming error,
    # not an operator error, so fail loudly.
    raise ValueError(f"unknown parameter kind '{spec.kind}' for '{name}'")


def validate_command(
    satellite_id: str,
    command_type: str,
    parameters: Dict[str, Any],
) -> List[str]:
    """
    Check a command request and return a list of error messages.

    An EMPTY list means the request is valid.  This function never raises
    for bad operator input; author_command() turns a non-empty list into
    an exception.  Keeping the two apart lets the dashboard show errors
    live while an operator types, without creating anything.
    """
    errors: List[str] = []

    # 1. Satellite ID format.
    if not _SATELLITE_ID_PATTERN.match(satellite_id or ""):
        errors.append(
            f"satellite_id '{satellite_id}' is not valid (expected e.g. SAT-001)"
        )

    # 2. Command type must be in the catalogue.  If it is not, the
    #    parameter checks below have nothing to compare against, so stop.
    spec = CATALOGUE.get(command_type)
    if spec is None:
        known = ", ".join(sorted(CATALOGUE))
        errors.append(f"unknown command_type '{command_type}' (known: {known})")
        return errors

    # 3. Required parameters must be present.
    for name, pspec in spec.parameters.items():
        if pspec.required and name not in parameters:
            errors.append(f"missing required parameter '{name}'")

    # 4. No unexpected parameters.  A typo such as "rate_mpbs" is caught
    #    here instead of being silently ignored.
    for name in parameters:
        if name not in spec.parameters:
            errors.append(f"unexpected parameter '{name}'")

    # 5. Each supplied, expected parameter must have a valid value.
    for name, value in parameters.items():
        pspec = spec.parameters.get(name)
        if pspec is None:
            continue   # already reported in step 4
        problem = _check_parameter(name, value, pspec)
        if problem:
            errors.append(problem)

    return errors


def author_command(
    satellite_id: str,
    command_type: str,
    parameters: Dict[str, Any],
    author: Actor,
    idempotency_key: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Command:
    """
    Validate a request and create a new Command in the DRAFT state.

    Raises CommandValidationError (listing every problem) if invalid.

    author:           who is writing the command (an operator or a system).
    idempotency_key:  optional caller-supplied key; stored for S8-05.
    now:              optional fixed time, so tests are deterministic.
                      Production callers leave it unset.
    """
    errors = validate_command(satellite_id, command_type, parameters)
    if errors:
        raise CommandValidationError(errors)

    created_at = now or datetime.now(timezone.utc)

    return Command(
        command_id=str(uuid.uuid4()),
        satellite_id=satellite_id,
        command_type=command_type,
        # Copy, so a caller changing their dict later cannot alter the command.
        parameters=dict(parameters),
        status=CommandStatus.DRAFT,
        idempotency_key=idempotency_key,
        created_by=author,
        created_at=created_at,
        # The first audit entry records the creation.
        history=[
            StatusChange(
                status=CommandStatus.DRAFT,
                actor=author,
                timestamp=created_at,
                reason="authored",
            )
        ],
    )
