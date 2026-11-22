"""
tests/test_command_kafka_loop.py

Tests for the command loop over a message bus (Sprint 9):

    ground sender -> uplinked topic -> satellite listener -> receiver
                  <- acks topic     <-
    ack listener -> CONFIRMED or FAILED

Everything runs on the in-memory bus with a real TelemetryGenerator as the
satellite, so no broker is needed.  Each test builds only what it needs.
"""

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from services.command.auth import AuthService, AuthorizationError, Role
from services.command.kafka_uplink import (
    ACK_LISTENER_ACTOR, AckListener, make_bus_sender,
)
from services.command.lifecycle import ApprovalError, InvalidTransitionError
from services.command.secure_service import SecureCommandService
from services.command.service import CommandNotFoundError, CommandService
from services.messaging.bus import InMemoryBus
from services.satellite_simulator.command_listener import CommandListener
from services.satellite_simulator.telemetry import TelemetryGenerator
from shared.config import settings
from shared.schemas.command_schema import (
    Actor, ActorKind, Command, CommandAck, CommandStatus,
)

OPERATOR = Actor(name="stephen", kind=ActorKind.OPERATOR)
UPLINKER = Actor(name="uplink-service", kind=ActorKind.SYSTEM)
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


# ── Building blocks ───────────────────────────────────────────────────────────

@pytest.fixture
def generator():
    gen = TelemetryGenerator(satellite_id="SAT-001")
    gen.clear_state()
    yield gen
    gen.clear_state()


def _uplinked_topic(sat="SAT-001"):
    return settings.commands_topic(sat, "uplinked")


def _acks_topic(sat="SAT-001"):
    return settings.commands_topic(sat, "acks")


def _approved(service, command_type="SET_SAFE_MODE", params=None, sat="SAT-001", key=None):
    """Submit and approve a command, return its ID."""
    params = {"enabled": True} if params is None else params
    cid = service.submit(sat, command_type, params, OPERATOR, key).command.command_id
    service.approve(cid, OPERATOR)
    return cid


def _uplinked(service, bus):
    """An approved command, already dispatched through the bus (so UPLINKED)."""
    cid = _approved(service)
    service.dispatch(cid, UPLINKER, make_bus_sender(bus))
    return cid


def _ack(command_id, accepted=True, reason="executed", sat="SAT-001", executed=None):
    return CommandAck(
        command_id=command_id, satellite_id=sat, accepted=accepted,
        executed=accepted if executed is None else executed, reason=reason, timestamp=NOW)


def _publish_ack(bus, ack, sat="SAT-001"):
    bus.publish(_acks_topic(sat), sat, ack.model_dump_json().encode())


class Loop:
    """The whole loop wired onto one in-memory bus, for the end-to-end tests."""

    def __init__(self, generator, satellite_id="SAT-001"):
        self.bus = InMemoryBus()
        self.service = CommandService()
        self.generator = generator
        self.sender = make_bus_sender(self.bus)
        self.satellite = CommandListener(
            generator.command_receiver,
            self.bus.subscriber([_uplinked_topic(satellite_id)]),
            self.bus, satellite_id)
        self.ground = AckListener(self.service, self.bus.subscriber([_acks_topic(satellite_id)]))

    def uplink(self, cid):
        return self.service.dispatch(cid, UPLINKER, self.sender)

    def run_satellite_and_ground(self):
        """Let the satellite read its commands, then the ground read the answers."""
        self.satellite.drain()
        self.ground.drain()


# ── CommandService.fail ───────────────────────────────────────────────────────

class TestServiceFail:

    def test_fail_an_uplinked_command(self):
        service, bus = CommandService(), InMemoryBus()
        cid = _uplinked(service, bus)
        failed = service.fail(cid, UPLINKER, "no contact")
        assert failed.status is CommandStatus.FAILED
        assert failed.history[-1].reason == "no contact"
        assert service.get(cid).status is CommandStatus.FAILED

    def test_fail_an_approved_command(self):
        service = CommandService()
        cid = _approved(service)
        assert service.fail(cid, UPLINKER, "could not be sent").status is CommandStatus.FAILED

    def test_a_repeated_failure_changes_nothing(self):
        service, bus = CommandService(), InMemoryBus()
        cid = _uplinked(service, bus)
        first = service.fail(cid, UPLINKER, "no contact")
        second = service.fail(cid, UPLINKER, "no contact")
        assert second == first and len(second.history) == len(first.history)

    @pytest.mark.parametrize("prepare", ["draft", "confirmed"])
    def test_other_states_cannot_fail(self, prepare):
        service, bus = CommandService(), InMemoryBus()
        if prepare == "draft":
            cid = service.submit("SAT-001", "SET_SAFE_MODE", {"enabled": True},
                                 OPERATOR).command.command_id
        else:
            cid = _uplinked(service, bus)
            service.confirm(cid, UPLINKER)
        with pytest.raises(InvalidTransitionError):
            service.fail(cid, UPLINKER, "too late")

    def test_a_reason_is_required(self):
        service, bus = CommandService(), InMemoryBus()
        cid = _uplinked(service, bus)
        with pytest.raises(ApprovalError):
            service.fail(cid, UPLINKER, "  ")

    def test_unknown_command(self):
        with pytest.raises(CommandNotFoundError):
            CommandService().fail("nope", UPLINKER, "x")


# ── The sender ────────────────────────────────────────────────────────────────

class TestBusSender:

    def test_publishes_the_command_to_its_satellites_topic(self):
        service, bus = CommandService(), InMemoryBus()
        cid = _approved(service)
        result = service.dispatch(cid, UPLINKER, make_bus_sender(bus))
        assert result.sent is True
        (message,) = bus.messages(_uplinked_topic())
        assert message.key == "SAT-001"
        sent = Command.model_validate_json(message.value)
        assert sent.command_id == cid and sent.status is CommandStatus.UPLINKED

    def test_the_command_waits_as_uplinked_until_acknowledged(self):
        service, bus = CommandService(), InMemoryBus()
        cid = _approved(service)
        service.dispatch(cid, UPLINKER, make_bus_sender(bus))
        assert service.get(cid).status is CommandStatus.UPLINKED

    def test_a_broker_outage_marks_the_command_failed(self):
        service, bus = CommandService(), InMemoryBus()
        cid = _approved(service)
        bus.fail_publishes = True
        result = service.dispatch(cid, UPLINKER, make_bus_sender(bus))
        assert result.sent is False and "uplink error" in result.reason
        assert service.get(cid).status is CommandStatus.FAILED
        assert bus.messages() == []                    # nothing half-sent

    def test_two_satellites_use_two_topics(self):
        service, bus = CommandService(), InMemoryBus()
        service.dispatch(_approved(service, sat="SAT-001"), UPLINKER, make_bus_sender(bus))
        service.dispatch(_approved(service, sat="SAT-002"), UPLINKER, make_bus_sender(bus))
        assert len(bus.messages(_uplinked_topic("SAT-001"))) == 1
        assert len(bus.messages(_uplinked_topic("SAT-002"))) == 1

    def test_twenty_racing_uplinks_publish_exactly_once(self):
        """The claim-before-send guard still holds across the bus."""
        service, bus = CommandService(), InMemoryBus()
        cid = _approved(service)
        sender = make_bus_sender(bus)
        barrier = threading.Barrier(20)

        def attempt():
            barrier.wait()
            service.dispatch(cid, UPLINKER, sender)

        threads = [threading.Thread(target=attempt) for _ in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(bus.messages(_uplinked_topic())) == 1


# ── The acknowledgement listener: every decision ─────────────────────────────

class TestAckListener:

    @pytest.fixture
    def parts(self):
        bus, service = InMemoryBus(), CommandService()
        listener = AckListener(service, bus.subscriber([_acks_topic()]))
        return bus, service, listener

    def test_an_accepted_ack_confirms(self, parts):
        bus, service, listener = parts
        cid = _uplinked(service, bus)
        _publish_ack(bus, _ack(cid))
        assert listener.drain() == 1
        stored = service.get(cid)
        assert stored.status is CommandStatus.CONFIRMED
        assert stored.history[-1].actor == ACK_LISTENER_ACTOR
        assert listener.stats.confirmed == 1

    def test_a_rejected_ack_fails_with_the_satellites_reason(self, parts):
        bus, service, listener = parts
        cid = _uplinked(service, bus)
        _publish_ack(bus, _ack(cid, accepted=False, reason="no contact"))
        listener.drain()
        stored = service.get(cid)
        assert stored.status is CommandStatus.FAILED
        assert "no contact" in stored.history[-1].reason
        assert listener.stats.failed == 1

    def test_a_repeated_accepted_ack_is_a_harmless_duplicate(self, parts):
        bus, service, listener = parts
        cid = _uplinked(service, bus)
        for _ in range(3):
            _publish_ack(bus, _ack(cid))
        listener.drain()
        stored = service.get(cid)
        assert stored.status is CommandStatus.CONFIRMED
        assert [h.status for h in stored.history].count(CommandStatus.CONFIRMED) == 1
        assert (listener.stats.confirmed, listener.stats.duplicate) == (1, 2)

    def test_a_repeated_rejection_is_a_harmless_duplicate(self, parts):
        bus, service, listener = parts
        cid = _uplinked(service, bus)
        for _ in range(2):
            _publish_ack(bus, _ack(cid, accepted=False, reason="no contact"))
        listener.drain()
        assert (listener.stats.failed, listener.stats.duplicate) == (1, 1)

    def test_a_rejection_for_a_confirmed_command_is_a_conflict(self, parts):
        bus, service, listener = parts
        cid = _uplinked(service, bus)
        _publish_ack(bus, _ack(cid))
        _publish_ack(bus, _ack(cid, accepted=False, reason="late no"))
        listener.drain()
        assert service.get(cid).status is CommandStatus.CONFIRMED      # unchanged
        assert listener.stats.conflicting == 1

    def test_an_acceptance_for_a_failed_command_is_a_conflict(self, parts):
        bus, service, listener = parts
        cid = _uplinked(service, bus)
        _publish_ack(bus, _ack(cid, accepted=False, reason="no contact"))
        _publish_ack(bus, _ack(cid))
        listener.drain()
        assert service.get(cid).status is CommandStatus.FAILED
        assert listener.stats.conflicting == 1

    def test_an_ack_for_a_command_never_sent_is_a_conflict(self, parts):
        bus, service, listener = parts
        cid = _approved(service)                        # APPROVED, never uplinked
        _publish_ack(bus, _ack(cid))
        listener.drain()
        assert service.get(cid).status is CommandStatus.APPROVED
        assert listener.stats.conflicting == 1

    def test_the_listener_can_never_approve_a_draft(self, parts):
        bus, service, listener = parts
        cid = service.submit("SAT-001", "SET_SAFE_MODE", {"enabled": True},
                             OPERATOR).command.command_id          # still DRAFT
        _publish_ack(bus, _ack(cid))
        listener.drain()
        assert service.get(cid).status is CommandStatus.DRAFT
        assert listener.stats.conflicting == 1

    def test_an_unknown_command_is_counted(self, parts):
        bus, _, listener = parts
        _publish_ack(bus, _ack("does-not-exist"))
        listener.drain()
        assert listener.stats.unknown == 1

    def test_an_ack_from_the_wrong_satellite_is_ignored(self, parts):
        bus, service, listener = parts
        cid = _uplinked(service, bus)
        _publish_ack(bus, _ack(cid, sat="SAT-999"))
        listener.drain()
        assert service.get(cid).status is CommandStatus.UPLINKED
        assert listener.stats.mismatched == 1

    @pytest.mark.parametrize("raw", [b"", b"garbage", b"{}", b'{"command_id": 5}',
                                     b"\xff\xfe", b"[1,2]", b"null"])
    def test_malformed_messages_are_skipped(self, parts, raw):
        bus, _, listener = parts
        bus.publish(_acks_topic(), "SAT-001", raw)
        listener.drain()
        assert listener.stats.malformed == 1

    def test_reading_continues_after_bad_messages(self, parts):
        bus, service, listener = parts
        cid = _uplinked(service, bus)
        bus.publish(_acks_topic(), "SAT-001", b"garbage")
        _publish_ack(bus, _ack("unknown"))
        _publish_ack(bus, _ack(cid))
        listener.drain()
        assert service.get(cid).status is CommandStatus.CONFIRMED

    def test_nothing_waiting_handles_nothing(self, parts):
        assert parts[2].drain() == 0 and parts[2].poll_once() is False

    def test_drain_respects_its_limit(self, parts):
        bus, _, listener = parts
        for _ in range(5):
            _publish_ack(bus, _ack("x"))
        assert listener.drain(max_messages=3) == 3

    def test_topic_for(self):
        assert AckListener.topic_for("SAT-001") == "commands.SAT-001.acks"

    def test_run_stops_when_told(self, parts):
        bus, service, listener = parts
        cid = _uplinked(service, bus)
        _publish_ack(bus, _ack(cid))
        stop = threading.Event()
        thread = threading.Thread(target=listener.run, args=(stop, 0.01))
        thread.start()
        for _ in range(200):
            if service.get(cid).status is CommandStatus.CONFIRMED:
                break
            threading.Event().wait(0.01)
        stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert service.get(cid).status is CommandStatus.CONFIRMED

    def test_run_survives_an_unexpected_error(self):
        """The first poll raises; the loop must log it and poll again."""
        stop = threading.Event()
        calls = []

        def poll(_timeout):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("broker hiccup")
            stop.set()                 # second poll: ask the loop to finish
            return None

        subscriber = MagicMock()
        subscriber.poll.side_effect = poll
        AckListener(CommandService(), subscriber).run(stop, 0.0)   # must return, not raise
        assert len(calls) == 2


# ── The satellite-side listener ───────────────────────────────────────────────

class TestCommandListener:

    def _uplink_one(self, loop, command_type="SET_SAFE_MODE", params=None):
        cid = _approved(loop.service, command_type, params)
        loop.uplink(cid)
        return cid

    def test_a_command_in_contact_is_executed_and_acknowledged(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = True
        cid = self._uplink_one(loop)
        loop.satellite.drain()
        (message,) = loop.bus.messages(_acks_topic())
        ack = CommandAck.model_validate_json(message.value)
        assert (ack.command_id, ack.accepted, ack.executed) == (cid, True, True)
        assert message.key == "SAT-001"
        assert generator.generate().safe_mode is True              # the effect is real

    def test_a_command_out_of_contact_is_rejected(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = False
        self._uplink_one(loop)
        loop.satellite.drain()
        ack = CommandAck.model_validate_json(loop.bus.messages(_acks_topic())[0].value)
        assert ack.accepted is False and ack.reason == "no contact"

    def test_a_command_delivered_twice_runs_once(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = True
        cid = self._uplink_one(loop, "SET_DOWNLINK_RATE", {"rate_mbps": 50.0})
        original = loop.bus.messages(_uplinked_topic())[0]
        loop.bus.publish(original.topic, original.key, original.value)      # redelivery
        loop.satellite.drain()
        acks = [CommandAck.model_validate_json(m.value) for m in loop.bus.messages(_acks_topic())]
        assert len(acks) == 2 and acks[0] == acks[1]                # same answer both times
        assert generator.command_receiver.duplicates_ignored == 1
        assert loop.satellite.stats.received == 2

    def test_a_malformed_command_is_counted_and_not_acknowledged(self, generator):
        loop = Loop(generator)
        for raw in (b"garbage", b"{}", b"", b'{"command_id": 1}'):
            loop.bus.publish(_uplinked_topic(), "SAT-001", raw)
        loop.satellite.drain()
        assert loop.satellite.stats.malformed == 4
        assert loop.bus.messages(_acks_topic()) == []

    def test_reading_continues_after_a_malformed_command(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = True
        loop.bus.publish(_uplinked_topic(), "SAT-001", b"garbage")
        self._uplink_one(loop)
        loop.satellite.drain()
        assert loop.satellite.stats.acknowledged == 1

    def test_a_command_for_another_satellite_is_rejected_by_the_receiver(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = True
        cid = _approved(loop.service, sat="SAT-002")
        service_cmd = loop.service.get(cid)
        # Misrouted: SAT-002's command appears on SAT-001's topic.
        loop.bus.publish(_uplinked_topic("SAT-001"), "SAT-001",
                         service_cmd.model_copy(update={"status": CommandStatus.UPLINKED})
                         .model_dump_json().encode())
        loop.satellite.drain()
        ack = CommandAck.model_validate_json(loop.bus.messages(_acks_topic())[0].value)
        assert ack.accepted is False and ack.reason == "wrong satellite"

    def test_a_failed_ack_publish_is_counted_and_the_command_still_ran(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = True
        self._uplink_one(loop)
        loop.bus.fail_publishes = True
        loop.satellite.drain()
        assert loop.satellite.stats.ack_failures == 1
        assert generator.command_receiver._executed                  # it did execute

    def test_each_satellite_reads_only_its_own_topic(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = True
        other = _approved(loop.service, sat="SAT-002")
        loop.service.dispatch(other, UPLINKER, loop.sender)
        loop.satellite.drain()
        assert loop.satellite.stats.received == 0
        assert len(loop.bus.messages(_uplinked_topic("SAT-002"))) == 1

    def test_topic_name(self):
        assert CommandListener.uplinked_topic("SAT-001") == "commands.SAT-001.uplinked"


# ── The whole loop ────────────────────────────────────────────────────────────

class TestFullLoop:

    def test_command_travels_out_and_is_confirmed(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = True
        cid = _approved(loop.service)
        result = loop.uplink(cid)
        assert result.sent and loop.service.get(cid).status is CommandStatus.UPLINKED

        loop.run_satellite_and_ground()

        stored = loop.service.get(cid)
        assert stored.status is CommandStatus.CONFIRMED
        assert [h.status for h in stored.history] == [
            CommandStatus.DRAFT, CommandStatus.APPROVED,
            CommandStatus.UPLINKED, CommandStatus.CONFIRMED]
        assert generator.generate().safe_mode is True

    def test_out_of_contact_ends_failed_with_the_reason(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = False
        cid = _approved(loop.service)
        loop.uplink(cid)
        loop.run_satellite_and_ground()
        stored = loop.service.get(cid)
        assert stored.status is CommandStatus.FAILED
        assert "no contact" in stored.history[-1].reason

    def test_a_new_command_succeeds_once_contact_returns(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = False
        first = _approved(loop.service)
        loop.uplink(first)
        loop.run_satellite_and_ground()
        assert loop.service.get(first).status is CommandStatus.FAILED

        generator.satellite.in_contact = True
        second = _approved(loop.service)                           # a NEW command
        loop.uplink(second)
        loop.run_satellite_and_ground()
        assert loop.service.get(second).status is CommandStatus.CONFIRMED

    def test_everything_delivered_twice_still_ends_correctly(self, generator):
        """Kafka redelivers both the command and the acknowledgement."""
        loop = Loop(generator)
        generator.satellite.in_contact = True
        cid = _approved(loop.service, "SET_TELEMETRY_INTERVAL", {"interval_s": 5.0})
        loop.uplink(cid)
        command_message = loop.bus.messages(_uplinked_topic())[0]
        loop.bus.publish(command_message.topic, command_message.key, command_message.value)
        loop.satellite.drain()
        ack_message = loop.bus.messages(_acks_topic())[0]
        loop.bus.publish(ack_message.topic, ack_message.key, ack_message.value)
        loop.ground.drain()

        assert loop.service.get(cid).status is CommandStatus.CONFIRMED
        assert generator.command_receiver.telemetry_interval_s == 5.0
        assert generator.command_receiver.duplicates_ignored == 1
        assert loop.ground.stats.confirmed == 1 and loop.ground.stats.duplicate >= 1

    def test_a_second_uplink_attempt_publishes_nothing_more(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = True
        cid = _approved(loop.service)
        loop.uplink(cid)
        again = loop.uplink(cid)
        assert again.sent is False
        assert len(loop.bus.messages(_uplinked_topic())) == 1

    def test_broker_outage_at_send_time_is_visible_not_silent(self, generator):
        loop = Loop(generator)
        generator.satellite.in_contact = True
        cid = _approved(loop.service)
        loop.bus.fail_publishes = True
        loop.uplink(cid)
        loop.bus.fail_publishes = 0
        loop.run_satellite_and_ground()
        assert loop.service.get(cid).status is CommandStatus.FAILED
        assert generator.command_receiver._executed == {}          # nothing ran


# ── Through the authenticated service ────────────────────────────────────────

class TestSecureUplinkAsync:

    @pytest.fixture
    def secure(self):
        auth = AuthService(hash_iterations=1000)
        auth.register("stephen", "stephen-secret", ActorKind.OPERATOR,
                      [Role.AUTHOR, Role.APPROVER])
        auth.register("uplink", "uplink-secret", ActorKind.SYSTEM, [Role.UPLINKER])
        auth.register("viewer", "viewer-secret", ActorKind.OPERATOR, [])
        tokens = {n: auth.login(n, s) for n, s in [
            ("stephen", "stephen-secret"), ("uplink", "uplink-secret"),
            ("viewer", "viewer-secret")]}
        return SecureCommandService(auth), tokens

    def _approved(self, svc, tokens):
        cid = svc.submit(tokens["stephen"], "SAT-001", "SET_SAFE_MODE",
                         {"enabled": True}).command.command_id
        svc.approve(tokens["stephen"], cid)
        return cid

    def test_uplink_async_sends_and_leaves_the_command_uplinked(self, secure):
        svc, tokens = secure
        bus = InMemoryBus()
        cid = self._approved(svc, tokens)
        result = svc.uplink_async(tokens["uplink"], cid, make_bus_sender(bus))
        assert result.sent is True
        assert svc.get(tokens["stephen"], cid).status is CommandStatus.UPLINKED
        assert len(bus.messages(_uplinked_topic())) == 1

    def test_uplink_async_needs_the_uplinker_role(self, secure):
        svc, tokens = secure
        bus = InMemoryBus()
        cid = self._approved(svc, tokens)
        with pytest.raises(AuthorizationError):
            svc.uplink_async(tokens["viewer"], cid, make_bus_sender(bus))
        with pytest.raises(AuthorizationError):
            svc.uplink_async(tokens["stephen"], cid, make_bus_sender(bus))   # author/approver only
        assert bus.messages() == []

    def test_uplink_async_rejects_a_forged_token(self, secure):
        svc, tokens = secure
        bus = InMemoryBus()
        cid = self._approved(svc, tokens)
        with pytest.raises(Exception):
            svc.uplink_async("forged", cid, make_bus_sender(bus))
        assert bus.messages() == []

    def test_uplink_async_is_recorded_in_the_security_audit_log(self, secure):
        svc, tokens = secure
        cid = self._approved(svc, tokens)
        svc.uplink_async(tokens["uplink"], cid, make_bus_sender(InMemoryBus()))
        assert any(e.action == "uplink" and e.allowed and e.command_id == cid
                   for e in svc.audit_log())

    def test_the_whole_loop_through_logins(self, secure, generator):
        svc, tokens = secure
        loop = Loop(generator)
        generator.satellite.in_contact = True
        # The ack listener must work on the SAME stored commands as the secure service.
        loop.service = svc._service
        loop.ground = AckListener(svc._service, loop.bus.subscriber([_acks_topic()]))

        cid = self._approved(svc, tokens)
        svc.uplink_async(tokens["uplink"], cid, loop.sender)
        loop.run_satellite_and_ground()
        assert svc.get(tokens["stephen"], cid).status is CommandStatus.CONFIRMED


# ── The telemetry producer hook ───────────────────────────────────────────────

class TestProducerHook:

    @pytest.fixture
    def prod(self, monkeypatch):
        from services.kafka_producer import producer as prod
        monkeypatch.setattr(prod, "Producer", lambda conf: MagicMock())
        monkeypatch.setattr(prod.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))
        return prod

    def test_commands_are_off_by_default(self, prod):
        assert prod.TelemetryProducer("SAT-001")._command_listener is None

    def test_an_enabled_producer_drains_commands_every_tick(self, prod):
        listener = MagicMock()
        prod.TelemetryProducer("SAT-001", command_listener=listener).run()
        listener.drain.assert_called_once()

    def test_a_command_failure_never_stops_telemetry(self, prod, caplog):
        listener = MagicMock()
        listener.drain.side_effect = RuntimeError("command trouble")
        producer = prod.TelemetryProducer("SAT-001", command_listener=listener)
        with caplog.at_level("ERROR"):
            producer.run()                                   # must not raise
        assert "command listener failed" in caplog.text
        assert producer._producer.produce.called             # telemetry was still published

    def test_the_setting_builds_a_listener_on_the_right_topics(self, prod, monkeypatch):
        built = {}

        class FakeSubscriber:
            def __init__(self, group, topics):
                built["group"], built["topics"] = group, topics

        monkeypatch.setattr(settings, "commands_enabled", True)
        monkeypatch.setattr("services.messaging.bus.KafkaSubscriber", FakeSubscriber)
        monkeypatch.setattr("services.messaging.bus.KafkaPublisher", MagicMock)
        producer = prod.TelemetryProducer("SAT-001")
        assert isinstance(producer._command_listener, CommandListener)
        assert built == {"group": "ilmop-satellite-commands",
                         "topics": ["commands.SAT-001.uplinked"]}
