"""
services/ingest/__main__.py

Command-line entry point:

    python -m services.ingest                    run the simulated downlink forever
    python -m services.ingest --once             one tick, then print the counters
    python -m services.ingest --drop-every-n 5   lose every 5th frame on purpose

Only a SIMULATED downlink is wired in: no real antenna interface exists yet
(see ADR-023).  The service publishes to the real Kafka broker.
"""

import argparse
import logging
import time

from services.ingest.frame_ingest import SimulatedDownlink, build_service, parse_scids
from shared.config import settings

log = logging.getLogger("ingest")


def main(argv=None, publisher=None, sleep=time.sleep) -> int:
    """Run the ingest loop.  publisher and sleep are injectable for tests."""
    parser = argparse.ArgumentParser(prog="python -m services.ingest")
    parser.add_argument("--interval", type=float, default=settings.simulator_tick_interval_s,
                        help="seconds between ticks")
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    parser.add_argument("--drop-every-n", type=int, default=0,
                        help="delete every Nth frame, to exercise loss handling")
    args = parser.parse_args(argv)

    if publisher is None:
        from services.messaging.bus import KafkaPublisher
        publisher = KafkaPublisher()

    scids = parse_scids(settings.ingest_satellite_scids)
    service, config, registry = build_service(publisher, scids=scids)
    satellites = sorted(scids)
    source = SimulatedDownlink(config, registry, satellites,
                               drop_every_n=args.drop_every_n)
    log.info("Ingest started | satellites=%s frame_length=%d interval=%.1fs",
             satellites, config.frame_length, args.interval)

    try:
        while True:
            service.ingest(source.next_frames())
            if args.once:
                break
            sleep(args.interval)
    except KeyboardInterrupt:
        log.info("Interrupted")

    # A summary on the way out, so a --once run shows what happened.
    log.info("Ingest stats | %s | processor: %s", service.stats, service.processor_stats)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
    raise SystemExit(main())
