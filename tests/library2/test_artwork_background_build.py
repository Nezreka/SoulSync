"""Cold artwork resolves off the request thread (perf25-02).

A first visit to an artist page used to block the HTTP worker on a sequential
provider walk plus a download and two Pillow encodes.  The endpoint now answers
immediately with the placeholder contract and schedules the build.
"""

from __future__ import annotations

import threading

from core.library2 import artwork


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
