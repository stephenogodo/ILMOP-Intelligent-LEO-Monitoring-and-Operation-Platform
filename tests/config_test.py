"""
Tests for shared/config.py — settings loading and helper methods.
"""
import os
import pytest
from shared.config import Settings


def test_default_kafka_bootstrap_servers():
    s = Settings()
    assert s.kafka_bootstrap_servers == "localhost:9092"


def test_telemetry_topic_format():
    s = Settings()
    assert s.telemetry_topic("SAT-001") == "telemetry.SAT-001"
    assert s.telemetry_topic("SAT-042") == "telemetry.SAT-042"


def test_alarms_topic_format():
    s = Settings()
    assert s.alarms_topic("SAT-001") == "alarms.SAT-001"


def test_timescaledb_url_format():
    s = Settings()
    url = s.timescaledb_url
    assert url.startswith("postgresql://")
    assert "ilmop" in url
    assert "5432" in url


def test_timescaledb_dsn_format():
    s = Settings()
    dsn = s.timescaledb_dsn
    assert "host=" in dsn
    assert "dbname=ilmop" in dsn
    assert "user=ilmop" in dsn


def test_env_var_override(monkeypatch):
    """Environment variables must override defaults."""
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    s = Settings()
    assert s.kafka_bootstrap_servers == "kafka:9092"


def test_satellite_id_default():
    s = Settings()
    assert s.simulator_satellite_id == "SAT-001"


def test_topic_prefix_customisable(monkeypatch):
    monkeypatch.setenv("KAFKA_TELEMETRY_TOPIC_PREFIX", "raw")
    s = Settings()
    assert s.telemetry_topic("SAT-001") == "raw.SAT-001"
