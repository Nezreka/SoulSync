from __future__ import annotations

import json
from database.music_database import MusicDatabase
from core.listening_import.listenbrainz import ListenBrainzListeningImportWorker, normalize_listenbrainz_listen


class _Config:
    def __init__(self, values=None):
        self.values = values or {
            "listenbrainz.token": "token-123",
            "listenbrainz.username": "tester",
        }

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def test_normalizes_listenbrainz_listen_payload():
    event = normalize_listenbrainz_listen({
        "listened_at": 1700000000,
        "recording_msid": "msid-123",
        "track_metadata": {
            "track_name": "Blue Monday",
            "artist_name": "New Order",
            "release_name": "Power, Corruption & Lies",
            "additional_info": {
                "recording_mbid": "mbid-456",
                "duration_ms": 449000,
            },
        },
    })

    assert event == {
        "track_id": "mbid-456",
        "title": "Blue Monday",
        "artist": "New Order",
        "album": "Power, Corruption & Lies",
        "played_at": "2023-11-14 22:13:20",
        "duration_ms": 449000,
        "server_source": "listenbrainz",
        "db_track_id": None,
    }


def test_normalizes_listenbrainz_listen_payload_fallback_msid_and_duration():
    event = normalize_listenbrainz_listen({
        "listened_at": 1700000000,
        "recording_msid": "msid-123",
        "track_metadata": {
            "track_name": "Ceremony",
            "artist_name": "New Order",
            "additional_info": {
                "duration": 264,
            },
        },
    })

    assert event is not None
    assert event["track_id"] == "msid-123"
    assert event["duration_ms"] == 264000
    assert event["album"] == ""


def test_listenbrainz_import_skips_probable_server_duplicates(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    conn = db._get_connection()
    conn.execute(
        """
        INSERT INTO listening_history
            (track_id, title, artist, album, played_at, duration_ms, server_source)
        VALUES ('plex-1', 'Blue Monday', 'New Order', 'PCL', '2023-11-14 22:13:25', 180000, 'plex')
        """
    )
    conn.commit()
    conn.close()

    worker = ListenBrainzListeningImportWorker(db, _Config())
    inserted = worker._insert_events_deduped([{
        "track_id": "lb-1",
        "title": "Blue Monday",
        "artist": "New Order",
        "album": "PCL",
        "played_at": "2023-11-14 22:13:20",
        "duration_ms": 180000,
        "db_track_id": None,
    }])

    assert inserted == 0
    assert db.get_listening_stats("all")["total_plays"] == 1


def test_listenbrainz_import_sets_scrobbled_listenbrainz_flag(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    worker = ListenBrainzListeningImportWorker(db, _Config())

    inserted = worker._insert_events_deduped([{
        "track_id": "lb-100",
        "title": "Temptation",
        "artist": "New Order",
        "album": "Singles",
        "played_at": "2023-11-14 22:13:20",
        "duration_ms": 180000,
        "db_track_id": None,
    }])

    assert inserted == 1
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT scrobbled_listenbrainz FROM listening_history WHERE track_id = 'lb-100'")
    row = cursor.fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 1


def test_listenbrainz_backfill_error_does_not_advance_incremental_cursor(tmp_path, monkeypatch):
    import core.listening_import.listenbrainz as lb_module

    db = MusicDatabase(str(tmp_path / "music.db"))
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_listen_count(self, username):
            return 300

        def get_user_listens(self, username, min_ts=None, max_ts=None, count=100):
            calls.append({"username": username, "min_ts": min_ts, "max_ts": max_ts})
            if len(calls) == 1:
                return _listens_payload(ts_values=[300, 290])
            raise RuntimeError("server error")

    monkeypatch.setattr(lb_module, "ListenBrainzClient", FakeClient)
    monkeypatch.setattr(lb_module, "TRANSIENT_PAGE_RETRIES", 1)

    worker = ListenBrainzListeningImportWorker(db, _Config())
    state = worker.run_once()

    assert state["status"] == "error"
    assert state["progress"] < 100
    assert state["backfill_complete"] is False
    assert state["pending_max_ts"] == 192
    assert state.get("last_imported_ts", 0) == 0
    assert state["pending_last_imported_ts"] == 300


def test_listenbrainz_retries_transient_page_failure(tmp_path, monkeypatch):
    import core.listening_import.listenbrainz as lb_module

    db = MusicDatabase(str(tmp_path / "music.db"))
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_listen_count(self, username):
            return 2

        def get_user_listens(self, username, min_ts=None, max_ts=None, count=100):
            calls.append(max_ts)
            if len(calls) == 2 and calls.count(max_ts) == 1:
                raise RuntimeError("500 Server Error")
            if len(calls) == 1:
                return _listens_payload(ts_values=[300])
            return _listens_payload(ts_values=[200], is_last=True)

    monkeypatch.setattr(lb_module, "ListenBrainzClient", FakeClient)
    monkeypatch.setattr(lb_module, "TRANSIENT_PAGE_RETRY_BASE_SECONDS", 0)

    worker = ListenBrainzListeningImportWorker(db, _Config())
    state = worker.run_once()

    assert len(calls) == 3
    assert state["status"] == "complete"
    assert state["backfill_complete"] is True
    assert state.get("error") is None


def test_listenbrainz_backfill_resumes_interrupted_cursor(tmp_path, monkeypatch):
    import core.listening_import.listenbrainz as lb_module

    db = MusicDatabase(str(tmp_path / "music.db"))
    db.set_metadata("listenbrainz_listening_import_state", """{
        "status": "error",
        "page": 1,
        "total_pages": 2,
        "backfill_complete": false,
        "pending_max_ts": 250,
        "pending_last_imported_ts": 300,
        "pending_last_imported_at": "1970-01-01 00:05:00"
    }""")
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_listen_count(self, username):
            return 200

        def get_user_listens(self, username, min_ts=None, max_ts=None, count=100):
            calls.append({"username": username, "max_ts": max_ts})
            assert max_ts == 250
            return _listens_payload(ts_values=[200], is_last=True)

    monkeypatch.setattr(lb_module, "ListenBrainzClient", FakeClient)

    worker = ListenBrainzListeningImportWorker(db, _Config())
    state = worker.run_once()

    assert state["status"] == "complete"
    assert state["backfill_complete"] is True
    assert state["last_imported_ts"] == 300
    assert state.get("pending_max_ts") is None
    assert calls == [{"username": "tester", "max_ts": 250}]


def test_listenbrainz_incremental_sync_starts_with_newest_page(tmp_path, monkeypatch):
    import core.listening_import.listenbrainz as lb_module

    db = MusicDatabase(str(tmp_path / "music.db"))
    db.set_metadata("listenbrainz_listening_import_state", """{
        "username": "tester",
        "status": "complete",
        "backfill_complete": true,
        "last_imported_ts": 1000000,
        "last_imported_at": "1970-01-12 13:46:40"
    }""")
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_listen_count(self, username):
            return 500

        def get_user_listens(self, username, min_ts=None, max_ts=None, count=100):
            calls.append({"username": username, "min_ts": min_ts, "max_ts": max_ts})
            return _listens_payload(ts_values=[1000500], is_last=True)

    monkeypatch.setattr(lb_module, "ListenBrainzClient", FakeClient)

    worker = ListenBrainzListeningImportWorker(db, _Config())
    state = worker.run_once()

    assert state["status"] == "complete"
    assert state["last_imported_ts"] == 1000500
    assert calls[0]["min_ts"] is None
    assert calls[0]["max_ts"] is None


def test_corrected_username_does_not_reuse_old_accounts_cursor(tmp_path, monkeypatch):
    import core.listening_import.listenbrainz as lb_module

    db = MusicDatabase(str(tmp_path / "music.db"))
    db.set_metadata("listenbrainz_listening_import_state", json.dumps({
        "username": "k", "status": "complete", "backfill_complete": True,
        "last_imported_ts": 999999, "page": 3, "total_pages": 3,
    }))
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_listen_count(self, username):
            return 100

        def get_user_listens(self, username, min_ts=None, max_ts=None, count=100):
            calls.append((username, min_ts, max_ts))
            return _listens_payload(ts_values=[100], is_last=True)

    monkeypatch.setattr(lb_module, "ListenBrainzClient", FakeClient)
    state = ListenBrainzListeningImportWorker(db, _Config()).run_once(username="corrected")

    assert state["status"] == "complete"
    assert calls == [("corrected", None, None)]
    assert state["last_imported_ts"] == 100


def _listens_payload(*, ts_values: list[int], is_last: bool = False):
    listens = [
        {
            "listened_at": ts,
            "recording_msid": f"msid-{ts}",
            "track_metadata": {
                "track_name": f"Track {ts}",
                "artist_name": "Artist",
                "release_name": "Album",
                "additional_info": {
                    "recording_mbid": f"mbid-{ts}",
                    "duration_ms": 180000,
                },
            },
        }
        for ts in ts_values
    ]
    if not is_last and len(listens) < 100:
        # pad to 100 so it's treated as a full page
        extra = 100 - len(listens)
        base = min(ts_values) if ts_values else 1000
        for i in range(1, extra + 1):
            listens.append({
                "listened_at": base - i,
                "recording_msid": f"msid-pad-{i}",
                "track_metadata": {
                    "track_name": f"Padded Track {i}",
                    "artist_name": "Artist",
                    "release_name": "Album",
                },
            })
    return {
        "payload": {
            "count": len(listens),
            "listens": listens,
        }
    }
