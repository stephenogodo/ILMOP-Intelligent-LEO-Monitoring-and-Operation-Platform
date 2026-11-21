"""
services/schema_registry/defaults.py

Builds a registry pre-loaded with this project's schema history (S8-01/02).

The history is registered IN ORDER, exactly as it happened, so that the
registry's compatibility checks run on every real change:

    telemetry-value  v1  telemetry_v1.avsc  Telemetry 2.0 (no orbit_type)
                     v2  telemetry_v2.avsc  Telemetry 2.1 (adds orbit_type, ADR-016)
    alarms-value     v1  alarm_v1.avsc

If a future edit to a committed file broke compatibility, building this
registry would raise IncompatibleSchemaError, and the tests would fail.

Both subjects use BACKWARD compatibility (the registry default): consumers
are upgraded first and must always be able to read what producers wrote
earlier.  Note this means a new orbit_type SYMBOL is not allowed to slip in
unnoticed: the enum has no default symbol, so an old consumer would
reject it rather than treat an unknown orbit as LEO (ADR-016).
"""

import json

from services.schema_registry.generate import (
    ALARM_SUBJECT, AVRO_DIR, TELEMETRY_SUBJECT,
)
from services.schema_registry.registry import SchemaRegistry


def load_avsc(filename: str) -> dict:
    """Read one committed schema file."""
    return json.loads((AVRO_DIR / filename).read_text(encoding="utf-8"))


def build_default_registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register(TELEMETRY_SUBJECT, load_avsc("telemetry_v1.avsc"))
    registry.register(TELEMETRY_SUBJECT, load_avsc("telemetry_v2.avsc"))
    registry.register(ALARM_SUBJECT, load_avsc("alarm_v1.avsc"))
    return registry
