"""
tests/test_messaging_bus.py

Tests for the message bus (Sprint 9): the in-memory bus used by the other
tests, and the thin Kafka wrappers, checked against hand-made fakes of the
confluent_kafka client (there is no broker in the test environment).
"""

from unittest.mock import MagicMock

import pytest

from services.messaging.bus import (
    BusMessage, InMemoryBus, KafkaPublisher, KafkaSubscriber, PublishError,
)
from shared.config import Settings


# ── InMemoryBus ───────────────────────────────────────────────────────────────

class TestInMemoryBus:

    def test_messages_come_back_in_publish_order(self):
        bus = InMemoryBus()
        sub = bus.subscriber(["t"])
        for i in range(5):
            bus.publish("t", "k", f"m{i}".encode())
        assert [sub.poll().value for _ in range(5)] == [b"m0", b"m1", b"m2", b"m3", b"m4"]

    def test_empty_poll_returns_none(self):
        assert InMemoryBus().subscriber(["t"]).poll() is None

    def test_a_subscriber_only_sees_its_topics(self):
        bus = InMemoryBus()
        sub = bus.subscriber(["a"])
        bus.publish("b", None, b"other")
        bus.publish("a", None, b"mine")
        assert sub.poll() == BusMessage("a", None, b"mine")
        assert sub.poll() is None

    def test_two_subscribers_each_see_every_message(self):
        bus = InMemoryBus()
        s1, s2 = bus.subscriber(["t"]), bus.subscriber(["t"])
        bus.publish("t", "k", b"x")
        assert s1.poll().value == b"x" and s2.poll().value == b"x"

    def test_a_late_subscriber_can_skip_history(self):
        bus = InMemoryBus()
        bus.publish("t", None, b"old")
        late = bus.subscriber(["t"], from_beginning=False)
        bus.publish("t", None, b"new")
        assert late.poll().value == b"new"
        assert late.poll() is None

    def test_a_subscriber_can_start_from_the_beginning(self):
        bus = InMemoryBus()
        bus.publish("t", None, b"old")
        assert bus.subscriber(["t"]).poll().value == b"old"

    def test_messages_helper_filters_by_topic(self):
        bus = InMemoryBus()
        bus.publish("a", None, b"1")
        bus.publish("b", None, b"2")
        assert [m.value for m in bus.messages("a")] == [b"1"]
        assert len(bus.messages()) == 2

    def test_published_bytes_are_copied(self):
        bus = InMemoryBus()
        data = bytearray(b"abc")
        bus.publish("t", None, data)
        data[0] = ord("z")
        assert bus.messages()[0].value == b"abc"

    def test_simulated_outage_counts_down(self):
        bus = InMemoryBus()
        bus.fail_publishes = 2
        for _ in range(2):
            with pytest.raises(PublishError):
                bus.publish("t", None, b"x")
        bus.publish("t", None, b"ok")                  # third works
        assert len(bus.messages()) == 1

    def test_simulated_outage_until_cleared(self):
        bus = InMemoryBus()
        bus.fail_publishes = True
        for _ in range(5):
            with pytest.raises(PublishError):
                bus.publish("t", None, b"x")
        bus.fail_publishes = 0
        bus.publish("t", None, b"ok")

    def test_a_failed_publish_stores_nothing(self):
        bus = InMemoryBus()
        bus.fail_publishes = 1
        with pytest.raises(PublishError):
            bus.publish("t", None, b"x")
        assert bus.messages() == []


# ── Fakes of the confluent_kafka client ──────────────────────────────────────

class FakeProducer:
    """Records produce() calls; reports success or a scripted failure on flush()."""

    def __init__(self, delivery_error=None, left_in_queue=0, produce_error=None):
        self.sent, self._callbacks = [], []
        self.delivery_error, self.left_in_queue = delivery_error, left_in_queue
        self.produce_error = produce_error

    def produce(self, topic, key, value, callback):
        if self.produce_error:
            raise self.produce_error
        self.sent.append((topic, key, value))
        self._callbacks.append(callback)

    def flush(self, timeout):
        for cb in self._callbacks:
            cb(self.delivery_error, None)
        self._callbacks = []
        return self.left_in_queue


class FakeKafkaMessage:
    def __init__(self, topic="t", key=b"k", value=b"v", error=None):
        self._t, self._k, self._v, self._e = topic, key, value, error

    def topic(self): return self._t
    def key(self): return self._k
    def value(self): return self._v
    def error(self): return self._e


class FakeConsumer:
    def __init__(self, scripted=()):
        self.scripted = list(scripted)
        self.subscribed, self.closed = None, False

    def subscribe(self, topics): self.subscribed = topics
    def poll(self, timeout): return self.scripted.pop(0) if self.scripted else None
    def close(self): self.closed = True


# ── KafkaPublisher ────────────────────────────────────────────────────────────

class TestKafkaPublisher:

    def test_publishes_and_waits_for_the_broker(self):
        fake = FakeProducer()
        KafkaPublisher(fake).publish("commands.SAT-001.uplinked", "SAT-001", b"payload")
        assert fake.sent == [("commands.SAT-001.uplinked", b"SAT-001", b"payload")]

    def test_a_missing_key_is_allowed(self):
        fake = FakeProducer()
        KafkaPublisher(fake).publish("t", None, b"x")
        assert fake.sent[0][1] is None

    def test_a_broker_rejection_raises(self):
        with pytest.raises(PublishError, match="rejected"):
            KafkaPublisher(FakeProducer(delivery_error="MSG_TIMED_OUT")).publish("t", "k", b"x")

    def test_a_flush_timeout_raises(self):
        with pytest.raises(PublishError, match="timed out"):
            KafkaPublisher(FakeProducer(left_in_queue=1)).publish("t", "k", b"x")

    def test_a_full_local_queue_raises(self):
        with pytest.raises(PublishError, match="could not queue"):
            KafkaPublisher(FakeProducer(produce_error=BufferError("queue full"))).publish(
                "t", "k", b"x")

    def test_the_flush_timeout_is_passed_on(self):
        fake = FakeProducer()
        fake.flush = MagicMock(return_value=0)
        KafkaPublisher(fake, flush_timeout_s=3.5).publish("t", "k", b"x")
        fake.flush.assert_called_once_with(3.5)


# ── KafkaSubscriber ───────────────────────────────────────────────────────────

class TestKafkaSubscriber:

    def test_subscribes_to_the_topics(self):
        fake = FakeConsumer()
        KafkaSubscriber("g", ["a", "b"], consumer=fake)
        assert fake.subscribed == ["a", "b"]

    def test_converts_a_message(self):
        sub = KafkaSubscriber("g", ["t"], consumer=FakeConsumer(
            [FakeKafkaMessage("t", b"SAT-001", b"payload")]))
        assert sub.poll(0) == BusMessage("t", "SAT-001", b"payload")

    def test_a_missing_key_becomes_none(self):
        sub = KafkaSubscriber("g", ["t"], consumer=FakeConsumer([FakeKafkaMessage(key=None)]))
        assert sub.poll(0).key is None

    def test_nothing_to_read_returns_none(self):
        assert KafkaSubscriber("g", ["t"], consumer=FakeConsumer()).poll(0) is None

    def test_a_client_error_is_ignored_not_raised(self):
        sub = KafkaSubscriber("g", ["t"], consumer=FakeConsumer(
            [FakeKafkaMessage(error="UNKNOWN_TOPIC_OR_PART")]))
        assert sub.poll(0) is None

    def test_reading_continues_after_an_error(self):
        sub = KafkaSubscriber("g", ["t"], consumer=FakeConsumer(
            [FakeKafkaMessage(error="boom"), FakeKafkaMessage(value=b"good")]))
        assert sub.poll(0) is None
        assert sub.poll(0).value == b"good"

    def test_close_closes_the_client(self):
        fake = FakeConsumer()
        KafkaSubscriber("g", ["t"], consumer=fake).close()
        assert fake.closed


# ── Configuration ─────────────────────────────────────────────────────────────

class TestConfig:

    def test_command_topic_names(self):
        s = Settings()
        assert s.commands_topic("SAT-001", "uplinked") == "commands.SAT-001.uplinked"
        assert s.commands_topic("SAT-MA1", "acks") == "commands.SAT-MA1.acks"

    def test_commands_are_off_by_default(self):
        assert Settings().commands_enabled is False

    def test_consumer_groups_are_distinct(self):
        s = Settings()
        groups = {s.kafka_consumer_group_satellite_commands, s.kafka_consumer_group_command_acks,
                  s.kafka_consumer_group_sink, s.kafka_consumer_group_anomaly,
                  s.kafka_consumer_group_dashboard}
        assert len(groups) == 5
