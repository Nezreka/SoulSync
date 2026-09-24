from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import sqlite3
from types import SimpleNamespace

import pytest

from core.listening_import.dedup import insert_import_events
from core.listening_import.lastfm import LastFMListeningImportWorker
from core.listening_import.listenbrainz import ListenBrainzListeningImportWorker


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "history.db")
    def connect():
        return sqlite3.connect(path, timeout=10)
    conn = connect()
    conn.execute("""CREATE TABLE listening_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, track_id TEXT, title TEXT, artist TEXT,
        album TEXT, played_at TEXT, duration_ms INTEGER, server_source TEXT, db_track_id INTEGER,
        scrobbled_lastfm INTEGER DEFAULT 0, scrobbled_listenbrainz INTEGER DEFAULT 0,
        profile_id INTEGER NOT NULL DEFAULT 1,
        UNIQUE(track_id, played_at, server_source, profile_id))""")
    conn.close()
    return SimpleNamespace(_get_connection=connect, get_metadata=lambda _: None)


def event(seconds=0, **overrides):
    return {"track_id": "recording-1", "title": "Song", "artist": "Artist", "album": "Album",
            "played_at": (datetime(2026, 9, 21, 10) + timedelta(seconds=seconds)).isoformat(sep=" "),
            "duration_ms": 90000, **overrides}


def count(db):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM listening_history").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture(params=[LastFMListeningImportWorker, ListenBrainzListeningImportWorker])
def worker(request, db):
    return request.param(db, SimpleNamespace(get=lambda key, default=None: default))


def test_same_source_short_repeats_survive_across_batches_and_reimports(worker, db):
    assert worker._insert_events_deduped([event()]) == 1
    assert worker._insert_events_deduped([event(1), event(90)]) == 2
    assert worker._insert_events_deduped([event(), event(1), event(90)]) == 0
    assert count(db) == 3


def test_duplicate_payload_and_changed_recording_id_are_idempotent(worker, db):
    assert worker._insert_events_deduped([event(), event(track_id="new-id")]) == 1
    assert worker._insert_events_deduped([event(track_id="changed-again")]) == 0
    assert count(db) == 1


def test_one_server_play_cannot_absorb_two_source_plays(worker, db):
    insert_import_events(db, [event(5)], "plex")
    assert worker._insert_events_deduped([event(9), event(5)]) == 1
    assert worker._insert_events_deduped([event(5), event(9)]) == 0
    assert count(db) == 2


def test_matches_are_persistent_across_worker_instances_and_pages(worker, db):
    insert_import_events(db, [event(5)], "plex")
    assert worker._insert_events_deduped([event()]) == 0
    restarted = type(worker)(db, worker.config_manager)
    assert restarted._insert_events_deduped([event()]) == 0
    assert restarted._insert_events_deduped([event(9)]) == 1
    assert count(db) == 2


def test_large_clock_differences_and_different_albums_are_not_guessed(worker, db):
    insert_import_events(db, [event()], "plex")
    assert worker._insert_events_deduped([event(90), event(5, album="Live")]) == 2
    assert count(db) == 3


def test_trimmed_unicode_case_variants_match(worker, db):
    insert_import_events(db, [event(title=" SÖNG ", artist=" ARTIST ")], "plex")
    assert worker._insert_events_deduped([event(5, title="söng")]) == 0
    assert count(db) == 1


def test_multiple_sources_and_concurrent_imports_count_a_play_once(db):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: insert_import_events(db, [event(pair[1])], pair[0]),
                                [("lastfm", 0), ("listenbrainz", 5)]))
    assert sum(results) == 1
    assert count(db) == 1
    assert insert_import_events(db, [event()], "lastfm") == 0
    assert insert_import_events(db, [event(5)], "listenbrainz") == 0
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT scrobbled_lastfm, scrobbled_listenbrainz FROM listening_history").fetchone() == (1, 1)
    finally:
        conn.close()


def test_preexisting_same_source_history_uses_exact_timestamp(worker, db):
    source = "lastfm" if isinstance(worker, LastFMListeningImportWorker) else "listenbrainz"
    conn = db._get_connection()
    conn.execute("INSERT INTO listening_history (track_id,title,artist,album,played_at,server_source) VALUES (?,?,?,?,?,?)",
                 ("old-id", "Song", "Artist", "Album", event()["played_at"], source))
    conn.commit()
    conn.close()
    assert worker._insert_events_deduped([event(), event(90)]) == 1
    assert count(db) == 2


def test_deleted_history_can_be_reimported(worker, db):
    assert worker._insert_events_deduped([event()]) == 1
    conn = db._get_connection()
    conn.execute("DELETE FROM listening_history")
    conn.commit()
    conn.close()
    assert worker._insert_events_deduped([event()]) == 1
    assert worker._insert_events_deduped([event()]) == 0
    assert count(db) == 1


def test_later_server_and_player_arrivals_use_same_dedup_path(db):
    from database.music_database import MusicDatabase
    assert insert_import_events(db, [event()], "listenbrainz") == 1
    assert MusicDatabase.insert_listening_events(db, [event(5, server_source="plex", db_track_id=7)]) == 0
    assert MusicDatabase.insert_listening_events(db, [event(90, server_source="plex", db_track_id=7)]) == 1
    assert MusicDatabase.insert_listening_events(db, [event(5, server_source="plex", db_track_id=7)]) == 0
    assert count(db) == 2
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT db_track_id FROM listening_history ORDER BY id LIMIT 1").fetchone() == (7,)
    finally:
        conn.close()
