"""
services/command/catalogue.py

The catalogue of commands ILMOP will accept, and the rules for their
parameters (Sprint 8, S8-03).

WHY A CATALOGUE
───────────────
A spacecraft only understands a fixed set of commands.  Letting an operator
type any command name with any parameters would let typos and nonsense reach
the approval step.  Instead, every command type is declared here once, with
the exact parameters it takes and the range each may have.  Authoring
(authoring.py) checks every new command against this table.

THE LIMITS BELOW ARE ILLUSTRATIVE
─────────────────────────────────
Real limits come from a satellite's command database.  The values here are
sensible for the simulator.  To change a limit, edit the number below; no
other code needs to change.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ParameterSpec:
    """
    The rule for ONE parameter of a command.

    kind:      "bool", "float" or "choice".
    minimum:   for "float", the smallest allowed value (inclusive).
    maximum:   for "float", the largest allowed value (inclusive).
    choices:   for "choice", the only allowed values.
    required:  if True, the command is invalid without this parameter.
    """

    kind:     str
    minimum:  Optional[float] = None
    maximum:  Optional[float] = None
    choices:  Tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class CommandSpec:
    """
    The rule for ONE command type: a description for operators and the
    parameters it takes, keyed by parameter name.
    """

    description: str
    parameters:  dict   # name (str) -> ParameterSpec


# The catalogue itself.  Key = command_type string used everywhere else.
CATALOGUE = {

    "SET_SAFE_MODE": CommandSpec(
        description="Put the satellite into, or take it out of, safe mode.",
        parameters={
            "enabled": ParameterSpec(kind="bool"),
        },
    ),

    "SET_DOWNLINK_RATE": CommandSpec(
        description="Change the downlink data rate used during contact windows.",
        parameters={
            "rate_mbps": ParameterSpec(kind="float", minimum=0.0, maximum=200.0),
        },
    ),

    "SET_TELEMETRY_INTERVAL": CommandSpec(
        description="Change how often the satellite emits a telemetry record.",
        parameters={
            "interval_s": ParameterSpec(kind="float", minimum=1.0, maximum=60.0),
        },
    ),

    "REBOOT_SUBSYSTEM": CommandSpec(
        description="Reboot one subsystem of the satellite.",
        parameters={
            "subsystem": ParameterSpec(
                kind="choice",
                choices=("OBC", "ADCS", "COMMS", "EPS"),
            ),
        },
    ),
}
