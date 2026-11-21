"""
tests/test_command_auth.py

Tests for authentication: accounts, login, tokens, lockout (S8-08).
A small hash_iterations value keeps the tests fast; the real default is
much higher.
"""

from datetime import datetime, timedelta, timezone

import pytest

from shared.schemas.command_schema import ActorKind
from services.command.auth import (
    AuthenticationError,
    AuthorizationError,
    AuthService,
    Role,
)

T0 = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)


def _auth(**kwargs):
    """An AuthService with fast hashing and one registered operator."""
    auth = AuthService(hash_iterations=1000, **kwargs)
    auth.register("stephen", "correct-horse", ActorKind.OPERATOR, [Role.AUTHOR, Role.APPROVER])
    return auth


# ── Registration ──────────────────────────────────────────────────────────────

class TestRegister:

    def test_duplicate_name_is_refused(self):
        auth = _auth()
        with pytest.raises(ValueError):
            auth.register("stephen", "another-secret", ActorKind.OPERATOR, [])

    def test_short_secret_is_refused(self):
        with pytest.raises(ValueError):
            AuthService(hash_iterations=1000).register("a", "short", ActorKind.OPERATOR, [])

    def test_secret_is_not_stored_in_plain_text(self):
        auth = _auth()
        account = auth._accounts["stephen"]
        assert b"correct-horse" not in account.secret_hash
        assert len(account.salt) == 16

    def test_same_secret_gives_different_hashes_for_different_accounts(self):
        auth = _auth()
        auth.register("amara", "correct-horse", ActorKind.OPERATOR, [])
        assert auth._accounts["stephen"].secret_hash != auth._accounts["amara"].secret_hash


# ── Login and tokens ──────────────────────────────────────────────────────────

class TestLogin:

    def test_correct_credentials_return_a_token(self):
        assert len(_auth().login("stephen", "correct-horse", now=T0)) >= 32

    def test_token_resolves_to_the_verified_identity(self):
        auth = _auth()
        identity = auth.resolve(auth.login("stephen", "correct-horse", now=T0), now=T0)
        assert identity.actor.name == "stephen"
        assert identity.actor.kind is ActorKind.OPERATOR
        assert identity.roles == {Role.AUTHOR, Role.APPROVER}

    def test_wrong_secret_is_refused(self):
        with pytest.raises(AuthenticationError):
            _auth().login("stephen", "wrong-secret", now=T0)

    def test_unknown_name_gives_the_same_error_as_a_wrong_secret(self):
        auth = _auth()
        with pytest.raises(AuthenticationError) as wrong:
            auth.login("stephen", "wrong-secret", now=T0)
        with pytest.raises(AuthenticationError) as unknown:
            auth.login("nobody", "wrong-secret", now=T0)
        assert str(wrong.value) == str(unknown.value)

    def test_each_login_gets_a_different_token(self):
        auth = _auth()
        assert auth.login("stephen", "correct-horse") != auth.login("stephen", "correct-horse")

    def test_raw_token_is_not_stored(self):
        auth = _auth()
        token = auth.login("stephen", "correct-horse", now=T0)
        assert token not in auth._sessions          # only a hash is a key


class TestTokenLifetime:

    def test_token_works_before_expiry(self):
        auth = _auth(token_ttl_seconds=60)
        token = auth.login("stephen", "correct-horse", now=T0)
        auth.resolve(token, now=T0 + timedelta(seconds=59))

    def test_token_is_refused_after_expiry(self):
        auth = _auth(token_ttl_seconds=60)
        token = auth.login("stephen", "correct-horse", now=T0)
        with pytest.raises(AuthenticationError):
            auth.resolve(token, now=T0 + timedelta(seconds=60))

    def test_logout_revokes_the_token(self):
        auth = _auth()
        token = auth.login("stephen", "correct-horse", now=T0)
        auth.logout(token)
        with pytest.raises(AuthenticationError):
            auth.resolve(token, now=T0)

    def test_made_up_token_is_refused(self):
        with pytest.raises(AuthenticationError):
            _auth().resolve("not-a-real-token", now=T0)

    def test_empty_token_is_refused(self):
        with pytest.raises(AuthenticationError):
            _auth().resolve("", now=T0)


# ── Lockout ───────────────────────────────────────────────────────────────────

class TestLockout:

    def _fail(self, auth, times, now=T0):
        for _ in range(times):
            with pytest.raises(AuthenticationError):
                auth.login("stephen", "wrong-secret", now=now)

    def test_account_locks_after_too_many_failures(self):
        auth = _auth(max_failed_logins=3)
        self._fail(auth, 3)
        # Even the CORRECT secret is refused while locked.
        with pytest.raises(AuthenticationError, match="locked"):
            auth.login("stephen", "correct-horse", now=T0)

    def test_lock_expires(self):
        auth = _auth(max_failed_logins=3, lockout_seconds=300)
        self._fail(auth, 3)
        later = T0 + timedelta(seconds=301)
        assert auth.login("stephen", "correct-horse", now=later)

    def test_success_resets_the_failure_count(self):
        auth = _auth(max_failed_logins=3)
        self._fail(auth, 2)
        auth.login("stephen", "correct-horse", now=T0)     # resets the count
        self._fail(auth, 2)                                # still below 3
        assert auth.login("stephen", "correct-horse", now=T0)

    def test_failures_for_one_account_do_not_lock_another(self):
        auth = _auth(max_failed_logins=3)
        auth.register("amara", "another-secret", ActorKind.OPERATOR, [Role.AUTHOR])
        self._fail(auth, 3)
        assert auth.login("amara", "another-secret", now=T0)


# ── Roles ─────────────────────────────────────────────────────────────────────

class TestRoles:

    def test_require_passes_for_a_held_role(self):
        auth = _auth()
        auth.resolve(auth.login("stephen", "correct-horse")).require(Role.AUTHOR)

    def test_require_raises_for_a_missing_role(self):
        auth = _auth()
        identity = auth.resolve(auth.login("stephen", "correct-horse"))
        with pytest.raises(AuthorizationError):
            identity.require(Role.UPLINKER)
