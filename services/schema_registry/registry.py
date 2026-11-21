"""
services/schema_registry/registry.py

An in-memory schema registry (S8-01) that enforces compatibility (S8-02).

WHAT A REGISTRY IS FOR
──────────────────────
Avro bytes cannot be read without their schema, and sending the full schema
with every message would be wasteful.  So producers register the schema once
and receive a small integer ID; every message carries only that ID, and a
consumer looks the ID up here.

VOCABULARY (the same as the Confluent Schema Registry, so Sprint 9 can swap
the real one in without changing callers)
    subject   a named history of schemas, e.g. "telemetry-value".
              Kafka convention: "<topic>-value" for the message value.
    version   1, 2, 3 ... within one subject, assigned in order.
    schema ID global integer.  The SAME schema registered under two subjects
              gets the SAME ID, because an ID identifies a schema, not a
              (subject, version) pair.

BEHAVIOUR
    register(subject, schema)
        new schema, compatible    -> stored as the next version, new/existing ID
        schema already registered -> returns the existing ID (idempotent), no
                                     new version.  Producers can therefore
                                     call register() on every start-up.
        incompatible              -> IncompatibleSchemaError listing the reasons.

LIMITS: everything lives in process memory and is lost on restart; there is
no HTTP API and no authentication.  Thread-safe (one lock).
"""

import threading
from dataclasses import dataclass
from typing import Dict, List, Union

from services.schema_registry.avro_schema import (
    Schema, fingerprint, parse_schema,
)
from services.schema_registry.compatibility import CompatibilityMode, check_compatibility


class RegistryError(Exception):
    """Base class for registry failures."""


class SubjectNotFoundError(RegistryError):
    pass


class SchemaNotFoundError(RegistryError):
    pass


class IncompatibleSchemaError(RegistryError):
    """A proposed schema breaks the subject's compatibility rule."""

    def __init__(self, subject: str, mode: CompatibilityMode, problems: List[str]):
        self.subject = subject
        self.mode = mode
        self.problems = problems
        super().__init__(f"schema for subject {subject!r} is not {mode.value}-compatible: "
                         + "; ".join(problems))


@dataclass(frozen=True)
class RegisteredSchema:
    """One stored version of a subject."""
    subject: str
    version: int
    schema_id: int
    schema: Schema
    fingerprint: str


class SchemaRegistry:
    """See the module docstring."""

    def __init__(self, default_mode: CompatibilityMode = CompatibilityMode.BACKWARD):
        self._lock = threading.Lock()
        self._default_mode = default_mode
        self._by_id: Dict[int, Schema] = {}                  # schema id -> schema
        self._id_by_fingerprint: Dict[str, int] = {}         # identity -> schema id
        self._versions: Dict[str, List[RegisteredSchema]] = {}
        self._modes: Dict[str, CompatibilityMode] = {}
        self._next_id = 1

    # ── Compatibility configuration ──────────────────────────────────────────

    def set_compatibility(self, subject: str, mode: CompatibilityMode) -> None:
        """Choose the rule for one subject.  Applies to FUTURE registrations."""
        with self._lock:
            self._modes[subject] = CompatibilityMode(mode)

    def get_compatibility(self, subject: str) -> CompatibilityMode:
        with self._lock:
            return self._modes.get(subject, self._default_mode)

    # ── Registration ─────────────────────────────────────────────────────────

    def check(self, subject: str, schema: Union[Schema, str, dict]) -> List[str]:
        """
        Dry run: would this schema be accepted for the subject?
        Returns the list of problems (empty = acceptable).  Stores nothing.
        """
        candidate = schema if not isinstance(schema, (str, dict, list)) else parse_schema(schema)
        with self._lock:
            return self._problems(subject, candidate)

    def register(self, subject: str, schema: Union[Schema, str, dict]) -> int:
        """Register a schema under a subject and return its schema ID."""
        candidate = schema if not isinstance(schema, (str, dict, list)) else parse_schema(schema)
        fp = fingerprint(candidate)

        with self._lock:
            history = self._versions.setdefault(subject, [])

            # Already registered under this subject: idempotent, no new version.
            for existing in history:
                if existing.fingerprint == fp:
                    return existing.schema_id

            problems = self._problems(subject, candidate)
            if problems:
                if not history:
                    self._versions.pop(subject, None)   # leave no empty subject behind
                raise IncompatibleSchemaError(subject, self._modes.get(
                    subject, self._default_mode), problems)

            # The same schema under another subject keeps its existing ID.
            schema_id = self._id_by_fingerprint.get(fp)
            if schema_id is None:
                schema_id = self._next_id
                self._next_id += 1
                self._id_by_fingerprint[fp] = schema_id
                self._by_id[schema_id] = candidate

            history.append(RegisteredSchema(subject, len(history) + 1, schema_id,
                                            candidate, fp))
            return schema_id

    def _problems(self, subject: str, candidate: Schema) -> List[str]:
        """Compatibility problems for a candidate (caller holds the lock)."""
        history = self._versions.get(subject, [])
        mode = self._modes.get(subject, self._default_mode)
        return check_compatibility(candidate, [h.schema for h in history], mode)

    # ── Lookup ───────────────────────────────────────────────────────────────

    def find_id(self, subject: str, schema: Union[Schema, str, dict]) -> int:
        """
        The ID of a schema ALREADY registered under the subject, without
        registering anything.  For producers configured not to auto-register.
        """
        candidate = schema if not isinstance(schema, (str, dict, list)) else parse_schema(schema)
        fp = fingerprint(candidate)
        with self._lock:
            for existing in self._history(subject):
                if existing.fingerprint == fp:
                    return existing.schema_id
        raise SchemaNotFoundError(f"that schema is not registered under subject {subject!r}")

    def get_schema(self, schema_id: int) -> Schema:
        """The schema for an ID (what a consumer does with the ID in a message)."""
        with self._lock:
            try:
                return self._by_id[schema_id]
            except KeyError:
                raise SchemaNotFoundError(f"no schema with id {schema_id}") from None

    def get_version(self, subject: str, version: int) -> RegisteredSchema:
        with self._lock:
            history = self._history(subject)
            if not 1 <= version <= len(history):
                raise SchemaNotFoundError(f"subject {subject!r} has no version {version}")
            return history[version - 1]

    def latest(self, subject: str) -> RegisteredSchema:
        with self._lock:
            return self._history(subject)[-1]

    def versions(self, subject: str) -> List[int]:
        with self._lock:
            return [h.version for h in self._history(subject)]

    def subjects(self) -> List[str]:
        with self._lock:
            return sorted(s for s, h in self._versions.items() if h)

    def _history(self, subject: str) -> List[RegisteredSchema]:
        history = self._versions.get(subject)
        if not history:
            raise SubjectNotFoundError(f"unknown subject {subject!r}")
        return history
