"""
run_demo.py — ILMOP Constellation Demo Runner

Single entry point for all four constellation scenarios.

Usage:
    python run_demo.py --scenario 1              # 1 satellite, real time
    python run_demo.py --scenario 2              # 6 satellites, real time
    python run_demo.py --scenario 3 --speed 60  # 24 satellites, 60x speed
    python run_demo.py --scenario 4 --speed 10  # 6 Molniya satellites

    # With fault injection (for anomaly detection training data):
    python run_demo.py --scenario 1 --fault battery_degradation
    python run_demo.py --scenario 1 --fault thermal_runaway
    python run_demo.py --scenario 1 --fault safe_mode_trigger
"""

import argparse
import json
import logging
import time

from confluent_kafka import Producer

from services.satellite_simulator.constellation import (
    ConstellationConfig,
    ConstellationManager,
)
from services.satellite_simulator.telemetry import FaultType
from shared.config import settings

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

SCENARIO_FILES = {
    1: "config/constellations/scenario_1_single.yaml",
    2: "config/constellations/scenario_2_single_orbit.yaml",
    3: "config/constellations/scenario_3_multi_orbit.yaml",
    4: "config/constellations/scenario_4_molniya_polar.yaml",
}

FAULT_TYPES = {
    "none":                FaultType.NONE,
    "battery_degradation": FaultType.BATTERY_DEGRADATION,
    "thermal_runaway":     FaultType.THERMAL_RUNAWAY,
    "safe_mode_trigger":   FaultType.SAFE_MODE_TRIGGER,
}


def parse_args():
    p = argparse.ArgumentParser(description="ILMOP Constellation Demo Runner")
    p.add_argument(
        "--scenario", type=int, choices=[1, 2, 3, 4], default=1,
        help="Constellation scenario (1=single, 2=6-sat, 3=24-sat, 4=Molniya)",
    )
    p.add_argument(
        "--speed", type=float, default=1.0,
        help="Time multiplier — e.g. 60 generates 60 seconds of simulated "
             "telemetry per wall-clock second (default: 1.0 = real time)",
    )
    p.add_argument(
        "--fault", choices=list(FAULT_TYPES.keys()), default="none",
        help="Fault injection mode for anomaly detection training data",
    )
    return p.parse_args()


def delivery_callback(err, msg):
    if err:
        log.error("Delivery failed | %s", err)


def main():
    args = parse_args()

    config  = ConstellationConfig.from_yaml(SCENARIO_FILES[args.scenario])
    fault   = FAULT_TYPES[args.fault]
    manager = ConstellationManager(
        config          = config,
        time_multiplier = args.speed,
        fault_type      = fault,
    )

    producer = Producer({
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "acks":              "all",
    })

    tick_s = settings.simulator_tick_interval_s / args.speed

    print(f"\n{'=' * 65}")
    print(f"  ILMOP — {config.name}")
    print(f"  Scenario {config.scenario} | {manager.satellite_count} satellites "
          f"| orbit={config.orbit_type}")
    print(f"  Speed: {args.speed}×  | Fault: {args.fault}")
    print(f"  Kafka: {settings.kafka_bootstrap_servers}")
    print(f"{'=' * 65}\n")

    if args.fault != "none":
        log.warning(
            "FAULT INJECTION ACTIVE: %s — telemetry marked fault_injected=True",
            args.fault,
        )

    published = 0
    try:
        while True:
            snapshots = manager.generate_all()

            for sat_id, telemetry in snapshots.items():
                topic   = settings.telemetry_topic(sat_id)
                payload = json.dumps(
                    telemetry.model_dump(mode="json")
                ).encode("utf-8")
                producer.produce(
                    topic    = topic,
                    key      = sat_id.encode("utf-8"),
                    value    = payload,
                    callback = delivery_callback,
                )

            producer.poll(0)
            published += manager.satellite_count

            if published % (manager.satellite_count * 10) == 0:
                first = next(iter(snapshots.values()))
                # Build a dynamic status string showing the most relevant
                # telemetry parameter for the active fault type
                if fault == FaultType.THERMAL_RUNAWAY:
                    status = f"temp={first.temperature_c:.1f}°C"
                elif fault == FaultType.SAFE_MODE_TRIGGER:
                    status = f"safe_mode={first.safe_mode}"
                else:
                    status = f"batt={first.battery_pct:.1f}%"
                log.info(
                    "Published %d records | sat=%s eclipse=%s %s fault=%s",
                    published,
                    first.satellite_id,
                    first.in_eclipse,
                    status,
                    first.fault_injected,
                )

            time.sleep(tick_s)

    except KeyboardInterrupt:
        log.info("Shutting down …")
    finally:
        producer.flush(timeout=5)
        log.info("Producer flushed. Total published: %d", published)


if __name__ == "__main__":
    main()
