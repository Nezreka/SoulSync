"""Tests for JioSaavn enrichment worker."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from database.music_database import MusicDatabase
from core.enrichment.unmatched import SERVICE_ENTITY_SUPPORT


@dataclass
class _FakeArtist:
    id: str
    name: str
    image_url: str | None = None


@dataclass
class _FakeAlbum:
    id: str
    name: str
    artists: list
    release_date: str = "2020"
    image_url: str | None = None


@dataclass
class _FakeTrack:
    id: str
    name: str
    artists: list
    album: str = "Album"
    album_id: str | None = None
    release_date: str | None = "2020"


class _FakeJioSaavnClient:
    def search_artists(self, query, limit=5):
        if query == "Test Artist":
            return [_FakeArtist("art-1", "Test Artist")]
        return []

    def search_albums(self, query, limit=5):
        if "Test Album" in query:
            return [_FakeAlbum("alb-1", "Test Album", ["Test Artist"])]
        return []

    def search_tracks(self, query, limit=5):
        if "Test Track" in query:
            return [_FakeTrack("trk-1", "Test Track", ["Test Artist"], album_id="alb-1")]
        return []

    def get_album(self, album_id):
        if album_id == "alb-1":
            return {"id": "alb-1", "name": "Test Album", "label": "Label", "total_tracks": 10}
        return None

    def get_track_details(self, track_id):
        if track_id == "trk-1":
            return {"id": "trk-1", "name": "Test Track", "album_id": "alb-1"}
        return None


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.fixture
def worker(db):
    from core.jiosaavn_worker import JioSaavnWorker

    w = JioSaavnWorker(database=db)
    w._client = _FakeJioSaavnClient()
    return w


# The worker reads and writes Library v2 now (docs §32.3.1 stage 2), so the
# fixtures below seed lib2 rows and the assertions read the provider-attempt ledger
# plus external_ids where they used to read jiosaavn_match_status / jiosaavn_id.
# MusicDatabase creates both schemas, so the fixture itself is unchanged.
def _insert_artist(db, name="Test Artist"):
    with db._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO lib2_artists (name, sort_name) VALUES (?, ?)", (name, name))
        conn.commit()
        return cur.lastrowid


def _insert_album(db, artist_id, title="Test Album"):
    with db._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO lib2_albums (primary_artist_id, title, album_type) "
            "VALUES (?, ?, 'album')", (artist_id, title))
        conn.commit()
        return cur.lastrowid


def _insert_track(db, album_id, title="Test Track"):
    with db._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO lib2_tracks (album_id, title) VALUES (?, ?)",
            (album_id, title))
        conn.commit()
        return cur.lastrowid


def _status(db, entity_type, entity_id):
    """``(status, provider_id)`` — the ledger row and external_ids, which is where
    the legacy match-status column and jiosaavn_id column now live."""
    import json

    from core.library2.provider_attempts import attempt_state

    table = {"artist": "lib2_artists", "album": "lib2_albums",
             "track": "lib2_tracks"}[entity_type]
    conn = db._get_connection()
    try:
        state = attempt_state(conn, entity_type=entity_type, entity_id=entity_id)
        raw = conn.execute(
            f"SELECT external_ids FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    return (state.get("jiosaavn", {}).get("status"),
            json.loads(raw or "{}").get("jiosaavn"))


class TestJioSaavnWorkerGating:
    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=False)
    def test_get_stats_reports_disabled(self, _enabled, worker):
        stats = worker.get_stats()
        assert stats["enabled"] is False
        assert stats["running"] is False

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=False)
    def test_run_loop_skips_work_when_disabled(self, _enabled, worker):
        calls = {"get_next": 0, "process": 0}

        def _fake_sleep(_ev, _t):
            worker.should_stop = True

        worker.should_stop = False
        worker._get_next_item = lambda: calls.__setitem__("get_next", calls["get_next"] + 1) or None
        worker._process_item = lambda _item: calls.__setitem__("process", calls["process"] + 1)

        with patch("core.jiosaavn_worker.interruptible_sleep", side_effect=_fake_sleep):
            worker._run()

        assert calls["get_next"] == 0
        assert calls["process"] == 0


class TestJioSaavnWorkerMatching:
    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_artist_match(self, _enabled, worker, db):
        artist = _insert_artist(db)
        worker._process_artist(artist, "Test Artist")
        status, js_id = _status(db, "artist", artist)
        assert status == "matched"
        assert js_id == "art-1"

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_artist_not_found(self, _enabled, worker, db):
        artist = _insert_artist(db, name="Unknown Artist")
        worker._process_artist(artist, "Unknown Artist")
        status, js_id = _status(db, "artist", artist)
        assert status == "not_found"
        assert js_id is None

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_album_match(self, _enabled, worker, db):
        artist = _insert_artist(db)
        album = _insert_album(db, artist)
        worker._process_album(album, "Test Album", "Test Artist")
        status, js_id = _status(db, "album", album)
        assert status == "matched"
        assert js_id == "alb-1"

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_track_match(self, _enabled, worker, db):
        artist = _insert_artist(db)
        album = _insert_album(db, artist)
        track = _insert_track(db, album)
        worker._process_track(track, "Test Track", "Test Artist")
        status, js_id = _status(db, "track", track)
        assert status == "matched"
        assert js_id == "trk-1"

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_preserves_existing_id(self, _enabled, worker, db):
        # An id-only write (e.g. manual match) leaves no attempt recorded.
        # Processing it must PRESERVE the id AND record 'matched' — otherwise
        # _get_next_item, which hands out unattempted rows every loop, re-picks it
        # forever and wedges the queue (#964).
        artist = _insert_artist(db)
        with db._get_connection() as conn:
            conn.execute(
                "UPDATE lib2_artists SET external_ids = ? WHERE id = ?",
                ('{"jiosaavn": "existing"}', artist))
            conn.commit()
        worker._process_artist(artist, "Test Artist")
        status, js_id = _status(db, "artist", artist)
        assert js_id == "existing"
        assert status == "matched"
        # And it must not be handed out again by the queue.
        assert worker._get_next_item() is None

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_mark_status_updates_artist_and_album(self, _enabled, worker, db):
        artist = _insert_artist(db)
        album = _insert_album(db, artist)
        worker._mark_status("artist", artist, "not_found")
        worker._mark_status("album", album, "error")

        from core.library2.provider_attempts import attempt_state

        conn = db._get_connection()
        try:
            artist_state = attempt_state(
                conn, entity_type="artist", entity_id=artist)["jiosaavn"]
            album_state = attempt_state(
                conn, entity_type="album", entity_id=album)["jiosaavn"]
        finally:
            conn.close()
        assert artist_state["status"] == "not_found"
        assert artist_state["last_attempted_at"] is not None
        assert album_state["status"] == "error"
        assert album_state["last_attempted_at"] is not None

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_album_details_unavailable_marks_error_and_does_not_stall(self, _enabled, worker, db):
        # A search match whose detail fetch returns None must be marked 'error' (NOT
        # left unattempted): an unattempted row is re-selected by _get_next_item every
        # loop, spinning the API on one bad id and blocking every later album. 'error'
        # plus a fresh timestamp defers it to the retry_days queue instead (#964).
        artist = _insert_artist(db)
        album = _insert_album(db, artist)
        # Match the artist first so only the album is left pending.
        worker._process_artist(artist, "Test Artist")

        class _ClientNoAlbumDetails(_FakeJioSaavnClient):
            def get_album(self, album_id):
                return None

        worker._client = _ClientNoAlbumDetails()
        worker._process_album(album, "Test Album", "Test Artist")
        status, js_id = _status(db, "album", album)
        assert status == "error"
        assert js_id is None
        # Regression: the row must leave the immediate queue — no other pending work
        # exists, so the queue is now empty rather than re-handing out the album.
        assert worker._get_next_item() is None

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_track_details_unavailable_marks_error_and_does_not_stall(self, _enabled, worker, db):
        # Same stall guard for tracks (#964).
        artist = _insert_artist(db)
        album = _insert_album(db, artist)
        track = _insert_track(db, album)
        # Match the artist + album first so only the track is left pending.
        worker._process_artist(artist, "Test Artist")
        worker._process_album(album, "Test Album", "Test Artist")

        class _ClientNoTrackDetails(_FakeJioSaavnClient):
            def get_track_details(self, track_id):
                return None

        worker._client = _ClientNoTrackDetails()
        worker._process_track(track, "Test Track", "Test Artist")
        status, js_id = _status(db, "track", track)
        assert status == "error"
        assert js_id is None
        assert worker._get_next_item() is None


class TestJioSaavnWorkerQueue:
    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_queue_prefers_artists(self, _enabled, worker, db):
        artist = _insert_artist(db)
        album = _insert_album(db, artist)
        _insert_track(db, album)
        item = worker._get_next_item()
        assert item["type"] == "artist"


class TestJioSaavnDbMigration:
    def test_jiosaavn_columns_exist(self, db):
        with db._get_connection() as conn:
            for table in ("artists", "albums", "tracks"):
                cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                assert "jiosaavn_id" in cols
                assert "jiosaavn_match_status" in cols
                assert "jiosaavn_last_attempted" in cols

    def test_repair_columns_survive_deezer_migration_failure(self, db):
        """#964 regression: the repair-worker columns live in their OWN try block, so
        a failure in the Deezer-column migration must NOT skip them — the repair
        worker queries repair_status on every run and errors out if it's missing."""
        with db._get_connection() as conn:
            real = conn.cursor()
            # Simulate a pre-migration tracks table lacking the repair columns.
            real.execute("DROP TABLE IF EXISTS tracks")
            real.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT)")
            conn.commit()

            class _FailDeezerCursor:
                """Aborts the Deezer half at its first statement; delegates the rest
                (the repair block never touches an 'artists' PRAGMA/ALTER)."""
                def execute(self, sql, *args):
                    if "artists" in sql.lower():
                        raise RuntimeError("simulated Deezer migration failure")
                    return real.execute(sql, *args)
                def fetchall(self):
                    return real.fetchall()
                def fetchone(self):
                    return real.fetchone()

            db._add_deezer_columns(_FailDeezerCursor())
            conn.commit()

            cols = {row[1] for row in real.execute("PRAGMA table_info(tracks)")}
            assert "repair_status" in cols
            assert "repair_last_checked" in cols


def test_jiosaavn_in_service_entity_support():
    assert SERVICE_ENTITY_SUPPORT["jiosaavn"] == ("artist", "album", "track")


def test_enrichment_status_omits_jiosaavn_when_disabled(monkeypatch):
    web_server = pytest.importorskip("web_server")
    monkeypatch.setattr("core.metadata.registry.is_jiosaavn_enabled", lambda: False)
    status = web_server._get_enrichment_status()
    assert "jiosaavn_enrichment" not in status
