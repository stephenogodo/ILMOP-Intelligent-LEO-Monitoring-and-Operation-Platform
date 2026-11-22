"""
services/messaging/bus.py

A minimal message-bus interface, with an in-memory version for tests and a
thin Kafka version for real use (Sprint 9).

WHY AN INTERFACE AT ALL
───────────────────────
The command loop (ground -> Kafka -> satellite -> Kafka -> ground) is the
most safety-relevant logic in ILMOP.  We want to test every awkward case in
it (duplicates, lost messages, garbage, a broker that is down) quickly and
without Docker.  So the loop code talks to two tiny interfaces:

    Publisher.publish(topic, key, value)
    Subscriber.poll(timeout_s) -> BusMessage | None

and never imports Kafka itself.  Tests plug in InMemoryBus; production
plugs in KafkaPublisher / KafkaSubscriber.

WHAT THE KAFKA VERSION PROMISES
───────────────────────────────
publish() does not return until the broker has acknowledged the message (or
it raises PublishError).  Commands are rare, so waiting for each one is
fine, and the caller learns about a failure at once instead of finding out
later that a command was never sent.

WHAT IT DOES NOT PROMISE
────────────────────────
Kafka delivers at least once, so a subscriber can see a message twice.
Handling that is the job of the code that reads the messages (the satellite
already ignores a command it has run; the ground ignores a repeated
confirmation).  This file does not hide duplicates.
"""

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

from shared.config import settings

log = logging.getLogger(__name__)


class PublishError(Exception):
    """A message could not be delivered to the broker."""


@dataclass(frozen=True)
class BusMessage:
    """One message: where it was published, its key and its bytes."""
    topic: str
    key: Optional[str]
    value: bytes


# ══════════════════════════════════════════════════════════════════════════════
# In-memory implementation (tests, and single-process demos)
# ══════════════════════════════════════════════════════════════════════════════

class InMemoryBus:
    """
    A bus that lives in process memory.  It is both the Publisher and the
    factory for Subscribers.

    It keeps ONE append-only log of every message.  Each subscriber has its
    own position in that log, so two subscribers each see every message (like
    two Kafka consumer groups).  A subscriber only returns messages from the
    topics it asked for.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._log: List[BusMessage] = []
        # Test hook: make the next publish() calls fail, to simulate a broker
        # outage.  A number counts down; True means "fail every time".
        self.fail_publishes: Any = 0

    def publish(self, topic: str, key: Optional[str], value: bytes) -> None:
        if self.fail_publishes:
            if self.fail_publishes is not True:
                self.fail_publishes -= 1
            raise PublishError("simulated broker outage")
        with self._lock:
            self._log.append(BusMessage(topic, key, bytes(value)))

    def subscriber(self, topics: Sequence[str],
                   from_beginning: bool = True) -> "InMemorySubscriber":
        """A new subscriber.  from_beginning=False sees only later messages."""
        with self._lock:
            start = 0 if from_beginning else len(self._log)
        return InMemorySubscriber(self, set(topics), start)

    def messages(self, topic: Optional[str] = None) -> List[BusMessage]:
        """Everything published so far (for assertions in tests)."""
        with self._lock:
            return [m for m in self._log if topic is None or m.topic == topic]


class InMemorySubscriber:
    """Reads the bus's log in order, filtered to the subscribed topics."""

    def __init__(self, bus: InMemoryBus, topics: set, position: int) -> None:
        self._bus = bus
        self._topics = topics
        self._pos = position

    def poll(self, timeout_s: float = 0.0) -> Optional[BusMessage]:
        # timeout_s is accepted for interface compatibility; an in-memory
        # bus never has to wait, so it answers at once.
        with self._bus._lock:
            while self._pos < len(self._bus._log):
                message = self._bus._log[self._pos]
                self._pos += 1
                if message.topic in self._topics:
                    return message
        return None

    def close(self) -> None:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Kafka implementation
# ══════════════════════════════════════════════════════════════════════════════

class KafkaPublisher:
    """
    Publishes through a confluent_kafka Producer and waits for the broker.

    `producer` can be passed in (tests pass a fake).  If it is not, a real
    Producer is created lazily so importing this module never needs Kafka.
    """

    def __init__(self, producer: Any = None, flush_timeout_s: float = 10.0) -> None:
        if producer is None:
            from confluent_kafka import Producer          # imported only when needed
            producer = Producer({
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "acks": "all",                 # wait for the full set of replicas
                "enable.idempotence": True,    # the client will not duplicate on retry
            })
        self._producer = producer
        self._flush_timeout_s = flush_timeout_s

    def publish(self, topic: str, key: Optional[str], value: bytes) -> None:
        errors: List[Any] = []

        def on_delivery(err: Any, _msg: Any) -> None:
            # Called by the client during flush(); it reports failure here,
            # NOT by raising from produce().
            if err is not None:
                errors.append(err)

        try:
            self._producer.produce(
                topic=topic,
                key=key.encode("utf-8") if key is not None else None,
                value=value,
                callback=on_delivery,
            )
        except Exception as exc:                  # e.g. local queue full
            raise PublishError(f"could not queue message for {topic}: {exc}") from exc

        still_queued = self._producer.flush(self._flush_timeout_s)
        if errors:
            raise PublishError(f"broker rejected message for {topic}: {errors[0]}")
        if still_queued:
            raise PublishError(f"timed out after {self._flush_timeout_s}s waiting for "
                               f"the broker (topic {topic})")


class KafkaSubscriber:
    """
    Reads from Kafka through a confluent_kafka Consumer.

    poll() returns None when there is nothing to read AND when the client
    reports a transient problem (logged).  A long-running listener should
    keep running through a broker restart or a topic that is not yet
    created, not crash.
    """

    def __init__(self, group_id: str, topics: Sequence[str],
                 consumer: Any = None) -> None:
        if consumer is None:
            from confluent_kafka import Consumer          # imported only when needed
            consumer = Consumer({
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "group.id": group_id,
                # A new group starts from the oldest message so a command
                # published before the listener started is not missed.
                "auto.offset.reset": "earliest",
                "enable.auto.commit": True,
            })
        self._consumer = consumer
        self._consumer.subscribe(list(topics))

    def poll(self, timeout_s: float = 0.0) -> Optional[BusMessage]:
        msg = self._consumer.poll(timeout_s)
        if msg is None:
            return None
        if msg.error():
            # Partition end-of-file and "topic not created yet" are routine.
            log.warning("Kafka consumer error (ignored): %s", msg.error())
            return None
        key = msg.key()
        return BusMessage(
            topic=msg.topic(),
            key=key.decode("utf-8") if key is not None else None,
            value=msg.value(),
        )

    def close(self) -> None:
        self._consumer.close()
