"""
services/schema_registry/compatibility.py

Schema resolution rules and compatibility modes (S8-02).

THE ONE IDEA BEHIND ALL OF THIS
───────────────────────────────
Data is written with one schema (the WRITER schema) and read with another
(the READER schema).  They are not always the same, because producers and
consumers are upgraded at different times.  Avro defines exactly when a
reader can make sense of a writer's data; that is "schema resolution".

    can_read(writer, reader)  ->  list of problems  (empty list = it works)

Everything else in this file is built on that one function.

THE RULES (applied by field NAME, never by position)
────────────────────────────────────────────────────
  record  : names must match.
            • field in both        -> the two field types must resolve.
            • field only in WRITER -> reader simply ignores it.     OK
            • field only in READER -> reader uses its DEFAULT; with
                                      no default it is a PROBLEM.
  enum    : names must match.  A writer symbol the reader does not have is
            a PROBLEM unless the reader's enum declares a default symbol.
  union   : every writer branch must resolve to some reader branch.
  primitive: same type.  One promotion allowed: writer long -> reader double.
            Logical types must agree (we are stricter than the Avro spec
            here on purpose: a timestamp must not silently become a number).

THE MODES (what a registry enforces when a new version is registered)
─────────────────────────────────────────────────────────────────────
  BACKWARD : the NEW schema can read data written with the OLD one.
             Upgrade CONSUMERS first, then producers.  (Our default.)
  FORWARD  : the OLD schema can read data written with the NEW one.
             Upgrade PRODUCERS first.
  FULL     : both of the above.
  NONE     : no checking.
  *_TRANSITIVE variants check against EVERY earlier version, not just the
  latest one.  Needed when old data (or an old consumer) can still be around
  after several upgrades.
"""

from enum import Enum
from typing import List, Sequence

from services.schema_registry.avro_schema import (
    EnumSchema, PrimitiveSchema, RecordSchema, Schema, UnionSchema, describe,
)


class CompatibilityMode(str, Enum):
    NONE = "NONE"
    BACKWARD = "BACKWARD"
    BACKWARD_TRANSITIVE = "BACKWARD_TRANSITIVE"
    FORWARD = "FORWARD"
    FORWARD_TRANSITIVE = "FORWARD_TRANSITIVE"
    FULL = "FULL"
    FULL_TRANSITIVE = "FULL_TRANSITIVE"


# ── Resolution: can this reader understand that writer? ──────────────────────

def can_read(writer: Schema, reader: Schema) -> List[str]:
    """
    Return the reasons `reader` cannot read data written with `writer`.
    An empty list means it can.  Each reason names the path to the problem,
    e.g. "$.orbit_type: writer symbol 'MEO_X' is not in the reader enum".
    """
    problems: List[str] = []
    _resolve(writer, reader, "$", problems)
    return problems


def _resolve(w: Schema, r: Schema, path: str, out: List[str]) -> None:
    """Recursive worker: appends problems to `out`."""

    # Writer is a union: whichever branch was written must be readable.
    if isinstance(w, UnionSchema):
        for branch in w.branches:
            _resolve(branch, r, f"{path}|{describe(branch)}", out)
        return

    # Reader is a union but writer is not: the writer's type must match at
    # least one reader branch.
    if isinstance(r, UnionSchema):
        if not any(not can_read(w, b) for b in r.branches):
            out.append(f"{path}: writer type {describe(w)} matches no branch of "
                       f"reader {describe(r)}")
        return

    if isinstance(w, PrimitiveSchema) and isinstance(r, PrimitiveSchema):
        if w.name == r.name and w.logical_type == r.logical_type:
            return
        if w.name == "long" and w.logical_type is None and r.name == "double" \
                and r.logical_type is None:
            return                                  # the one allowed promotion
        out.append(f"{path}: cannot read {describe(w)} as {describe(r)}")
        return

    if isinstance(w, EnumSchema) and isinstance(r, EnumSchema):
        if w.name != r.name:
            out.append(f"{path}: enum name differs ({w.name} vs {r.name})")
            return
        if r.default is None:
            for sym in w.symbols:
                if sym not in r.symbols:
                    out.append(f"{path}: writer symbol {sym!r} is not in the reader "
                               f"enum and the reader enum has no default")
        return

    if isinstance(w, RecordSchema) and isinstance(r, RecordSchema):
        if w.name != r.name:
            out.append(f"{path}: record name differs ({w.name} vs {r.name})")
            return
        for rf in r.fields:
            wf = w.field(rf.name)
            if wf is None:
                # Reader wants a field the writer never wrote.
                if not rf.has_default:
                    out.append(f"{path}.{rf.name}: reader field has no default and "
                               f"the writer does not have it")
            else:
                _resolve(wf.type, rf.type, f"{path}.{rf.name}", out)
        # Fields only the writer has are ignored: nothing to check.
        return

    out.append(f"{path}: cannot read {describe(w)} as {describe(r)}")


# ── Modes: check a candidate new schema against earlier versions ─────────────

def check_compatibility(candidate: Schema, previous: Sequence[Schema],
                        mode: CompatibilityMode) -> List[str]:
    """
    Check `candidate` (a proposed new version) against `previous` versions
    (oldest first) under `mode`.  Returns a list of problems, empty if OK.
    """
    if mode == CompatibilityMode.NONE or not previous:
        return []

    transitive = mode.value.endswith("_TRANSITIVE")
    # Non-transitive modes only look at the most recent version.
    targets = list(previous) if transitive else [previous[-1]]
    base = mode.value.replace("_TRANSITIVE", "")

    problems: List[str] = []
    for old in targets:
        if base in ("BACKWARD", "FULL"):
            # New schema must read old data.
            for p in can_read(writer=old, reader=candidate):
                problems.append(f"[BACKWARD: new cannot read old] {p}")
        if base in ("FORWARD", "FULL"):
            # Old schema must read new data.
            for p in can_read(writer=candidate, reader=old):
                problems.append(f"[FORWARD: old cannot read new] {p}")
    return problems
