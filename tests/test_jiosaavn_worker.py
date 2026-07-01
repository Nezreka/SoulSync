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


def _insert_artist(db, artist_id="a1", name="Test Artist"):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
            (artist_id, name, "test"),
        )
        conn.commit()


def _insert_album(db, album_id="al1", title="Test Album", artist_id="a1"):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO albums (id, title, artist_id, server_source) VALUES (?, ?, ?, ?)",
            (album_id, title, artist_id, "test"),
        )
        conn.commit()


def _insert_track(db, track_id="t1", title="Test Track", artist_id="a1", album_id="al1"):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, album_id, server_source) VALUES (?, ?, ?, ?, ?)",
            (track_id, title, artist_id, album_id, "test"),
        )
        conn.commit()


def _status(db, table, entity_id, col="jiosaavn_match_status"):
    with db._get_connection() as conn:
        row = conn.execute(f"SELECT {col}, jiosaavn_id FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        return row[0], row[1]


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
        _insert_artist(db)
        worker._process_artist("a1", "Test Artist")
        status, js_id = _status(db, "artists", "a1")
        assert status == "matched"
        assert js_id == "art-1"

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_artist_not_found(self, _enabled, worker, db):
        _insert_artist(db, name="Unknown Artist")
        worker._process_artist("a1", "Unknown Artist")
        status, js_id = _status(db, "artists", "a1")
        assert status == "not_found"
        assert js_id is None

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_album_match(self, _enabled, worker, db):
        _insert_artist(db)
        _insert_album(db)
        worker._process_album("al1", "Test Album", "Test Artist")
        status, js_id = _status(db, "albums", "al1")
        assert status == "matched"
        assert js_id == "alb-1"

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_track_match(self, _enabled, worker, db):
        _insert_artist(db)
        _insert_album(db)
        _insert_track(db)
        worker._process_track("t1", "Test Track", "Test Artist")
        status, js_id = _status(db, "tracks", "t1")
        assert status == "matched"
        assert js_id == "trk-1"

    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_preserves_existing_id(self, _enabled, worker, db):
        _insert_artist(db)
        with db._get_connection() as conn:
            conn.execute(
                "UPDATE artists SET jiosaavn_id = ? WHERE id = ?",
                ("existing", "a1"),
            )
            conn.commit()
        worker._process_artist("a1", "Test Artist")
        status, js_id = _status(db, "artists", "a1")
        assert js_id == "existing"
        assert status is None


    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_mark_status_updates_artist_and_album(self, _enabled, worker, db):
        _insert_artist(db)
        _insert_album(db)
        worker._mark_status("artist", "a1", "not_found")
        worker._mark_status("album", "al1", "error")
        with db._get_connection() as conn:
            artist = conn.execute(
                "SELECT jiosaavn_match_status, updated_at FROM artists WHERE id = ?",
                ("a1",),
            ).fetchone()
            album = conn.execute(
                "SELECT jiosaavn_match_status, updated_at FROM albums WHERE id = ?",
                ("al1",),
            ).fetchone()
        assert artist[0] == "not_found"
        assert artist[1] is not None
        assert album[0] == "error"
        assert album[1] is not None


class TestJioSaavnWorkerQueue:
    @patch("core.jiosaavn_worker.is_jiosaavn_enabled", return_value=True)
    def test_queue_prefers_artists(self, _enabled, worker, db):
        _insert_artist(db, "a1", "Test Artist")
        _insert_album(db)
        _insert_track(db, album_id="al1")
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


def test_jiosaavn_in_service_entity_support():
    assert SERVICE_ENTITY_SUPPORT["jiosaavn"] == ("artist", "album", "track")
