"""
services/ingest/frame_ingest.py

Connects the CCSDS receive chain to Kafka (Sprint 9, patch D).

THE PICTURE
───────────
    frame source ──► FrameProcessor ──► Telemetry ──► encode ──► telemetry.{sat}
    (raw bytes)      (CRC, packets,     (objects)     (JSON or     (Kafka topic)
                      reassembly)                      Avro)

Nothing downstream can tell this path from the simulator's own producer:
same topics, same keys, same codec (so KAFKA_SERIALIZATION applies).

THREE PARTS
───────────
    FrameIngestService   the core: frames in, published telemetry out
    SimulatedDownlink    a frame SOURCE that fabricates a downlink from the
                         telemetry simulator, so the chain runs with no
                         ground station
    frames_from_bytes    a helper that cuts a recorded byte stream into frames

A "source" is simply anything that yields raw frame bytes.  A real antenna
interface (UDP, serial, file tail) would be one more source; the service
itself would not change.

NOISE IS NORMAL, PUBLISH FAILURES ARE SURVIVED
──────────────────────────────────────────────
Bad frames never raise (the FrameProcessor counts them).  A failed publish is
counted and logged, and ingest carries on: a record lost to a broker outage
cannot be fetched again from a radio link, but stopping would lose every
record that follows as well.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional

from services.frame_processor.framer import FrameBuilder, make_builders
from services.frame_processor.processor import FrameProcessor
from services.frame_processor.telemetry_codec import ScidRegistry
from services.frame_processor.tm_frame import FrameConfig
from services.messaging.bus import PublishError
from services.messaging.project_codecs import encode_telemetry
from services.satellite_simulator.telemetry import TelemetryGenerator
from shared.config import settings

log = logging.getLogger(__name__)


# ── Settings helpers ─────────────────────────────────────────────────────────

def parse_scids(text: str) -> Dict[str, int]:
    """
    Parse "SAT-001=1,SAT-002=2" into {"SAT-001": 1, "SAT-002": 2}.

    Raises ValueError on a malformed entry so a typo stops the service at
    start-up.  (Range and duplicate checks are done by ScidRegistry.)
    """
    mapping: Dict[str, int] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, number = part.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"bad INGEST_SATELLITE_SCIDS entry: '{part}' (want NAME=NUMBER)")
        try:
            mapping[name.strip()] = int(number)
        except ValueError:
            raise ValueError(f"bad spacecraft ID in '{part}': not a whole number") from None
    if not mapping:
        raise ValueError("INGEST_SATELLITE_SCIDS lists no satellites")
    return mapping


# ── The core service ─────────────────────────────────────────────────────────

@dataclass
class IngestStats:
    """
    The service's own counters.  Frame-level counters (CRC errors, lost
    frames, resyncs ...) live on FrameProcessor.stats.
    """
    frames_in: int = 0
    telemetry_published: int = 0
    publish_errors: int = 0


class FrameIngestService:
    """
    Feeds raw frames to a FrameProcessor and publishes what comes out.

        service = FrameIngestService(processor, publisher)
        service.ingest(frames)          # publisher: anything with publish(topic, key, bytes)
    """

    def __init__(self, processor: FrameProcessor, publisher) -> None:
        self._processor = processor
        self._publisher = publisher
        self.stats = IngestStats()

    @property
    def processor_stats(self):
        """The receive chain's counters (CRC errors, frames lost, and so on)."""
        return self._processor.stats

    def ingest(self, frames: Iterable[bytes]) -> int:
        """
        Process frames in order.  Returns how many telemetry records were
        published by this call.  Never raises on bad frames or broker errors.
        """
        published = 0
        for raw in frames:
            self.stats.frames_in += 1
            # feed() returns every record completed by this frame: none while
            # a packet is still being assembled, or several if a frame ends
            # one packet and holds whole ones after it.
            for telemetry in self._processor.feed(raw):
                if self._publish(telemetry):
                    published += 1
        return published

    def _publish(self, telemetry) -> bool:
        """Encode and publish one record.  True on success."""
        try:
            self._publisher.publish(
                settings.telemetry_topic(telemetry.satellite_id),
                telemetry.satellite_id,                 # key: one satellite stays in order
                encode_telemetry(telemetry),            # JSON or Avro per KAFKA_SERIALIZATION
            )
        except PublishError as exc:
            self.stats.publish_errors += 1
            log.error("ingest: could not publish %s record: %s",
                      telemetry.satellite_id, exc)
            return False
        self.stats.telemetry_published += 1
        return True


# ── Source 1: a simulated downlink ───────────────────────────────────────────

class SimulatedDownlink:
    """
    Fabricates what a ground station would receive, from the simulator.

    Each call to next_frames() takes one telemetry record per satellite
    from its TelemetryGenerator and encodes it into transfer frames with a
    per-satellite FrameBuilder (which keeps proper frame counters).

    drop_every_n: if > 0, every Nth frame is deleted from the output, as a
                  radio link might lose it.  The receiver should detect the
                  gap (frames_lost) and carry on.  With the default 1115-byte
                  frame each record fits one frame, so a drop costs exactly
                  one record.
    """

    def __init__(self, config: FrameConfig, registry: ScidRegistry,
                 satellite_ids: Iterable[str], drop_every_n: int = 0) -> None:
        self._satellites = list(satellite_ids)
        self._generators = {sat: TelemetryGenerator(satellite_id=sat)
                            for sat in self._satellites}
        self._builders: Dict[str, FrameBuilder] = make_builders(
            config, registry, self._satellites)
        self._drop_every_n = drop_every_n
        self._frames_made = 0          # counts every frame built, kept or dropped

    def next_frames(self) -> List[bytes]:
        """One tick of the downlink: the frames carrying one record per satellite."""
        out: List[bytes] = []
        for sat in self._satellites:
            record = self._generators[sat].generate()
            for frame in self._builders[sat].frames_for_telemetry([record]):
                self._frames_made += 1
                if self._drop_every_n and self._frames_made % self._drop_every_n == 0:
                    continue                      # lost on the link
                out.append(frame)
        return out

    def stream(self, ticks: int) -> Iterator[bytes]:
        """Yield the frames of `ticks` consecutive ticks."""
        for _ in range(ticks):
            yield from self.next_frames()


# ── Source 2: a recorded byte stream ─────────────────────────────────────────

def frames_from_bytes(data: bytes, frame_length: int) -> List[bytes]:
    """
    Cut a recorded stream (for example a file) into frames of a fixed length.

    Raises ValueError if the length is not a whole number of frames: that
    means the recording and the configured frame length disagree, and
    guessing would feed the processor misaligned garbage.
    """
    if frame_length <= 0:
        raise ValueError("frame_length must be positive")
    if len(data) % frame_length != 0:
        raise ValueError(
            f"{len(data)} bytes is not a whole number of {frame_length}-byte frames")
    return [data[i:i + frame_length] for i in range(0, len(data), frame_length)]


# ── Assembly from settings ───────────────────────────────────────────────────

def build_service(publisher, config: Optional[FrameConfig] = None,
                  scids: Optional[Dict[str, int]] = None):
    """
    Build (service, config, registry) from settings, or from the arguments
    given (tests pass their own).
    """
    config = config or FrameConfig(frame_length=settings.ingest_frame_length)
    registry = ScidRegistry(scids or parse_scids(settings.ingest_satellite_scids))
    processor = FrameProcessor(config, registry)
    return FrameIngestService(processor, publisher), config, registry
