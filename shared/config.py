"""
shared/config.py — Central configuration for all ILMOP services.

Every service imports its settings from here. No hardcoded strings
(hostnames, ports, topic names, credentials) anywhere else in the codebase.

Configuration values are read from environment variables with sensible
defaults so the project runs locally with no setup and in Docker/Azure
with only environment variable overrides.

Usage
─────
    from shared.config import settings

    producer = Producer({"bootstrap.servers": settings.kafka_bootstrap_servers})
    conn     = await asyncpg.connect(settings.timescaledb_url)
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All ILMOP runtime configuration in one place.
    Override any value by setting the corresponding environment variable
    (case-insensitive). Example:
        KAFKA_BOOTSTRAP_SERVERS=kafka:9092 python services/kafka_producer/producer.py
    """

    model_config = SettingsConfigDict(
        env_file=".env",            # load from .env if present (development)
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",             # silently ignore unknown env vars
    )

    # ── Kafka ─────────────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_telemetry_topic_prefix: str = "telemetry"   # topic = prefix.{satellite_id}
    kafka_alarms_topic_prefix: str = "alarms"
    kafka_commands_topic_prefix: str = "commands"
    kafka_consumer_group_sink: str = "ilmop-sink"
    kafka_consumer_group_anomaly: str = "ilmop-anomaly-detection"
    kafka_consumer_group_dashboard: str = "ilmop-dashboard"
    # Command loop (Sprint 9): the satellite side reads uplinked commands,
    # the ground side reads the satellite's acknowledgements.
    kafka_consumer_group_satellite_commands: str = "ilmop-satellite-commands"
    kafka_consumer_group_command_acks: str = "ilmop-command-acks"

    # Switches the satellite-side command listener on inside the telemetry
    # producer.  Off by default so nothing needs the command topics unless
    # asked for.  Set COMMANDS_ENABLED=true to turn it on.
    commands_enabled: bool = False

    # ── Command REST endpoints (Sprint 9, patch C) ────────────────────────────
    # The accounts allowed to use the command endpoints, as a JSON list:
    #   [{"name": "stephen", "secret": "...", "kind": "OPERATOR",
    #     "roles": ["AUTHOR", "APPROVER"]},
    #    {"name": "uplink",  "secret": "...", "kind": "SYSTEM",
    #     "roles": ["UPLINKER"]}]
    # Empty (the default) means NO accounts: every command endpoint then
    # answers 503.  There are deliberately no default credentials.
    command_accounts_json: str = ""

    # Satellites whose acknowledgement topics the API listens to, comma
    # separated.  Only used when COMMANDS_ENABLED is true.
    command_satellites: str = "SAT-001"

    # Format the PRODUCERS write.  Consumers always read both, so upgrade
    # every consumer first and only then set this to "avro" (ADR-022).
    # An invalid value stops the service at start-up.
    kafka_serialization: Literal["json", "avro"] = "json"

    # ── TimescaleDB ───────────────────────────────────────────────────────────
    timescaledb_host: str = "localhost"
    timescaledb_port: int = 5432
    timescaledb_name: str = "ilmop"
    timescaledb_user: str = "ilmop"
    timescaledb_password: str = "ilmop"

    @property
    def timescaledb_url(self) -> str:
        """asyncpg-compatible connection URL."""
        return (
            f"postgresql://{self.timescaledb_user}:{self.timescaledb_password}"
            f"@{self.timescaledb_host}:{self.timescaledb_port}/{self.timescaledb_name}"
        )

    @property
    def timescaledb_dsn(self) -> str:
        """psycopg2-compatible DSN string."""
        return (
            f"host={self.timescaledb_host} "
            f"port={self.timescaledb_port} "
            f"dbname={self.timescaledb_name} "
            f"user={self.timescaledb_user} "
            f"password={self.timescaledb_password}"
        )

    # ── Simulator ─────────────────────────────────────────────────────────────
    simulator_satellite_id: str = "SAT-001"
    simulator_tick_interval_s: float = 1.0   # seconds between telemetry ticks

    # ── MLflow (Sprint 5) ────────────────────────────────────────────────────────
    mlflow_tracking_uri: str = "mlruns"   # local file store; override for Azure Blob

    # ── Redis cache (Sprint 4) ────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"
    cache_ttl_s: int = 5

    # ── API (Sprint 4) ────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_base_url: str = "http://localhost:8000"

    # ── Dashboard (Sprint 4) ──────────────────────────────────────────────────
    dashboard_port: int = 8501

    # ── Helper methods ────────────────────────────────────────────────────────
    def telemetry_topic(self, satellite_id: str) -> str:
        """Return the Kafka topic name for a given satellite's telemetry."""
        return f"{self.kafka_telemetry_topic_prefix}.{satellite_id}"

    def commands_topic(self, satellite_id: str, stage: str) -> str:
        """
        Kafka topic for one stage of the command path, e.g.
        commands.SAT-001.uplinked (ground to satellite) or
        commands.SAT-001.acks (satellite to ground).
        """
        return f"{self.kafka_commands_topic_prefix}.{satellite_id}.{stage}"

    def alarms_topic(self, satellite_id: str) -> str:
        """Return the Kafka topic name for a given satellite's alarms."""
        return f"{self.kafka_alarms_topic_prefix}.{satellite_id}"


# Module-level singleton — import this in every service
settings = Settings()
