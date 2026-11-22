"""The certificate bootstrap: a fresh server has no certificate, so
presence cannot start, so pairing codes are never registered and
every app lookup comes back empty. Nothing in the app asked for the
first certificate. These tests pin the wiring that fixes that:
start() and register_created() kick a background bootstrap, the
bootstrap obtains the certificate and then starts presence, and at
most one bootstrap runs at a time.
"""
from __future__ import annotations

import threading
import time

from core.connect import rendezvous


class FakeStore:
    def __init__(self):
        self.fp = None

    def fingerprint(self):
        return self.fp


class FakeIdentity:
    fingerprint = "abcd1234ef00"
    lookup_id = "ab" * 32


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.registered = []

    def start(self):
        pass

    def register_code(self, code_hash, ttl):
        self.registered.append((code_hash, ttl))


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_bootstrap_obtains_cert_then_starts_presence(monkeypatch):
    rendezvous._reset_for_tests()
    store = FakeStore()
    monkeypatch.setattr(rendezvous, "_identity_and_store", lambda gd, cm: (FakeIdentity(), store))
    monkeypatch.setattr(rendezvous, "PresenceClient", FakeClient)
    monkeypatch.setattr(rendezvous, "_local_lan_ips", lambda: [])
    import core.connect.addresses as addresses

    monkeypatch.setattr(addresses, "web_port", lambda: 8008)

    def fake_ensure(gd, cm):
        store.fp = "f" * 64
        return True

    monkeypatch.setattr(rendezvous, "ensure_certificate", fake_ensure)

    # No certificate: start() returns None but kicks the bootstrap.
    assert rendezvous.start(lambda: object(), object()) is None
    assert _wait_for(lambda: rendezvous._client is not None)
    assert isinstance(rendezvous._client, FakeClient)
    rendezvous._reset_for_tests()


def test_register_created_kicks_bootstrap_when_no_client(monkeypatch):
    rendezvous._reset_for_tests()
    kicked = threading.Event()
    monkeypatch.setattr(rendezvous, "_kick_bootstrap", lambda gd, cm: kicked.set())

    # Without the wiring arguments it stays a silent no-op (old callers).
    rendezvous.register_created("ab" * 32, 600)
    assert not kicked.is_set()

    rendezvous.register_created("ab" * 32, 600, lambda: object(), object())
    assert kicked.is_set()
    rendezvous._reset_for_tests()


def test_only_one_bootstrap_at_a_time(monkeypatch):
    rendezvous._reset_for_tests()
    release = threading.Event()
    calls = []

    def slow_ensure(gd, cm):
        calls.append(1)
        release.wait(5)
        return False

    store = FakeStore()
    monkeypatch.setattr(rendezvous, "_identity_and_store", lambda gd, cm: (FakeIdentity(), store))
    monkeypatch.setattr(rendezvous, "ensure_certificate", slow_ensure)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    rendezvous._kick_bootstrap(lambda: object(), object())
    rendezvous._kick_bootstrap(lambda: object(), object())
    assert _wait_for(lambda: len(calls) == 1)
    time.sleep(0.1)
    assert len(calls) == 1  # the second kick was refused, not queued
    release.set()
    assert _wait_for(lambda: not rendezvous._bootstrap_running)
    rendezvous._reset_for_tests()


def test_failed_cycle_releases_flag_and_cooldown_gates_the_next(monkeypatch):
    rendezvous._reset_for_tests()
    store = FakeStore()
    monkeypatch.setattr(rendezvous, "_identity_and_store", lambda gd, cm: (FakeIdentity(), store))
    calls = []

    def failing_ensure(gd, cm):
        calls.append(1)
        return False

    monkeypatch.setattr(rendezvous, "ensure_certificate", failing_ensure)
    monkeypatch.setattr(rendezvous, "_BOOTSTRAP_RETRY_DELAY_SECONDS", 0)

    # One cycle: two attempts, then the flag is released.
    rendezvous._kick_bootstrap(lambda: object(), object())
    assert _wait_for(lambda: not rendezvous._bootstrap_running and len(calls) == 2)

    # Inside the cooldown an immediate kick starts nothing.
    rendezvous._kick_bootstrap(lambda: object(), object())
    time.sleep(0.15)
    assert len(calls) == 2

    # After the cooldown a kick runs a fresh cycle.
    monkeypatch.setattr(rendezvous, "_BOOTSTRAP_COOLDOWN_SECONDS", 0)
    rendezvous._kick_bootstrap(lambda: object(), object())
    assert _wait_for(lambda: not rendezvous._bootstrap_running and len(calls) == 4)
    rendezvous._reset_for_tests()
