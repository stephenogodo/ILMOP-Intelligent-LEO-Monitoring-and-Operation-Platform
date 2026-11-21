"""
services/schema_registry/generate.py

Generate Avro schemas from the Pydantic models (S8-01).

WHY GENERATE INSTEAD OF WRITING BY HAND
───────────────────────────────────────
The Pydantic class is already the source of truth for the Python code.  If
the Avro schema were written separately, the two would drift: someone adds a
field to the class, forgets the .avsc, and producers silently publish data
that consumers cannot read.  So the Avro schema is DERIVED from the class,
the result is committed as an .avsc file (so humans can review changes in a
diff), and a test fails if the committed file no longer matches what the
class produces.

TYPE MAPPING
    str                  -> "string"
    bool                 -> "boolean"
    float                -> "double"
    int                  -> "long"
    datetime             -> long, logicalType timestamp-micros
    Literal["A","B"]     -> enum (named after the field, e.g. orbit_type -> OrbitType)
    Optional[X]          -> union [null, X]  (default null)
Field defaults on the class become Avro field defaults.  A default is what
lets an OLD message (written before the field existed) be read by NEW code,
so a new field WITHOUT a default is not backward compatible.

Run this file to rewrite the committed files:
    python -m services.schema_registry.generate
"""

import json
import types
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional, Union, get_args, get_origin

from pydantic import BaseModel

from shared.schemas.alarm_schema import Alarm
from shared.schemas.telemetry_schema import Telemetry

# Where the committed schema files live.
AVRO_DIR = Path(__file__).resolve().parents[2] / "shared" / "schemas" / "avro"


def _camel(field_name: str) -> str:
    """orbit_type -> OrbitType (used to name generated enums)."""
    return "".join(part.capitalize() for part in field_name.split("_"))


def _avro_type(annotation: Any, field_name: str) -> Any:
    """Map one Python type annotation to Avro schema JSON."""
    origin = get_origin(annotation)

    # Optional[X] is Union[X, None] (or X | None on newer Pythons).
    if origin in (Union, types.UnionType):
        args = get_args(annotation)
        non_null = [a for a in args if a is not type(None)]
        if len(args) == 2 and len(non_null) == 1:
            return ["null", _avro_type(non_null[0], field_name)]
        raise TypeError(f"{field_name}: only Optional[X] unions are supported")

    if origin is Literal:
        symbols = list(get_args(annotation))
        if not all(isinstance(s, str) for s in symbols):
            raise TypeError(f"{field_name}: only string Literals are supported")
        return {"type": "enum", "name": _camel(field_name), "symbols": symbols}

    # bool must be tested before int: in Python, bool is a subclass of int.
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "long"
    if annotation is float:
        return "double"
    if annotation is str:
        return "string"
    if annotation is datetime:
        return {"type": "long", "logicalType": "timestamp-micros"}
    raise TypeError(f"{field_name}: unsupported type {annotation!r}")


def avro_schema_for_model(model: type, name: str, namespace: str,
                          doc: Optional[str] = None) -> dict:
    """Build the Avro schema (as JSON-compatible data) for a Pydantic model."""
    fields = []
    for field_name, info in model.model_fields.items():
        entry: dict = {"name": field_name, "type": _avro_type(info.annotation, field_name)}
        if not info.is_required():
            entry["default"] = info.default
        fields.append(entry)
    schema = {"type": "record", "name": name, "namespace": namespace, "fields": fields}
    if doc:
        schema["doc"] = doc
    return schema


# ── The schemas of this project ──────────────────────────────────────────────

TELEMETRY_SUBJECT = "telemetry-value"
ALARM_SUBJECT = "alarms-value"


def telemetry_schema_json() -> dict:
    """Current Telemetry schema (version 2.1 of the Pydantic model)."""
    return avro_schema_for_model(
        Telemetry, "Telemetry", "ilmop.telemetry",
        "Satellite telemetry snapshot. Generated from shared/schemas/telemetry_schema.py.")


def alarm_schema_json() -> dict:
    """Current Alarm schema."""
    return avro_schema_for_model(
        Alarm, "Alarm", "ilmop.alarm",
        "Anomaly alarm. Generated from shared/schemas/alarm_schema.py.")


def write_current_schemas() -> None:
    """Rewrite the committed files for the CURRENT models."""
    AVRO_DIR.mkdir(parents=True, exist_ok=True)
    for filename, schema in (("telemetry_v2.avsc", telemetry_schema_json()),
                             ("alarm_v1.avsc", alarm_schema_json())):
        (AVRO_DIR / filename).write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_current_schemas()
    print(f"wrote schemas to {AVRO_DIR}")
