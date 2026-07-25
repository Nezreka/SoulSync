"""Cold artwork resolves off the request thread (perf25-02).

A first visit to an artist page used to block the HTTP worker on a sequential
provider walk plus a download and two Pillow encodes.  The endpoint now answers
immediately with the placeholder contract and schedules the build.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from core.library2 import artwork


def _config(**values):
    config = MagicMock()
    config.get = MagicMock(side_effect=lambda key, default=None: values.get(key, default))
    return config


def _shim(tmp_path):
    class _DB:
        database_path = str(tmp_path / "music.db")

        def _get_connection(self):
            return object()

    return _DB()


def test_schedule_builds_in_the_background(tmp_path, monkeypatch):
    database = _shim(tmp_path)
    built = []
    thread_names = []

    def fake_build(db, conn, config_manager, kind, entity_id, force=False):
        thread_names.append(threading.current_thread().name)
        built.append((kind, entity_id))
        return "art.jpg"

    monkeypatch.setattr(artwork, "build_artwork", fake_build)

    future = artwork.schedule_artwork_build(database, None, "artist", 5)

    assert future is not None
    future.result(timeout=10)
    assert built == [("artist", 5)]
    assert thread_names[0] != threading.current_thread().name


def test_duplicate_schedules_are_collapsed(tmp_path, monkeypatch):
    database = _shim(tmp_path)
    release = threading.Event()
    started = threading.Event()
    calls = []

    def fake_build(db, conn, config_manager, kind, entity_id, force=False):
        calls.append(entity_id)
        started.set()
        release.wait(timeout=10)
        return None

    monkeypatch.setattr(artwork, "build_artwork", fake_build)

    first = artwork.schedule_artwork_build(database, None, "artist", 9)
    assert started.wait(timeout=10)
    second = artwork.schedule_artwork_build(database, None, "artist", 9)
    release.set()
    first.result(timeout=10)

    assert second is None
    assert calls == [9]

    # Once the in-flight build finished, the entity may be scheduled again.
    third = artwork.schedule_artwork_build(database, None, "artist", 9)
    assert third is not None
    third.result(timeout=10)
    assert calls == [9, 9]


def test_connection_failure_does_not_pin_the_entity(tmp_path, monkeypatch):
    """rev25-01: a transient connection error (SQLite file locked, EMFILE)
    must release the in-flight key just like a build failure does — the old
    code returned before the ``finally`` that released it, pinning the
    entity to its placeholder for the rest of the process's life."""
    database = _shim(tmp_path)
    real_get_connection = database._get_connection
    calls = {"n": 0}

    def flaky_get_connection():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("unable to open database file")
        return real_get_connection()

    monkeypatch.setattr(database, "_get_connection", flaky_get_connection)
    monkeypatch.setattr(artwork, "build_artwork", lambda *_a, **_k: "art.jpg")

    first = artwork.schedule_artwork_build(database, None, "album", 11)
    assert first.result(timeout=10) is False

    second = artwork.schedule_artwork_build(database, None, "album", 11)
    assert second is not None, "the key must be released even when connecting fails"
    assert second.result(timeout=10) is True


def test_build_failure_does_not_pin_the_entity(tmp_path, monkeypatch):
    database = _shim(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(artwork, "build_artwork", boom)

    first = artwork.schedule_artwork_build(database, None, "album", 3)
    first.result(timeout=10)

    monkeypatch.setattr(
        artwork, "build_artwork", lambda *_a, **_k: "art.jpg"
    )
    second = artwork.schedule_artwork_build(database, None, "album", 3)
    assert second is not None
    assert second.result(timeout=10) is True


def test_queue_bound_drops_scheduling_past_the_cap(tmp_path, monkeypatch):
    """rev25-08: submit() has no native queue bound — without one, a client
    hammering the endpoint (or repeated renders of a page full of uncached
    covers) could grow the pending queue without limit."""
    database = _shim(tmp_path)
    monkeypatch.setattr(artwork, "_MAX_BACKGROUND_QUEUE", 2)
    release = threading.Event()

    def blocking_build(*_a, **_k):
        release.wait(timeout=10)
        return None

    monkeypatch.setattr(artwork, "build_artwork", blocking_build)

    first = artwork.schedule_artwork_build(database, None, "artist", 1)
    second = artwork.schedule_artwork_build(database, None, "artist", 2)
    third = artwork.schedule_artwork_build(database, None, "artist", 3)

    assert first is not None
    assert second is not None
    assert third is None, "the queue bound must drop scheduling once saturated"

    release.set()
    first.result(timeout=10)
    second.result(timeout=10)


def test_worker_count_picks_up_a_config_change_once_idle(tmp_path, monkeypatch):
    """rev25-08: the pool's worker count used to freeze from whichever caller
    happened to construct it first — a later change to
    ``auto_import.max_workers``/``library_v2.artwork_cache_workers`` never
    took effect without a process restart. Once the pool is idle (nothing
    scheduled/running), the next call should pick up the new value."""
    database = _shim(tmp_path)
    monkeypatch.setattr(artwork, "build_artwork", lambda *_a, **_k: "art.jpg")
    monkeypatch.setattr(artwork, "_background_executor", None)

    first = artwork.schedule_artwork_build(
        database, _config(**{"auto_import.max_workers": 2}), "artist", 1
    )
    first.result(timeout=10)
    assert artwork._background_executor_workers == 2

    second = artwork.schedule_artwork_build(
        database, _config(**{"auto_import.max_workers": 5}), "artist", 2
    )
    second.result(timeout=10)
    assert artwork._background_executor_workers == 5


def test_shutdown_background_executor_is_a_safe_noop_when_never_created():
    artwork.shutdown_background_executor()


def test_shutdown_background_executor_shuts_down_the_pool(tmp_path, monkeypatch):
    """rev25-08: ThreadPoolExecutor's own atexit hook joins non-daemon worker
    threads, which used to delay interpreter/container exit indefinitely on a
    slow in-flight build. The app's shutdown handler must be able to tear the
    pool down without waiting for that."""
    database = _shim(tmp_path)
    monkeypatch.setattr(artwork, "build_artwork", lambda *_a, **_k: "art.jpg")
    monkeypatch.setattr(artwork, "_background_executor", None)

    future = artwork.schedule_artwork_build(database, None, "artist", 42)
    future.result(timeout=10)
    executor_ref = artwork._background_executor

    artwork.shutdown_background_executor()

    assert artwork._background_executor is None
    with pytest.raises(RuntimeError):
        executor_ref.submit(lambda: None)
