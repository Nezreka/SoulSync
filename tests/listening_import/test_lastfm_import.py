from __future__ import annotations

from database.music_database import MusicDatabase
from core.listening_import.lastfm import LastFMListeningImportWorker, normalize_lastfm_scrobble


class _Config:
    def get(self, key, default=None):
        values = {
            "lastfm.api_key": "key",
            "lastfm.username": "tester",
        }
        return values.get(key, default)

    def set(self, key, value):
        pass


def test_normalizes_lastfm_recent_track_payload():
    event = normalize_lastfm_scrobble({
        "name": "Ceremony",
        "artist": {"#text": "New Order"},
        "album": {"#text": "Substance"},
        "mbid": "mbid-1",
        "date": {"uts": "1700000000"},
    })

    assert event == {
        "track_id": "mbid-1",
        "title": "Ceremony",
        "artist": "New Order",
        "album": "Substance",
        "played_at": "2023-11-14 22:13:20",
        "duration_ms": 0,
        "server_source": "lastfm",
        # INT-01: this importer resolves against `lib2_tracks`, so the id it
        # carries is a CATALOGUE id and belongs in the catalogue column.
        "lib2_track_id": None,
    }


def test_lastfm_import_skips_probable_server_duplicates(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    conn = db._get_connection()
    conn.execute(
        """
        INSERT INTO listening_history
            (track_id, title, artist, album, played_at, duration_ms, server_source)
        VALUES ('plex-1', 'Ceremony', 'New Order', 'Substance', '2023-11-14 22:13:25', 180000, 'plex')
        """
    )
    conn.commit()
    conn.close()

    worker = LastFMListeningImportWorker(db, _Config())
    inserted = worker._insert_events_deduped([{
        "track_id": "lastfm-1",
        "title": "Ceremony",
        "artist": "New Order",
        "album": "Substance",
        "played_at": "2023-11-14 22:13:20",
        "duration_ms": 180000,
        "lib2_track_id": None,
    }])

    assert inserted == 0
    assert db.get_listening_stats("all")["total_plays"] == 1



def test_lastfm_backfill_error_does_not_advance_incremental_cursor(tmp_path, monkeypatch):
    import core.listening_import.lastfm as lastfm_module

    db = MusicDatabase(str(tmp_path / "music.db"))
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_recent_tracks(self, username, page=1, limit=200, from_ts=None, to_ts=None, extended=False):
            calls.append({"username": username, "page": page, "from_ts": from_ts})
            if page == 1:
                return _recent_tracks_payload(page=1, total_pages=3, uts_values=[300, 299])
            raise RuntimeError("network fell over")

    monkeypatch.setattr(lastfm_module, "LastFMClient", FakeClient)
    monkeypatch.setattr(lastfm_module, "TRANSIENT_PAGE_RETRIES", 1)

    worker = LastFMListeningImportWorker(db, _Config())
    state = worker.run_once()

    assert state["status"] == "error"
    assert state["progress"] < 100
    assert state["backfill_complete"] is False
    assert state["backfill_next_page"] == 2
    assert state.get("last_imported_ts", 0) == 0
    assert state["pending_last_imported_ts"] == 300
    assert calls == [{"username": "tester", "page": 1, "from_ts": None}, {"username": "tester", "page": 2, "from_ts": None}]


def test_lastfm_retries_transient_page_failure_before_failing_backfill(tmp_path, monkeypatch):
    import core.listening_import.lastfm as lastfm_module

    db = MusicDatabase(str(tmp_path / "music.db"))
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_recent_tracks(self, username, page=1, limit=200, from_ts=None, to_ts=None, extended=False):
            calls.append(page)
            if page == 2 and calls.count(2) == 1:
                raise RuntimeError("500 Server Error for url: https://ws.audioscrobbler.com/2.0/?api_key=secret-token&page=2")
            return _recent_tracks_payload(page=page, total_pages=2, uts_values=[300 - page])

    monkeypatch.setattr(lastfm_module, "LastFMClient", FakeClient)
    monkeypatch.setattr(lastfm_module, "TRANSIENT_PAGE_RETRY_BASE_SECONDS", 0)

    worker = LastFMListeningImportWorker(db, _Config())
    state = worker.run_once()

    assert calls == [1, 2, 2]
    assert state["status"] == "complete"
    assert state["backfill_complete"] is True
    assert state.get("error") is None


def test_lastfm_failed_page_keeps_resume_point_and_redacts_api_key(tmp_path, monkeypatch):
    import core.listening_import.lastfm as lastfm_module

    db = MusicDatabase(str(tmp_path / "music.db"))
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_recent_tracks(self, username, page=1, limit=200, from_ts=None, to_ts=None, extended=False):
            calls.append(page)
            if page == 1:
                return _recent_tracks_payload(page=1, total_pages=3, uts_values=[300, 299])
            raise RuntimeError("500 Server Error for url: https://ws.audioscrobbler.com/2.0/?method=user.getRecentTracks&api_key=secret-token&page=2")

    monkeypatch.setattr(lastfm_module, "LastFMClient", FakeClient)
    monkeypatch.setattr(lastfm_module, "TRANSIENT_PAGE_RETRIES", 2)
    monkeypatch.setattr(lastfm_module, "TRANSIENT_PAGE_RETRY_BASE_SECONDS", 0)

    worker = LastFMListeningImportWorker(db, _Config())
    state = worker.run_once()

    assert calls == [1, 2, 2]
    assert state["status"] == "error"
    assert state["progress"] < 100
    assert state["backfill_complete"] is False
    assert state["backfill_next_page"] == 2
    assert "secret-token" not in state["error"]
    assert "api_key=REDACTED" in state["error"]


def test_lastfm_backfill_resumes_interrupted_page_instead_of_incremental(tmp_path, monkeypatch):
    import core.listening_import.lastfm as lastfm_module

    db = MusicDatabase(str(tmp_path / "music.db"))
    db.set_metadata("lastfm_listening_import_state", """{
        "status": "error",
        "page": 1,
        "total_pages": 2,
        "backfill_complete": false,
        "backfill_next_page": 2,
        "pending_last_imported_ts": 300,
        "pending_last_imported_at": "1970-01-01 00:05:00"
    }""")
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_recent_tracks(self, username, page=1, limit=200, from_ts=None, to_ts=None, extended=False):
            calls.append({"username": username, "page": page, "from_ts": from_ts})
            assert page == 2
            assert from_ts is None
            return _recent_tracks_payload(page=2, total_pages=2, uts_values=[200, 199])

    monkeypatch.setattr(lastfm_module, "LastFMClient", FakeClient)

    worker = LastFMListeningImportWorker(db, _Config())
    state = worker.run_once()

    assert state["status"] == "complete"
    assert state["backfill_complete"] is True
    assert state["last_imported_ts"] == 300
    assert state["backfill_next_page"] is None
    assert calls == [{"username": "tester", "page": 2, "from_ts": None}]


def _recent_tracks_payload(*, page: int, total_pages: int, uts_values: list[int]):
    return {
        "recenttracks": {
            "@attr": {"page": str(page), "totalPages": str(total_pages), "total": str(total_pages * 200)},
            "track": [
                {
                    "name": f"Track {uts}",
                    "artist": {"#text": "Artist"},
                    "album": {"#text": "Album"},
                    "date": {"uts": str(uts)},
                }
                for uts in uts_values
            ],
        }
    }


def test_lastfm_status_normalizes_legacy_false_complete_backfill(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.set_metadata("lastfm_listening_import_state", """{
        "status": "complete",
        "phase": "Last.fm is up to date",
        "page": 5,
        "total_pages": 500,
        "progress": 100,
        "last_success_at": "2026-08-23 10:00:00"
    }""")

    worker = LastFMListeningImportWorker(db, _Config())
    state = worker.status()

    assert state["status"] == "partial"
    assert state["phase"] == "Last.fm import needs to resume"
    assert state["progress"] == 1
    assert state["last_success_at"] is None


def test_lastfm_backfill_does_not_stop_after_duplicate_pages(tmp_path, monkeypatch):
    import core.listening_import.lastfm as lastfm_module

    db = MusicDatabase(str(tmp_path / "music.db"))
    for page in range(1, 4):
        worker = LastFMListeningImportWorker(db, _Config())
        events = [
            {
                "track_id": f"existing-{page}-{i}",
                "title": f"Track {page}-{i}",
                "artist": "Artist",
                "album": "Album",
                "played_at": f"2023-11-14 22:{page:02d}:{i:02d}",
                "duration_ms": 180000,
                "lib2_track_id": None,
            }
            for i in range(2)
        ]
        worker._insert_events_deduped(events)

    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_recent_tracks(self, username, page=1, limit=200, from_ts=None, to_ts=None, extended=False):
            calls.append(page)
            if page <= 3:
                return {
                    "recenttracks": {
                        "@attr": {"page": str(page), "totalPages": "5", "total": "62200"},
                        "track": [
                            {
                                "name": f"Track {page}-{i}",
                                "artist": {"#text": "Artist"},
                                "album": {"#text": "Album"},
                                "date": {"uts": str(1700000000 + page * 60 + i)},
                            }
                            for i in range(2)
                        ],
                    }
                }
            return _recent_tracks_payload(page=page, total_pages=5, uts_values=[1000 - page])

    monkeypatch.setattr(lastfm_module, "LastFMClient", FakeClient)

    worker = LastFMListeningImportWorker(db, _Config())
    state = worker.run_once()

    assert calls == [1, 2, 3, 4, 5]
    assert state["status"] == "complete"
    assert state["backfill_complete"] is True
    assert state["page"] == 5


def test_lastfm_status_normalizes_false_complete_even_when_backfill_flag_was_set(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.set_metadata("lastfm_listening_import_state", """{
        "status": "complete",
        "phase": "Last.fm is up to date",
        "page": 3,
        "total_pages": 311,
        "total_scrobbles": 62200,
        "imported": 600,
        "progress": 100,
        "backfill_complete": true,
        "last_success_at": "2026-08-23 10:00:00"
    }""")

    worker = LastFMListeningImportWorker(db, _Config())
    state = worker.status()

    assert state["status"] == "partial"
    assert state["progress"] == 1
    assert state["last_success_at"] is None



def test_lastfm_events_land_in_the_catalogue_column(tmp_path):
    """INT-01: this importer resolves scrobbles against `lib2_tracks`, so what
    it holds is a CATALOGUE id — but it stored it in `db_track_id`, which is the
    media server's own id namespace. Every stats reader joins the catalogue on
    `lib2_track_id`, so a Last.fm play showed with no cover, no artist link and
    no genre; worse, the startup backfill reads `db_track_id` as a LEGACY track
    id, so a numeric collision could link the play to a different track."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO lib2_artists(name, name_key) VALUES('New Order', 'new order')")
    artist_id = conn.execute("SELECT id FROM lib2_artists").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Substance')",
        (artist_id,))
    album_id = conn.execute("SELECT id FROM lib2_albums").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, 'Ceremony', 1)",
        (album_id,))
    track_id = conn.execute("SELECT id FROM lib2_tracks").fetchone()[0]
    conn.commit()
    conn.close()

    worker = LastFMListeningImportWorker(db, _Config())
    events = [{
        "track_id": "lastfm-1",
        "title": "Ceremony",
        "artist": "New Order",
        "album": "Substance",
        "played_at": "2023-11-14 22:13:20",
        "duration_ms": 180000,
        "server_source": "lastfm",
        "lib2_track_id": None,
    }]
    worker._resolve_db_track_ids(events)
    assert events[0]["lib2_track_id"] == track_id
    assert worker._insert_events_deduped(events) == 1

    conn = db._get_connection()
    row = conn.execute(
        "SELECT db_track_id, lib2_track_id FROM listening_history").fetchone()
    conn.close()
    assert row["lib2_track_id"] == track_id
    assert row["db_track_id"] is None


def test_existing_misfiled_lastfm_rows_are_repaired(tmp_path):
    """The rows already written the wrong way round get moved, source-specific
    and only where the value really names a catalogue row."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO lib2_artists(name, name_key) VALUES('New Order', 'new order')")
    artist_id = conn.execute("SELECT id FROM lib2_artists").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Substance')",
        (artist_id,))
    album_id = conn.execute("SELECT id FROM lib2_albums").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, 'Ceremony', 1)",
        (album_id,))
    track_id = conn.execute("SELECT id FROM lib2_tracks").fetchone()[0]
    conn.execute(
        "INSERT INTO listening_history(track_id, title, artist, album, played_at,"
        " duration_ms, server_source, db_track_id)"
        " VALUES('lastfm-1','Ceremony','New Order','Substance',"
        "        '2023-11-14 22:13:20', 180000, 'lastfm', ?)", (track_id,))
    # A media-server row keeps its own id namespace untouched.
    conn.execute(
        "INSERT INTO listening_history(track_id, title, artist, album, played_at,"
        " duration_ms, server_source, db_track_id)"
        " VALUES('plex-1','Ceremony','New Order','Substance',"
        "        '2023-11-15 22:13:20', 180000, 'plex', ?)", (track_id,))
    conn.commit()
    conn.close()

    # The migration runs once per path per process, so drive it directly.
    conn = db._get_connection()
    db._add_listening_history_table(conn.cursor())
    conn.commit()
    conn.close()

    conn = db._get_connection()
    rows = {
        r["server_source"]: (r["db_track_id"], r["lib2_track_id"])
        for r in conn.execute(
            "SELECT server_source, db_track_id, lib2_track_id FROM listening_history")
    }
    conn.close()
    assert rows["lastfm"] == (None, track_id)
    assert rows["plex"][0] == track_id
