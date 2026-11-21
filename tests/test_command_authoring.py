"""
tests/test_command_authoring.py

Tests for command validation and DRAFT creation (S8-03).
No Kafka, database or network is needed: the module is pure logic.
"""

from datetime import datetime, timezone

import pytest

from shared.schemas.command_schema import Actor, ActorKind, CommandStatus
from services.command.authoring import (
    CommandValidationError,
    author_command,
    validate_command,
)

# A human operator used as the author in most tests.
OPERATOR = Actor(name="stephen", kind=ActorKind.OPERATOR)
FIXED_TIME = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)


# ── Valid commands ────────────────────────────────────────────────────────────

class TestValidCommands:

    def test_valid_command_has_no_errors(self):
        assert validate_command("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 50.0}) == []

    def test_integer_is_accepted_as_a_number(self):
        assert validate_command("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 50}) == []

    def test_limits_are_inclusive(self):
        # Exactly the minimum and the maximum must both be allowed.
        assert validate_command("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 0.0}) == []
        assert validate_command("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 200.0}) == []

    def test_hybrid_scenario_satellite_ids_are_accepted(self):
        assert validate_command("SAT-MA1", "SET_SAFE_MODE", {"enabled": True}) == []
        assert validate_command("SAT-LB6", "SET_SAFE_MODE", {"enabled": False}) == []

    def test_choice_parameter_accepts_listed_value(self):
        assert validate_command("SAT-001", "REBOOT_SUBSYSTEM", {"subsystem": "ADCS"}) == []


# ── Invalid commands ──────────────────────────────────────────────────────────

class TestInvalidCommands:

    def test_unknown_command_type(self):
        errors = validate_command("SAT-001", "SELF_DESTRUCT", {})
        assert len(errors) == 1
        assert "unknown command_type" in errors[0]

    @pytest.mark.parametrize("bad_id", ["", "sat-001", "SAT 001", "001", "SAT-"])
    def test_bad_satellite_ids(self, bad_id):
        errors = validate_command(bad_id, "SET_SAFE_MODE", {"enabled": True})
        assert any("satellite_id" in e for e in errors)

    def test_missing_required_parameter(self):
        errors = validate_command("SAT-001", "SET_DOWNLINK_RATE", {})
        assert any("missing required parameter 'rate_mbps'" in e for e in errors)

    def test_unexpected_parameter_catches_typos(self):
        errors = validate_command(
            "SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 10, "rate_mpbs": 10}
        )
        assert any("unexpected parameter 'rate_mpbs'" in e for e in errors)

    def test_value_below_minimum(self):
        errors = validate_command("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": -1})
        assert any("below the minimum" in e for e in errors)

    def test_value_above_maximum(self):
        errors = validate_command("SAT-001", "SET_TELEMETRY_INTERVAL", {"interval_s": 61})
        assert any("above the maximum" in e for e in errors)

    def test_boolean_is_not_accepted_as_a_number(self):
        # True == 1 in Python; it must still be rejected for a numeric field.
        errors = validate_command("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": True})
        assert any("must be a number" in e for e in errors)

    def test_string_is_not_accepted_as_a_number(self):
        errors = validate_command("SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": "fast"})
        assert any("must be a number" in e for e in errors)

    def test_integer_is_not_accepted_as_a_boolean(self):
        errors = validate_command("SAT-001", "SET_SAFE_MODE", {"enabled": 1})
        assert any("must be true or false" in e for e in errors)

    def test_choice_outside_allowed_set(self):
        errors = validate_command("SAT-001", "REBOOT_SUBSYSTEM", {"subsystem": "WARP_DRIVE"})
        assert any("must be one of" in e for e in errors)

    def test_all_errors_are_reported_together(self):
        # Three separate problems in one request: all three must be listed.
        errors = validate_command("bad", "SET_DOWNLINK_RATE", {"rate_mbps": 999, "x": 1})
        assert len(errors) == 3


# ── author_command ────────────────────────────────────────────────────────────

class TestAuthorCommand:

    def test_creates_a_draft(self):
        cmd = author_command(
            "SAT-001", "SET_DOWNLINK_RATE", {"rate_mbps": 50}, OPERATOR, now=FIXED_TIME
        )
        assert cmd.status is CommandStatus.DRAFT
        assert cmd.satellite_id == "SAT-001"
        assert cmd.parameters == {"rate_mbps": 50}
        assert cmd.created_at == FIXED_TIME

    def test_history_starts_with_the_creation(self):
        cmd = author_command(
            "SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR, now=FIXED_TIME
        )
        assert len(cmd.history) == 1
        assert cmd.history[0].status is CommandStatus.DRAFT
        assert cmd.history[0].actor == OPERATOR

    def test_each_command_gets_a_unique_id(self):
        a = author_command("SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR)
        b = author_command("SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR)
        assert a.command_id != b.command_id

    def test_idempotency_key_is_stored(self):
        cmd = author_command(
            "SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR,
            idempotency_key="req-42",
        )
        assert cmd.idempotency_key == "req-42"

    def test_invalid_command_raises_with_all_errors(self):
        with pytest.raises(CommandValidationError) as exc:
            author_command("bad", "SELF_DESTRUCT", {}, OPERATOR)
        assert len(exc.value.errors) >= 2

    def test_caller_changing_their_dict_does_not_change_the_command(self):
        params = {"rate_mbps": 50}
        cmd = author_command("SAT-001", "SET_DOWNLINK_RATE", params, OPERATOR)
        params["rate_mbps"] = 999
        assert cmd.parameters["rate_mbps"] == 50

    def test_command_is_frozen(self):
        cmd = author_command("SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR)
        with pytest.raises(Exception):
            cmd.status = CommandStatus.APPROVED
