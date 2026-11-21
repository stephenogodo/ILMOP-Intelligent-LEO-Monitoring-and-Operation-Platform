"""
services/command/auth.py

Authentication for the command path (Sprint 8, S8-08).

AUTHENTICATION vs AUTHORISATION
───────────────────────────────
    Authentication  = proving WHO you are        (this file)
    Authorisation   = what you MAY do once known (roles, checked here and
                                                  applied in secure_service.py)

HOW IT WORKS
────────────
    1. An account has a name, a kind (human OPERATOR or automated SYSTEM),
       a set of roles and a secret.  The secret is NEVER stored; only a
       salted PBKDF2-HMAC-SHA256 hash of it is.
    2. login(name, secret) returns an opaque random session token.  Only a
       hash of the token is stored, so a leaked store does not give anyone
       a working token.
    3. Every later action presents the token.  resolve(token) turns it into
       a VERIFIED Actor.  Callers never build an Actor themselves, so an
       identity cannot be forged.

SAFETY DETAILS
──────────────
    * Secrets and tokens are compared in constant time (hmac.compare_digest
      for secrets; tokens are looked up by their hash).
    * An unknown name and a wrong secret give the SAME error, so an attacker
      cannot discover which names exist.
    * After MAX_FAILED_LOGINS consecutive failures an account is locked for
      LOCKOUT_SECONDS, which slows down password guessing.
    * Tokens expire, and can be revoked at logout.

LIMITS (stated plainly)
───────────────────────
Accounts are created in code and sessions live in memory (they vanish on
restart).  There is no TLS, multi-factor authentication or external identity
provider.  This is the core of the check, not a complete security system.
"""

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, FrozenSet, Optional

from shared.schemas.command_schema import Actor, ActorKind


class Role(str, Enum):
    """What an account is allowed to do (authorisation)."""

    AUTHOR   = "AUTHOR"      # may submit commands
    APPROVER = "APPROVER"    # may approve or reject drafts (humans only)
    UPLINKER = "UPLINKER"    # may uplink approved commands and confirm them


class AuthenticationError(Exception):
    """Wrong credentials, expired or unknown token, or a locked account."""


class AuthorizationError(Exception):
    """The caller is known but does not have the role the action needs."""


@dataclass(frozen=True)
class Account:
    """One registered identity.  Holds the hash of the secret, never the secret."""

    name:        str
    kind:        ActorKind
    roles:       FrozenSet[Role]
    secret_hash: bytes
    salt:        bytes


@dataclass(frozen=True)
class Session:
    """One live login.  The token itself is not stored, only its hash."""

    account_name: str
    expires_at:   datetime


def _hash_secret(secret: str, salt: bytes, iterations: int) -> bytes:
    """Salted PBKDF2-HMAC-SHA256 of a secret (slow on purpose, to resist guessing)."""
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)


def _hash_token(token: str) -> str:
    """Plain SHA-256 of a token.  Tokens are long and random, so no salt is needed."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    """Registers accounts, logs people in, and resolves tokens to verified Actors."""

    def __init__(
        self,
        token_ttl_seconds: int = 1800,        # sessions last 30 minutes
        max_failed_logins: int = 5,
        lockout_seconds:   int = 300,         # locked for 5 minutes
        hash_iterations:   int = 200_000,     # tests pass a small value for speed
    ) -> None:
        self._ttl         = timedelta(seconds=token_ttl_seconds)
        self._max_fails   = max_failed_logins
        self._lockout     = timedelta(seconds=lockout_seconds)
        self._iterations  = hash_iterations

        self._accounts: Dict[str, Account] = {}
        self._sessions: Dict[str, Session] = {}      # token hash -> Session
        self._failures: Dict[str, int] = {}          # name -> consecutive failures
        self._locked_until: Dict[str, datetime] = {}

        # One lock for all of the above; every public method takes it.
        self._lock = threading.Lock()

        # A throwaway hash used to spend the same time on unknown names as
        # on real ones, so response time does not reveal which names exist.
        self._dummy_salt = secrets.token_bytes(16)
        self._dummy_hash = _hash_secret("dummy", self._dummy_salt, self._iterations)

    # ── Accounts ──────────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        secret: str,
        kind: ActorKind,
        roles,
    ) -> None:
        """Create an account.  Raises ValueError if the name is taken or the secret is short."""
        if not name:
            raise ValueError("account name is required")
        if len(secret) < 8:
            raise ValueError("secret must be at least 8 characters")

        salt = secrets.token_bytes(16)
        account = Account(
            name=name,
            kind=kind,
            roles=frozenset(roles),
            secret_hash=_hash_secret(secret, salt, self._iterations),
            salt=salt,
        )
        with self._lock:
            if name in self._accounts:
                raise ValueError(f"account '{name}' already exists")
            self._accounts[name] = account

    # ── Login and logout ──────────────────────────────────────────────────────

    def login(self, name: str, secret: str, now: Optional[datetime] = None) -> str:
        """
        Check credentials and return a new session token.

        Raises AuthenticationError with the SAME message for an unknown name
        and a wrong secret.  A locked account is refused even with the
        right secret until the lock expires.
        """
        now = now or datetime.now(timezone.utc)

        with self._lock:
            # Locked accounts are refused before any secret check.
            locked_until = self._locked_until.get(name)
            if locked_until is not None:
                if now < locked_until:
                    raise AuthenticationError("account is temporarily locked")
                # Lock has expired: clear it and start counting afresh.
                del self._locked_until[name]
                self._failures[name] = 0

            account = self._accounts.get(name)
            if account is None:
                # Spend the same effort as a real check (see __init__).
                hmac.compare_digest(
                    _hash_secret(secret, self._dummy_salt, self._iterations),
                    self._dummy_hash,
                )
                ok = False
            else:
                candidate = _hash_secret(secret, account.salt, self._iterations)
                ok = hmac.compare_digest(candidate, account.secret_hash)

            if not ok:
                self._record_failure(name, now)
                raise AuthenticationError("invalid name or secret")

            # Success: forget earlier failures and open a session.
            self._failures[name] = 0
            token = secrets.token_urlsafe(32)
            self._sessions[_hash_token(token)] = Session(
                account_name=name, expires_at=now + self._ttl
            )
            return token

    def logout(self, token: str) -> None:
        """End a session.  Unknown tokens are ignored."""
        with self._lock:
            self._sessions.pop(_hash_token(token), None)

    def _record_failure(self, name: str, now: datetime) -> None:
        """Count a failed login and lock the account if the limit is reached.  Lock must be held."""
        count = self._failures.get(name, 0) + 1
        self._failures[name] = count
        if count >= self._max_fails:
            self._locked_until[name] = now + self._lockout

    # ── Resolving a token ─────────────────────────────────────────────────────

    def resolve(self, token: str, now: Optional[datetime] = None) -> "VerifiedIdentity":
        """
        Turn a token into a verified identity (the Actor plus its roles).

        Raises AuthenticationError if the token is unknown, revoked or expired.
        """
        now = now or datetime.now(timezone.utc)
        key = _hash_token(token or "")

        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                raise AuthenticationError("invalid or expired token")
            if now >= session.expires_at:
                del self._sessions[key]          # tidy up the dead session
                raise AuthenticationError("invalid or expired token")

            account = self._accounts[session.account_name]

        return VerifiedIdentity(
            actor=Actor(name=account.name, kind=account.kind),
            roles=account.roles,
        )


@dataclass(frozen=True)
class VerifiedIdentity:
    """
    Who the caller proved to be, and what they may do.

    Only AuthService.resolve() creates these, which is what makes the
    contained Actor trustworthy.
    """

    actor: Actor
    roles: FrozenSet[Role]

    def require(self, role: Role) -> None:
        """Raise AuthorizationError unless the identity holds `role`."""
        if role not in self.roles:
            raise AuthorizationError(f"'{self.actor.name}' lacks the {role.value} role")
