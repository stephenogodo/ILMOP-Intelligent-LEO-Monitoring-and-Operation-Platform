"""
services/ingest

The ground-side frame ingest service (Sprint 9, patch D).

Raw CCSDS transfer frames come in; decoded telemetry goes out on the
telemetry.{satellite_id} Kafka topics, in exactly the same format as the
simulator's own producer writes.  See frame_ingest.py.
"""
