"""
scripts/command_loop_kafka_check.py

A manual end-to-end check of the command loop over a REAL Kafka broker
(Sprint 9).  The automated tests use an in-memory bus and fakes of the Kafka
client; this script is the one thing that exercises the real client.

HOW TO RUN IT
─────────────
1. Start the broker:        docker compose -f infrastructure/docker-compose.yml up -d kafka
2. Run the check:           python -m scripts.command_loop_kafka_check

It prints PASS or FAIL for each of two cases and exits 0 only if both pass.
To see the script's own logic without a broker, add --in-memory.

WHAT IT DOES
────────────
Both ends of the loop run inside this one process, each on its own real
Kafka connection, using a throw-away satellite called SAT-CHECK:

    ground:    submit -> approve -> uplink (publishes to Kafka)
    satellite: reads the command from Kafka, runs it, publishes the answer
    ground:    reads the answer and confirms or fails the command

    Case 1  satellite IN contact      expect  CONFIRMED
    Case 2  satellite OUT of contact  expect  FAILED  (reason "no contact")

WHY THE SATELLITE'S CONTACT IS FORCED
─────────────────────────────────────
The simulator's real contact windows come only every 50 to 100 simulated
minutes, far too slow to wait for.  Here the satellite is simply told whether
it is in contact; nothing else about the command path is faked.

NOTES
─────
• The four topics are created first.  A consumer that subscribes to a topic
  that does not exist yet can take minutes to notice it when it appears.
• Consumer groups get a unique suffix per run, so an earlier run's saved
  position cannot hide this run's messages.
• The first read after starting can take several seconds while the consumer
  joins its group, so the default wait is generous (--timeout 60).
"""

import argparse
import sys
import threading
import time
from typing import Callable, Tuple

from services.command.kafka_uplink import AckListener, make_bus_sender
from services.command.service import CommandService
from services.messaging.bus import InMemoryBus, KafkaPublisher, KafkaSubscriber
from services.satellite_simulator.command_listener import CommandListener
from services.satellite_simulator.telemetry import TelemetryGenerator
from shared.config import settings
from shared.schemas.command_schema import Actor, ActorKind, CommandStatus

SATELLITE_ID = "SAT-CHECK"
OPERATOR = Actor(name="check-operator", kind=ActorKind.OPERATOR)
UPLINKER = Actor(name="check-uplinker", kind=ActorKind.SYSTEM)


def _topics() -> list:
    return [settings.commands_topic(SATELLITE_ID, "uplinked"),
            settings.commands_topic(SATELLITE_ID, "acks")]


def _create_topics() -> None:
    """Create the command topics on the real broker (ignore 'already exists')."""
    from confluent_kafka.admin import AdminClient, NewTopic
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
    futures = admin.create_topics([NewTopic(t, num_partitions=1, replication_factor=1)
                                   for t in _topics()])
    for topic, future in futures.items():
        try:
            future.result(timeout=15)
            print(f"  created topic {topic}")
        except Exception as exc:                       # usually: already exists
            print(f"  topic {topic}: {exc}")


def _transport(in_memory: bool) -> Tuple[object, Callable]:
    """Return (publisher, make_subscriber(topics, group))."""
    if in_memory:
        bus = InMemoryBus()
        return bus, lambda topics, group: bus.subscriber(topics)

    _create_topics()
    run_id = str(int(time.time()))
    return KafkaPublisher(), lambda topics, group: KafkaSubscriber(f"{group}-{run_id}", topics)


def _wait_for(service: CommandService, command_id: str, timeout_s: float) -> CommandStatus:
    """Wait until the command leaves UPLINKED, or the time runs out."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = service.get(command_id).status
        if status in (CommandStatus.CONFIRMED, CommandStatus.FAILED):
            return status
        time.sleep(0.2)
    return service.get(command_id).status


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--in-memory", action="store_true",
                        help="use the in-memory bus instead of a real Kafka broker")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="seconds to wait for each command (default 60)")
    args = parser.parse_args(argv)

    print("Command loop check (%s)" % ("in-memory bus" if args.in_memory else
                                        f"Kafka at {settings.kafka_bootstrap_servers}"))
    publisher, make_subscriber = _transport(args.in_memory)

    # ── The satellite end ────────────────────────────────────────────────────
    generator = TelemetryGenerator(satellite_id=SATELLITE_ID)
    satellite_listener = CommandListener(
        generator.command_receiver,
        make_subscriber([CommandListener.uplinked_topic(SATELLITE_ID)], "check-satellite"),
        publisher, SATELLITE_ID)

    # ── The ground end ───────────────────────────────────────────────────────
    service = CommandService()
    ack_listener = AckListener(
        service, make_subscriber([AckListener.topic_for(SATELLITE_ID)], "check-ground"))
    sender = make_bus_sender(publisher)

    stop = threading.Event()

    def satellite_loop() -> None:
        while not stop.is_set():
            try:
                satellite_listener.poll_once(0.5)
            except Exception as exc:                   # keep running, show the problem
                print("  satellite loop error:", exc)

    threads = [threading.Thread(target=satellite_loop, daemon=True),
               threading.Thread(target=ack_listener.run, args=(stop, 0.5), daemon=True)]
    for t in threads:
        t.start()

    results = []
    try:
        for label, in_contact, expected in (
                ("Case 1: in contact", True, CommandStatus.CONFIRMED),
                ("Case 2: out of contact", False, CommandStatus.FAILED)):
            generator.satellite.in_contact = in_contact
            command_id = service.submit(SATELLITE_ID, "SET_SAFE_MODE", {"enabled": True},
                                        OPERATOR).command.command_id
            service.approve(command_id, OPERATOR)
            service.dispatch(command_id, UPLINKER, sender)

            status = _wait_for(service, command_id, args.timeout)
            reason = service.get(command_id).history[-1].reason
            ok = status is expected
            results.append(ok)
            print(f"  {label}: {status.value} (expected {expected.value}) "
                  f"-> {'PASS' if ok else 'FAIL'}   [{reason}]")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=3)

    print("RESULT:", "PASS" if all(results) else "FAIL")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
