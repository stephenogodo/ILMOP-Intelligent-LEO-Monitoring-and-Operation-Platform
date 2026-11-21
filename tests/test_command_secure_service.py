"""
tests/test_command_secure_service.py

Tests for the token-based, role-checked command front door (S8-08),
including the whole command path driven through real logins.
"""

import pytest

from shared.schemas.command_schema import ActorKind, CommandStatus
from services.command.auth import (
    AuthenticationError,
    AuthorizationError,
    AuthService,
    Role,
)
from services.command.lifecycle import ApprovalError
from services.command.secure_service import SecureCommandService
from services.satellite_simulator.telemetry import TelemetryGenerator


def _setup(require_second_person=False):
    """
    A secure service with four accounts:
      stephen  human, AUTHOR + APPROVER
      amara    human, AUTHOR + APPROVER       (a second person)
      uplink   system, UPLINKER
      rogue    system, with the APPROVER role wrongly granted
      viewer   human, no roles
    and a dict of logged-in tokens.
    """
    auth = AuthService(hash_iterations=1000)
    auth.register("stephen", "stephen-secret", ActorKind.OPERATOR, [Role.AUTHOR, Role.APPROVER])
    auth.register("amara",   "amara-secret!",  ActorKind.OPERATOR, [Role.AUTHOR, Role.APPROVER])
    auth.register("uplink",  "uplink-secret",  ActorKind.SYSTEM,   [Role.UPLINKER])
    auth.register("rogue",   "rogue-secret!",  ActorKind.SYSTEM,   [Role.APPROVER])
    auth.register("viewer",  "viewer-secret",  ActorKind.OPERATOR, [])
    tokens = {n: auth.login(n, s) for n, s in [
        ("stephen", "stephen-secret"), ("amara", "amara-secret!"),
        ("uplink", "uplink-secret"), ("rogue", "rogue-secret!"),
        ("viewer", "viewer-secret"),
    ]}
    return SecureCommandService(auth, require_second_person=require_second_person), tokens


def _submit(svc, tokens, who="stephen"):
    return svc.submit(tokens[who], "SAT-001", "SET_SAFE_MODE", {"enabled": True})


# ── Authentication at the front door ──────────────────────────────────────────

class TestAuthenticationRequired:

    def test_forged_token_cannot_submit(self):
        svc, _ = _setup()
        with pytest.raises(AuthenticationError):
            svc.submit("forged", "SAT-001", "SET_SAFE_MODE", {"enabled": True})

    def test_forged_token_cannot_approve(self):
        svc, tokens = _setup()
        cid = _submit(svc, tokens).command.command_id
        with pytest.raises(AuthenticationError):
            svc.approve("forged", cid)

    def test_forged_token_cannot_read(self):
        svc, tokens = _setup()
        cid = _submit(svc, tokens).command.command_id
        with pytest.raises(AuthenticationError):
            svc.get("forged", cid)

    def test_logged_out_token_is_refused(self):
        svc, tokens = _setup()
        svc._auth.logout(tokens["stephen"])
        with pytest.raises(AuthenticationError):
            _submit(svc, tokens)


# ── Authorisation (roles) ─────────────────────────────────────────────────────

class TestRoles:

    def test_author_can_submit(self):
        svc, tokens = _setup()
        assert _submit(svc, tokens).created is True

    def test_user_without_author_role_cannot_submit(self):
        svc, tokens = _setup()
        with pytest.raises(AuthorizationError):
            _submit(svc, tokens, who="viewer")

    def test_submitted_command_records_the_verified_author(self):
        svc, tokens = _setup()
        command = _submit(svc, tokens, who="amara").command
        assert command.created_by.name == "amara"

    def test_approver_can_approve(self):
        svc, tokens = _setup()
        cid = _submit(svc, tokens).command.command_id
        assert svc.approve(tokens["stephen"], cid).status is CommandStatus.APPROVED

    def test_user_without_approver_role_cannot_approve(self):
        svc, tokens = _setup()
        cid = _submit(svc, tokens).command.command_id
        with pytest.raises(AuthorizationError):
            svc.approve(tokens["viewer"], cid)

    def test_system_account_cannot_approve_even_with_the_role(self):
        # The human-only rule: rogue was wrongly GIVEN the APPROVER role.
        svc, tokens = _setup()
        cid = _submit(svc, tokens).command.command_id
        with pytest.raises(AuthorizationError, match="human"):
            svc.approve(tokens["rogue"], cid)
        assert svc.get(tokens["stephen"], cid).status is CommandStatus.DRAFT

    def test_approver_can_reject_with_a_reason(self):
        svc, tokens = _setup()
        cid = _submit(svc, tokens).command.command_id
        rejected = svc.reject(tokens["stephen"], cid, "wrong satellite")
        assert rejected.status is CommandStatus.REJECTED

    def test_reject_still_requires_a_reason(self):
        svc, tokens = _setup()
        cid = _submit(svc, tokens).command.command_id
        with pytest.raises(ApprovalError):
            svc.reject(tokens["stephen"], cid, "  ")

    def test_only_uplinker_can_uplink(self):
        svc, tokens = _setup()
        gen = TelemetryGenerator(satellite_id="SAT-001")
        gen.clear_state()
        try:
            gen.satellite.in_contact = True
            cid = _submit(svc, tokens).command.command_id
            svc.approve(tokens["stephen"], cid)
            with pytest.raises(AuthorizationError):
                svc.uplink(tokens["stephen"], cid, gen.command_receiver)
        finally:
            gen.clear_state()


# ── Two-person rule ───────────────────────────────────────────────────────────

class TestTwoPersonRule:

    def test_author_may_approve_own_command_by_default(self):
        svc, tokens = _setup(require_second_person=False)
        cid = _submit(svc, tokens, who="stephen").command.command_id
        assert svc.approve(tokens["stephen"], cid).status is CommandStatus.APPROVED

    def test_author_cannot_approve_own_command_when_rule_is_on(self):
        svc, tokens = _setup(require_second_person=True)
        cid = _submit(svc, tokens, who="stephen").command.command_id
        with pytest.raises(ApprovalError):
            svc.approve(tokens["stephen"], cid)

    def test_a_different_operator_can_approve_when_rule_is_on(self):
        svc, tokens = _setup(require_second_person=True)
        cid = _submit(svc, tokens, who="stephen").command.command_id
        assert svc.approve(tokens["amara"], cid).status is CommandStatus.APPROVED


# ── The whole path with real logins ───────────────────────────────────────────

class TestFullPath:

    def test_login_to_confirmed(self):
        svc, tokens = _setup(require_second_person=True)
        gen = TelemetryGenerator(satellite_id="SAT-001")
        gen.clear_state()
        try:
            gen.satellite.in_contact = True

            cid = _submit(svc, tokens, who="stephen").command.command_id
            svc.approve(tokens["amara"], cid)
            result = svc.uplink(tokens["uplink"], cid, gen.command_receiver)

            assert result.sent is True
            stored = svc.get(tokens["stephen"], cid)
            assert stored.status is CommandStatus.CONFIRMED
            # The audit trail names the REAL people and services involved.
            assert [h.actor.name for h in stored.history] == [
                "stephen", "amara", "uplink", "uplink",
            ]
            assert gen.satellite.safe_mode is True
        finally:
            gen.clear_state()


# ── Security audit log ────────────────────────────────────────────────────────

class TestAuditLog:

    def test_allowed_actions_are_logged(self):
        svc, tokens = _setup()
        cid = _submit(svc, tokens).command.command_id
        svc.approve(tokens["stephen"], cid)
        events = svc.audit_log()
        assert [(e.who, e.action, e.allowed) for e in events] == [
            ("stephen", "submit", True), ("stephen", "approve", True),
        ]

    def test_denied_role_is_logged_with_a_reason(self):
        svc, tokens = _setup()
        cid = _submit(svc, tokens).command.command_id
        with pytest.raises(AuthorizationError):
            svc.approve(tokens["viewer"], cid)
        denied = [e for e in svc.audit_log() if not e.allowed]
        assert len(denied) == 1
        assert denied[0].who == "viewer" and "APPROVER" in denied[0].detail

    def test_forged_token_is_logged_as_unauthenticated(self):
        svc, _ = _setup()
        with pytest.raises(AuthenticationError):
            svc.submit("forged", "SAT-001", "SET_SAFE_MODE", {"enabled": True})
        event = svc.audit_log()[-1]
        assert event.who == "(unauthenticated)" and event.allowed is False

    def test_returned_log_is_a_copy(self):
        svc, tokens = _setup()
        _submit(svc, tokens)
        svc.audit_log().clear()
        assert len(svc.audit_log()) == 1
