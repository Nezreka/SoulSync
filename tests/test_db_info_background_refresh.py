"""The dashboard's library counts are served from cache and refreshed in the
background (6.4s of queries on a 311k-track library, asked every 60s while a
page was open). A finished scan clears them."""

from __future__ import annotations

import threading
import time

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    database = MusicDatabase(str(tmp_path / "m.db"))
    calls = []
    gate = threading.Event()

    def slow(server_source=None):
        calls.append(server_source)
        if len(calls) > 1:
            gate.wait(5)
        return {"tracks": len(calls)}
    monkeypatch.setattr(database, "_database_info_for_server", slow)
    MusicDatabase._db_info_cache.clear()
    MusicDatabase._db_info_refreshing.clear()
    yield database, calls, gate
    gate.set()
    MusicDatabase._db_info_cache.clear()
    MusicDatabase._db_info_refreshing.clear()


def test_a_stale_answer_is_served_at_once_and_refreshed_behind(db):
    database, calls, gate = db
    assert database.get_database_info_for_server("plex")["tracks"] == 1
    for key, (at, info) in list(MusicDatabase._db_info_cache.items()):
        MusicDatabase._db_info_cache[key] = (at - 10_000, info)
    started = time.monotonic()
    assert database.get_database_info_for_server("plex")["tracks"] == 1
    assert time.monotonic() - started < 1, "the stale answer must not wait for the refresh"
    # a second ask while it refreshes starts no second refresh
    database.get_database_info_for_server("plex")
    gate.set()
    for _ in range(50):
        if not MusicDatabase._db_info_refreshing:
            break
        time.sleep(0.05)
    assert len(calls) == 2
    assert database.get_database_info_for_server("plex")["tracks"] == 2


def test_a_scan_clears_the_counts(db):
    database, calls, _gate = db
    database.get_database_info_for_server("plex")
    MusicDatabase.invalidate_database_info()
    assert database.get_database_info_for_server("plex")["tracks"] == 2
