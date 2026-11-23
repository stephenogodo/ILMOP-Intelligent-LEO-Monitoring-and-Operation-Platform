"""
services/api/command_runtime.py

Everything the command endpoints need, built once and shared (Sprint 9, patch C).

WHAT IT HOLDS
─────────────
    secure      the SecureCommandService the endpoints call.  It checks every
                token, role and audit entry, so the endpoints stay thin.
    auth        the AuthService behind it (the endpoints call login/logout).
    sender      the function that publishes an approved command to Kafka, or
                None when uplink is not enabled.
    ack_listener / ack thread
                the background reader that turns the satellite's
                acknowledgements into CONFIRMED / FAILED.

WHY A SEPARATE MODULE
─────────────────────
main.py's lifespan only has to say "build it" and "stop it".  Tests build a
CommandRuntime directly on an in-memory bus, with no Kafka.
"""

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from services.command.auth import AuthService, Role
from services.command.kafka_uplink import AckListener, make_bus_sender
from services.command.secure_service import SecureCommandService
from services.command.service import CommandService
from shared.config import Settings
from shared.schemas.command_schema import ActorKind, Command

log = logging.getLogger(__name__)


class AccountConfigError(ValueError):
    """COMMAND_ACCOUNTS_JSON is present but not usable.  Stops start-up."""


def parse_accounts(raw: str) -> List[dict]:
    """
    Turn the COMMAND_ACCOUNTS_JSON text into a list of validated account dicts.

    An empty string means "no accounts" and returns [].  Anything malformed
    raises AccountConfigError, so a typo stops the service at start-up
    instead of silently leaving it with no (or the wrong) accounts.
    """
    if not raw or not raw.strip():
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AccountConfigError(f"COMMAND_ACCOUNTS_JSON is not valid JSON: {exc}") from exc
    if not isinstance(items, list):
        raise AccountConfigError("COMMAND_ACCOUNTS_JSON must be a JSON list")

    accounts = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise AccountConfigError(f"account #{i} must be an object")
        try:
            accounts.append({
                "name":  item["name"],
                "secret": item["secret"],
                "kind":  ActorKind(item["kind"]),
                "roles": [Role(r) for r in item["roles"]],
            })
        except KeyError as exc:
            raise AccountConfigError(f"account #{i} is missing {exc}") from exc
        except ValueError as exc:                 # unknown kind or role
            raise AccountConfigError(f"account #{i}: {exc}") from exc
    return accounts


@dataclass
class CommandRuntime:
    """The shared command machinery behind the REST endpoints."""

    secure: SecureCommandService
    auth:   AuthService
    sender: Optional[Callable[[Command], None]] = None
    ack_listener: Optional[AckListener] = None

    _stop:   threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    def start_ack_thread(self) -> None:
        """Run the acknowledgement listener in the background (if there is one)."""
        if self.ack_listener is None or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self.ack_listener.run, args=(self._stop,),
            name="ack-listener", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Ask the listener to stop and wait briefly for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
            self._thread = None


def build_runtime(
    settings: Settings,
    publisher: Any = None,
    subscriber: Any = None,
    hash_iterations: int = 200_000,
) -> Optional[CommandRuntime]:
    """
    Build the runtime from settings, or return None if no accounts are set.

    publisher / subscriber are injected by tests (an InMemoryBus).  In
    production they are None and, when COMMANDS_ENABLED is true, real Kafka
    ones are created here.
    """
    accounts = parse_accounts(settings.command_accounts_json)
    if not accounts:
        log.info("Command endpoints disabled: COMMAND_ACCOUNTS_JSON is empty")
        return None

    auth = AuthService(hash_iterations=hash_iterations)
    for acc in accounts:
        auth.register(acc["name"], acc["secret"], acc["kind"], acc["roles"])

    service = CommandService()
    secure = SecureCommandService(auth, service)

    sender = None
    ack_listener = None
    if settings.commands_enabled:
        satellites = [s.strip() for s in settings.command_satellites.split(",") if s.strip()]
        if publisher is None:
            from services.messaging.bus import KafkaPublisher
            publisher = KafkaPublisher()
        if subscriber is None:
            from services.messaging.bus import KafkaSubscriber
            subscriber = KafkaSubscriber(
                settings.kafka_consumer_group_command_acks,
                [AckListener.topic_for(s) for s in satellites],
            )
        sender = make_bus_sender(publisher)
        ack_listener = AckListener(service, subscriber)

    log.info("Command endpoints enabled | accounts=%d uplink=%s",
             len(accounts), "on" if sender else "off")
    return CommandRuntime(secure=secure, auth=auth, sender=sender, ack_listener=ack_listener)
