"""
tests/test_command_api.py

Tests for the command REST endpoints (Sprint 9, patch C).

No broker, database or cache is needed: the endpoints are driven through
Starlette's TestClient, and the Kafka side is an in-memory bus.  The
FastAPI lifespan does not run under TestClient, so each test installs a
CommandRuntime on app.state.commands itself (exactly what the lifespan does).
"""

import json

import pytest
from fastapi.testclient import TestClient

from services.api.command_runtime import (
    AccountConfigError, CommandRuntime, build_runtime, parse_accounts,
)
from services.api.main import app
from services.command.kafka_uplink import AckListener
from services.messaging.bus import InMemoryBus
from services.satellite_simulator.command_listener import CommandListener
from services.satellite_simulator.telemetry import TelemetryGenerator
from shared.config import Settings

SAT = "SAT-001"

ACCOUNTS = [
    {"name": "stephen", "secret": "stephen-secret", "kind": "OPERATOR",
     "roles": ["AUTHOR", "APPROVER"]},
    {"name": "uplink",  "secret": "uplink-secret",  "kind": "SYSTEM",
     "roles": ["UPLINKER"]},
    {"name": "viewer",  "secret": "viewer-secret",  "kind": "OPERATOR",
     "roles": []},
    # A robot with APPROVER: allowed the role, but not human, so must be refused.
    {"name": "robot",   "secret": "robot-secret",   "kind": "SYSTEM",
     "roles": ["APPROVER"]},
]


def _settings(**over):
    base = dict(command_accounts_json=json.dumps(ACCOUNTS),
                commands_enabled=True, command_satellites=SAT)
    base.update(over)
    return Settings(**base)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def bus():
    return InMemoryBus()


@pytest.fixture
def runtime(bus):
    """A runtime wired to the in-memory bus (small hash cost keeps tests fast)."""
    rt = build_runtime(
        _settings(), publisher=bus,
        subscriber=bus.subscriber([AckListener.topic_for(SAT)]),
        hash_iterations=1000,
    )
    app.state.commands = rt
    yield rt
    app.state.commands = None


@pytest.fixture
def client(runtime):
    return TestClient(app)


def _login(client, name):
    r = client.post("/auth/login", json={"name": name, "secret": f"{name}-secret"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def stephen(client):
    return _login(client, "stephen")


@pytest.fixture
def uplinker(client):
    return _login(client, "uplink")


def _submit(client, headers, key=None, params=None):
    body = {"satellite_id": SAT, "command_type": "SET_SAFE_MODE",
            "parameters": {"enabled": True} if params is None else params}
    if key:
        body["idempotency_key"] = key
    return client.post("/commands", json=body, headers=headers)


def _approved(client, stephen):
    cid = _submit(client, stephen).json()["command_id"]
    assert client.post(f"/commands/{cid}/approve", headers=stephen).status_code == 200
    return cid


# ── Account configuration ────────────────────────────────────────────────────

class TestParseAccounts:

    def test_empty_means_no_accounts(self):
        assert parse_accounts("") == [] and parse_accounts("   ") == []

    def test_a_valid_list_is_parsed(self):
        accounts = parse_accounts(json.dumps(ACCOUNTS))
        assert [a["name"] for a in accounts] == ["stephen", "uplink", "viewer", "robot"]

    @pytest.mark.parametrize("raw", [
        "not json",
        '{"name": "x"}',                                   # not a list
        '["x"]',                                           # item not an object
        '[{"name": "a", "secret": "s", "kind": "OPERATOR"}]',                 # no roles
        '[{"name": "a", "secret": "s", "kind": "ALIEN", "roles": []}]',       # bad kind
        '[{"name": "a", "secret": "s", "kind": "OPERATOR", "roles": ["GOD"]}]',  # bad role
    ])
    def test_malformed_input_is_refused(self, raw):
        with pytest.raises(AccountConfigError):
            parse_accounts(raw)

    def test_no_accounts_builds_no_runtime(self):
        assert build_runtime(Settings(command_accounts_json="")) is None

    def test_a_short_secret_stops_start_up(self):
        bad = json.dumps([{"name": "a", "secret": "short", "kind": "OPERATOR", "roles": []}])
        with pytest.raises(ValueError):
            build_runtime(Settings(command_accounts_json=bad), hash_iterations=1000)

    def test_uplink_is_off_unless_commands_are_enabled(self):
        rt = build_runtime(_settings(commands_enabled=False), hash_iterations=1000)
        assert rt.sender is None and rt.ack_listener is None


# ── Availability ─────────────────────────────────────────────────────────────

class TestAvailability:

    def test_503_when_no_accounts_are_configured(self):
        app.state.commands = None
        c = TestClient(app)
        assert c.post("/auth/login", json={"name": "a", "secret": "b"}).status_code == 503
        assert c.get("/commands/x", headers={"Authorization": "Bearer t"}).status_code == 503

    def test_uplink_503_when_not_enabled(self, bus):
        rt = build_runtime(_settings(commands_enabled=False), hash_iterations=1000)
        app.state.commands = rt
        try:
            c = TestClient(app)
            h = _login(c, "uplink")
            r = c.post("/commands/whatever/uplink", headers=h)
            assert r.status_code == 503 and "not enabled" in r.json()["detail"]
        finally:
            app.state.commands = None


# ── Login and tokens ─────────────────────────────────────────────────────────

class TestAuth:

    def test_login_returns_a_bearer_token(self, client):
        r = client.post("/auth/login", json={"name": "stephen", "secret": "stephen-secret"})
        assert r.status_code == 200
        assert r.json()["token_type"] == "bearer" and len(r.json()["token"]) > 20

    def test_wrong_secret_and_unknown_name_look_identical(self, client):
        a = client.post("/auth/login", json={"name": "stephen", "secret": "wrong-secret"})
        b = client.post("/auth/login", json={"name": "nobody", "secret": "wrong-secret"})
        assert a.status_code == b.status_code == 401
        assert a.json() == b.json()
        assert a.headers["www-authenticate"] == "Bearer"

    def test_repeated_failures_lock_the_account_even_for_the_right_secret(self, client):
        for _ in range(5):
            client.post("/auth/login", json={"name": "viewer", "secret": "bad-secret"})
        r = client.post("/auth/login", json={"name": "viewer", "secret": "viewer-secret"})
        assert r.status_code == 401 and "locked" in r.json()["detail"]

    def test_missing_malformed_or_unknown_tokens_are_401(self, client):
        assert client.get("/commands/x").status_code == 401
        assert client.get("/commands/x", headers={"Authorization": "Basic abc"}).status_code == 401
        assert client.get("/commands/x", headers={"Authorization": "Bearer"}).status_code == 401
        assert client.get("/commands/x",
                          headers={"Authorization": "Bearer nonsense"}).status_code == 401

    def test_logout_ends_the_session(self, client, stephen):
        assert client.post("/auth/logout", headers=stephen).status_code == 204
        assert _submit(client, stephen).status_code == 401

    def test_logout_with_an_unknown_token_does_not_fail(self, client):
        r = client.post("/auth/logout", headers={"Authorization": "Bearer nonsense"})
        assert r.status_code == 204


# ── Submit, read, approve, reject ────────────────────────────────────────────

class TestSubmitAndApprove:

    def test_submit_creates_a_draft(self, client, stephen):
        r = _submit(client, stephen)
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "DRAFT" and body["created_by"]["name"] == "stephen"

    def test_the_same_idempotency_key_returns_the_first_command(self, client, stephen):
        first = _submit(client, stephen, key="k1")
        again = _submit(client, stephen, key="k1")
        assert first.status_code == 201 and again.status_code == 200
        assert again.json()["command_id"] == first.json()["command_id"]

    def test_a_reused_key_with_different_content_is_409(self, client, stephen):
        _submit(client, stephen, key="k1")
        r = _submit(client, stephen, key="k1", params={"enabled": False})
        assert r.status_code == 409

    def test_an_invalid_command_is_422(self, client, stephen):
        bad = client.post("/commands", headers=stephen, json={
            "satellite_id": SAT, "command_type": "SET_DOWNLINK_RATE",
            "parameters": {"rate_mbps": 9999}})
        assert bad.status_code == 422

    def test_a_malformed_body_is_422(self, client, stephen):
        assert client.post("/commands", headers=stephen, json={}).status_code == 422

    def test_read_returns_the_command_and_its_history(self, client, stephen):
        cid = _submit(client, stephen).json()["command_id"]
        r = client.get(f"/commands/{cid}", headers=stephen)
        assert r.status_code == 200 and len(r.json()["history"]) == 1

    def test_reading_an_unknown_command_is_404(self, client, stephen):
        assert client.get("/commands/nope", headers=stephen).status_code == 404

    def test_any_valid_session_can_read(self, client, stephen):
        cid = _submit(client, stephen).json()["command_id"]
        assert client.get(f"/commands/{cid}", headers=_login(client, "viewer")).status_code == 200

    def test_approve(self, client, stephen):
        cid = _submit(client, stephen).json()["command_id"]
        r = client.post(f"/commands/{cid}/approve", headers=stephen)
        assert r.status_code == 200 and r.json()["status"] == "APPROVED"

    def test_approving_twice_is_409(self, client, stephen):
        cid = _approved(client, stephen)
        assert client.post(f"/commands/{cid}/approve", headers=stephen).status_code == 409

    def test_approving_an_unknown_command_is_404(self, client, stephen):
        assert client.post("/commands/nope/approve", headers=stephen).status_code == 404

    def test_reject_needs_a_reason_and_records_it(self, client, stephen):
        cid = _submit(client, stephen).json()["command_id"]
        assert client.post(f"/commands/{cid}/reject", headers=stephen, json={}).status_code == 422
        r = client.post(f"/commands/{cid}/reject", headers=stephen, json={"reason": "not now"})
        assert r.status_code == 200 and r.json()["status"] == "REJECTED"
        assert r.json()["history"][-1]["reason"] == "not now"

    def test_a_rejected_command_cannot_then_be_approved(self, client, stephen):
        cid = _submit(client, stephen).json()["command_id"]
        client.post(f"/commands/{cid}/reject", headers=stephen, json={"reason": "no"})
        assert client.post(f"/commands/{cid}/approve", headers=stephen).status_code == 409


# ── Roles (403) ──────────────────────────────────────────────────────────────

class TestRoles:

    def test_an_account_without_author_cannot_submit(self, client):
        assert _submit(client, _login(client, "viewer")).status_code == 403

    def test_an_account_without_approver_cannot_approve_or_reject(self, client, stephen):
        cid = _submit(client, stephen).json()["command_id"]
        viewer = _login(client, "viewer")
        assert client.post(f"/commands/{cid}/approve", headers=viewer).status_code == 403
        assert client.post(f"/commands/{cid}/reject", headers=viewer,
                           json={"reason": "x"}).status_code == 403

    def test_a_system_account_cannot_approve_even_with_the_role(self, client, stephen):
        cid = _submit(client, stephen).json()["command_id"]
        r = client.post(f"/commands/{cid}/approve", headers=_login(client, "robot"))
        assert r.status_code == 403 and "human" in r.json()["detail"]

    def test_the_author_account_cannot_uplink(self, client, stephen):
        cid = _approved(client, stephen)
        assert client.post(f"/commands/{cid}/uplink", headers=stephen).status_code == 403

    def test_denials_are_audited(self, client, runtime):
        _submit(client, _login(client, "viewer"))
        denied = [e for e in runtime.secure.audit_log() if not e.allowed]
        assert denied and denied[-1].who == "viewer" and denied[-1].action == "submit"


# ── Uplink ───────────────────────────────────────────────────────────────────

class TestUplink:

    def test_uplink_publishes_and_leaves_the_command_uplinked(self, client, stephen, uplinker, bus):
        cid = _approved(client, stephen)
        r = client.post(f"/commands/{cid}/uplink", headers=uplinker)
        assert r.status_code == 200
        assert r.json()["sent"] is True and r.json()["command"]["status"] == "UPLINKED"
        published = bus.messages(f"commands.{SAT}.uplinked")
        assert len(published) == 1
        assert json.loads(published[0].value)["command_id"] == cid

    def test_a_second_uplink_sends_nothing(self, client, stephen, uplinker, bus):
        cid = _approved(client, stephen)
        client.post(f"/commands/{cid}/uplink", headers=uplinker)
        again = client.post(f"/commands/{cid}/uplink", headers=uplinker)
        assert again.status_code == 200 and again.json()["sent"] is False
        assert "already" in again.json()["reason"]
        assert len(bus.messages(f"commands.{SAT}.uplinked")) == 1

    def test_a_draft_cannot_be_uplinked(self, client, stephen, uplinker):
        cid = _submit(client, stephen).json()["command_id"]
        assert client.post(f"/commands/{cid}/uplink", headers=uplinker).status_code == 409

    def test_uplinking_an_unknown_command_is_404(self, client, uplinker):
        assert client.post("/commands/nope/uplink", headers=uplinker).status_code == 404

    def test_a_broker_outage_marks_the_command_failed(self, client, stephen, uplinker, bus):
        cid = _approved(client, stephen)
        bus.fail_publishes = True                       # the outage hook on InMemoryBus
        r = client.post(f"/commands/{cid}/uplink", headers=uplinker)
        assert r.status_code == 200 and r.json()["sent"] is False
        assert r.json()["command"]["status"] == "FAILED"
        assert client.get(f"/commands/{cid}", headers=stephen).json()["status"] == "FAILED"


# ── The whole loop over HTTP ─────────────────────────────────────────────────

class TestFullLoop:

    @pytest.fixture
    def satellite(self, bus):
        gen = TelemetryGenerator(satellite_id=SAT)
        gen.clear_state()
        yield gen, CommandListener(
            gen.command_receiver,
            bus.subscriber([CommandListener.uplinked_topic(SAT)]), bus, SAT)
        gen.clear_state()

    def _round_trip(self, runtime, listener):
        listener.drain()                 # satellite reads its command and answers
        runtime.ack_listener.drain()     # ground reads the answer

    def test_in_contact_the_command_ends_confirmed(self, client, runtime, stephen, uplinker, satellite):
        gen, listener = satellite
        gen.satellite.in_contact = True
        cid = _approved(client, stephen)
        client.post(f"/commands/{cid}/uplink", headers=uplinker)
        assert client.get(f"/commands/{cid}", headers=stephen).json()["status"] == "UPLINKED"
        self._round_trip(runtime, listener)
        final = client.get(f"/commands/{cid}", headers=stephen).json()
        assert final["status"] == "CONFIRMED"
        assert [h["status"] for h in final["history"]] == [
            "DRAFT", "APPROVED", "UPLINKED", "CONFIRMED"]

    def test_out_of_contact_the_command_ends_failed(self, client, runtime, stephen, uplinker, satellite):
        gen, listener = satellite
        gen.satellite.in_contact = False
        cid = _approved(client, stephen)
        client.post(f"/commands/{cid}/uplink", headers=uplinker)
        self._round_trip(runtime, listener)
        final = client.get(f"/commands/{cid}", headers=stephen).json()
        assert final["status"] == "FAILED" and "no contact" in final["history"][-1]["reason"]


# ── Runtime background thread ────────────────────────────────────────────────

class TestRuntimeThread:

    def test_the_ack_thread_starts_and_stops(self, runtime):
        runtime.start_ack_thread()
        runtime.start_ack_thread()                      # a second start is harmless
        assert runtime._thread is not None and runtime._thread.is_alive()
        runtime.stop()
        assert runtime._thread is None

    def test_a_runtime_without_a_listener_has_nothing_to_start(self):
        rt = build_runtime(_settings(commands_enabled=False), hash_iterations=1000)
        rt.start_ack_thread()
        assert rt._thread is None
        rt.stop()


# ── CORS ─────────────────────────────────────────────────────────────────────

class TestCors:

    def test_a_browser_may_post_with_an_authorization_header(self, client):
        r = client.options("/commands", headers={
            "Origin": "http://localhost:8501",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        assert r.status_code == 200
        assert "POST" in r.headers["access-control-allow-methods"]
