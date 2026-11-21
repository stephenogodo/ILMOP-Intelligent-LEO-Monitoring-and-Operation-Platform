"""
tests/test_command_store.py

Tests for the in-memory command store (S8-05).
"""

import threading

from shared.schemas.command_schema import Actor, ActorKind, CommandStatus
from services.command.authoring import author_command
from services.command.lifecycle import approve
from services.command.store import InMemoryCommandStore

OPERATOR = Actor(name="stephen", kind=ActorKind.OPERATOR)


def _draft(key=None):
    return author_command(
        "SAT-001", "SET_SAFE_MODE", {"enabled": True}, OPERATOR, idempotency_key=key
    )


class TestCreateAndRead:

    def test_stored_command_can_be_read_back(self):
        store, cmd = InMemoryCommandStore(), _draft()
        store.create_if_key_absent(cmd)
        assert store.get(cmd.command_id) == cmd

    def test_unknown_id_returns_none(self):
        assert InMemoryCommandStore().get("nope") is None

    def test_find_by_key(self):
        store, cmd = InMemoryCommandStore(), _draft("k1")
        store.create_if_key_absent(cmd)
        assert store.find_by_idempotency_key("k1") == cmd

    def test_unknown_key_returns_none(self):
        assert InMemoryCommandStore().find_by_idempotency_key("k1") is None

    def test_second_command_with_same_key_returns_the_first(self):
        store = InMemoryCommandStore()
        first, second = _draft("k1"), _draft("k1")
        store.create_if_key_absent(first)
        returned = store.create_if_key_absent(second)
        assert returned.command_id == first.command_id
        assert store.get(second.command_id) is None   # never stored

    def test_commands_without_a_key_are_never_merged(self):
        store = InMemoryCommandStore()
        a, b = _draft(), _draft()
        store.create_if_key_absent(a)
        store.create_if_key_absent(b)
        assert store.get(a.command_id) and store.get(b.command_id)


class TestCompareAndSave:

    def test_swap_succeeds_when_status_matches(self):
        store, cmd = InMemoryCommandStore(), _draft()
        store.create_if_key_absent(cmd)
        assert store.compare_and_save(approve(cmd, OPERATOR), CommandStatus.DRAFT)
        assert store.get(cmd.command_id).status is CommandStatus.APPROVED

    def test_swap_fails_when_status_has_moved_on(self):
        store, cmd = InMemoryCommandStore(), _draft()
        store.create_if_key_absent(cmd)
        approved = approve(cmd, OPERATOR)
        store.compare_and_save(approved, CommandStatus.DRAFT)
        # A second caller still believes the command is a DRAFT.
        assert not store.compare_and_save(approved, CommandStatus.DRAFT)

    def test_swap_fails_for_unknown_command(self):
        assert not InMemoryCommandStore().compare_and_save(_draft(), CommandStatus.DRAFT)


class TestConcurrency:

    def test_same_key_from_many_threads_creates_one_command(self):
        store = InMemoryCommandStore()
        winners = []

        def submit():
            returned = store.create_if_key_absent(_draft("shared-key"))
            winners.append(returned.command_id)

        threads = [threading.Thread(target=submit) for _ in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()

        # Every thread must have been given the same command.
        assert len(set(winners)) == 1
