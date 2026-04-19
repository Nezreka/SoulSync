"""Tests for Fix 1.1: worker re-processing loop.

Before this fix:
  * `musicbrainz_worker._get_existing_id` always queried `musicbrainz_id` even
    for `albums`/`tracks` (which use `musicbrainz_release_id` /
    `musicbrainz_recording_id`), so the existence check silently failed and
    every row was re-processed on every loop.
  * `lastfm_worker._get_existing_id` queried a non-existent `lastfm_id`
    column (the real column is `lastfm_url`), with the same effect.
  * Even when workers did find an existing external ID, they returned
    without setting `<provider>_match_status`, so the row stayed NULL and
    the next worker loop re-selected it forever.

This test module covers:
  1. The backfill migration that retroactively sets match_status='matched'
     for rows that already have an external ID populated.
  2. `_get_existing_id` returns the correct column per entity type for
     MusicBrainz and Last.fm.
  3. Each worker's `_process_*` short-circuit path sets match_status to
     'matched' when an existing external ID is found (lastfm, tidal,
     qobuz, musicbrainz).
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from database.music_database import MusicDatabase


# ---------------------------------------------------------------------------
# Minimal stubs for optional deps some workers import at module load.
# ---------------------------------------------------------------------------

def _ensure_stub_module(name: str, attrs: dict | None = None) -> None:
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(mod, k, v)
    sys.modules[name] = mod


# TidalClient / QobuzClient live in core.* and are safe to import but require
# config_manager. We patch the classes at instantiation time instead.


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


# ---------------------------------------------------------------------------
# Backfill migration
# ---------------------------------------------------------------------------

class TestBackfillMigration:
    def test_lastfm_url_set_but_status_null_gets_matched(self, db):
        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO artists (id, name, lastfm_url) VALUES (?, ?, ?)",
                ("a1", "Artist A", "https://last.fm/music/Artist%20A"),
            )
            cur.execute(
                "INSERT INTO artists (id, name, lastfm_url, lastfm_match_status) VALUES (?, ?, ?, ?)",
                ("a2", "Artist B", "https://last.fm/music/B", "matched"),
            )
            cur.execute(
                "INSERT INTO artists (id, name) VALUES (?, ?)",
                ("a3", "Artist C"),  # no url, status stays NULL
            )
            conn.commit()

            # Run backfill a second time (first already ran during __init__)
            db._backfill_match_status_for_existing_ids(cur)
            conn.commit()

            rows = cur.execute(
                "SELECT name, lastfm_match_status FROM artists ORDER BY name"
            ).fetchall()

        by_name = {r[0]: r[1] for r in rows}
        assert by_name["Artist A"] == "matched"
        assert by_name["Artist B"] == "matched"  # untouched
        assert by_name["Artist C"] is None  # no id => no backfill

    def test_musicbrainz_release_id_on_albums(self, db):
        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO artists (id, name) VALUES (?, ?)",
                ("art1", "A"),
            )
            cur.execute(
                "INSERT INTO albums (id, artist_id, title, musicbrainz_release_id) "
                "VALUES (?, ?, ?, ?)",
                ("alb1", "art1", "Album X", "mb-release-uuid"),
            )
            conn.commit()

            db._backfill_match_status_for_existing_ids(cur)
            conn.commit()

            status = cur.execute(
                "SELECT musicbrainz_match_status FROM albums WHERE title = 'Album X'"
            ).fetchone()[0]
        assert status == "matched"

    def test_musicbrainz_recording_id_on_tracks(self, db):
        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO artists (id, name) VALUES ('art2', 'A')")
            cur.execute(
                "INSERT INTO albums (id, artist_id, title) VALUES (?, ?, 'Alb')",
                ("alb2", "art2"),
            )
            cur.execute(
                "INSERT INTO tracks (id, artist_id, album_id, title, musicbrainz_recording_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("trk1", "art2", "alb2", "T1", "mb-rec-uuid"),
            )
            conn.commit()

            db._backfill_match_status_for_existing_ids(cur)
            conn.commit()

            status = cur.execute(
                "SELECT musicbrainz_match_status FROM tracks WHERE title = 'T1'"
            ).fetchone()[0]
        assert status == "matched"

    def test_tidal_and_qobuz_ids_backfilled(self, db):
        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO artists (id, name, tidal_id, qobuz_id) VALUES (?, ?, ?, ?)",
                ("art3", "A", "tidal123", "qobuz456"),
            )
            conn.commit()

            db._backfill_match_status_for_existing_ids(cur)
            conn.commit()

            row = cur.execute(
                "SELECT tidal_match_status, qobuz_match_status FROM artists WHERE id = 'art3'"
            ).fetchone()
        assert tuple(row) == ("matched", "matched")

    def test_empty_string_id_is_not_backfilled(self, db):
        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO artists (id, name, tidal_id) VALUES (?, ?, ?)",
                ("art4", "Empty", ""),
            )
            conn.commit()

            db._backfill_match_status_for_existing_ids(cur)
            conn.commit()

            status = cur.execute(
                "SELECT tidal_match_status FROM artists WHERE id = 'art4'"
            ).fetchone()[0]
        assert status is None


# ---------------------------------------------------------------------------
# _get_existing_id column-mapping correctness
# ---------------------------------------------------------------------------

class TestGetExistingIdColumnMapping:
    def _insert_tree(self, db):
        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO artists (id, name, lastfm_url, musicbrainz_id) "
                "VALUES (?, ?, ?, ?)",
                ("art_x", "A", "https://last.fm/a", "mb-artist"),
            )
            cur.execute(
                "INSERT INTO albums (id, artist_id, title, lastfm_url, musicbrainz_release_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("alb_x", "art_x", "Album", "https://last.fm/album", "mb-release"),
            )
            cur.execute(
                "INSERT INTO tracks (id, artist_id, album_id, title, lastfm_url, musicbrainz_recording_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("trk_x", "art_x", "alb_x", "Track", "https://last.fm/track", "mb-rec"),
            )
            conn.commit()
        return "art_x", "alb_x", "trk_x"

    def test_lastfm_worker_reads_lastfm_url_for_all_entity_types(self, db):
        # Import lazily so test collection doesn't fail if config_manager is unavailable.
        from core import lastfm_worker as lw

        artist_id, album_id, track_id = self._insert_tree(db)

        with patch.object(lw.LastFMWorker, "_init_client", return_value=None):
            worker = lw.LastFMWorker(db)
            assert worker._get_existing_id("artist", artist_id) == "https://last.fm/a"
            assert worker._get_existing_id("album", album_id) == "https://last.fm/album"
            assert worker._get_existing_id("track", track_id) == "https://last.fm/track"

    def test_musicbrainz_worker_reads_correct_column_per_entity(self, db):
        from core import musicbrainz_worker as mbw

        artist_id, album_id, track_id = self._insert_tree(db)

        with patch.object(mbw, "MusicBrainzService", return_value=MagicMock()):
            worker = mbw.MusicBrainzWorker(db)
            assert worker._get_existing_id("artist", artist_id) == "mb-artist"
            assert worker._get_existing_id("album", album_id) == "mb-release"
            assert worker._get_existing_id("track", track_id) == "mb-rec"


# ---------------------------------------------------------------------------
# Worker _process_* short-circuit marks status='matched'
# ---------------------------------------------------------------------------

def _read_status(db, table: str, column: str, row_id: int):
    with db._get_connection() as conn:
        row = conn.execute(
            f"SELECT {column} FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()
    return row[0] if row else None


class TestLastFMWorkerMarksMatched:
    def test_existing_url_triggers_matched_status(self, db):
        from core import lastfm_worker as lw

        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO artists (id, name, lastfm_url) VALUES (?, ?, ?)",
                ("art_lf", "A", "https://last.fm/a"),
            )
            artist_id = "art_lf"
            # Explicitly null out status to simulate legacy row
            cur.execute(
                "UPDATE artists SET lastfm_match_status = NULL WHERE id = ?",
                (artist_id,),
            )
            conn.commit()

        with patch.object(lw.LastFMWorker, "_init_client", return_value=None):
            worker = lw.LastFMWorker(db)
            worker.client = MagicMock()
            worker._process_artist(artist_id, "A")
            # Client must NOT be called because we short-circuited.
            worker.client.get_artist_info.assert_not_called()

        assert _read_status(db, "artists", "lastfm_match_status", artist_id) == "matched"


class TestTidalWorkerMarksMatched:
    def test_existing_tidal_id_triggers_matched_status(self, db):
        from core import tidal_worker as tw

        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO artists (id, name, tidal_id) VALUES (?, ?, ?)",
                ("art_td", "A", "tidal-123"),
            )
            artist_id = "art_td"
            cur.execute(
                "UPDATE artists SET tidal_match_status = NULL WHERE id = ?",
                (artist_id,),
            )
            conn.commit()

        fake_client = MagicMock()
        worker = tw.TidalWorker(db, client=fake_client)
        worker._process_artist(artist_id, "A")

        fake_client.search_artist.assert_not_called()
        assert _read_status(db, "artists", "tidal_match_status", artist_id) == "matched"


class TestQobuzWorkerMarksMatched:
    def test_existing_qobuz_id_triggers_matched_status(self, db):
        from core import qobuz_worker as qw

        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO artists (id, name, qobuz_id) VALUES (?, ?, ?)",
                ("art_qz", "A", "qobuz-xyz"),
            )
            artist_id = "art_qz"
            cur.execute(
                "UPDATE artists SET qobuz_match_status = NULL WHERE id = ?",
                (artist_id,),
            )
            conn.commit()

        fake_client = MagicMock()
        worker = qw.QobuzWorker(db, client=fake_client)
        worker._process_artist(artist_id, "A")

        fake_client.search_artist.assert_not_called()
        assert _read_status(db, "artists", "qobuz_match_status", artist_id) == "matched"


class TestMusicBrainzWorkerMarksMatched:
    def test_existing_mbid_triggers_matched_status_via_service(self, db):
        from core import musicbrainz_worker as mbw

        with db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO artists (id, name, musicbrainz_id) VALUES (?, ?, ?)",
                ("art_mb", "A", "mb-uuid"),
            )
            artist_id = "art_mb"
            conn.commit()

        fake_service = MagicMock()
        with patch.object(mbw, "MusicBrainzService", return_value=fake_service):
            worker = mbw.MusicBrainzWorker(db)
            # mb_service on the instance is the MagicMock
            worker._process_item({"type": "artist", "id": artist_id, "name": "A"})

        fake_service.update_artist_mbid.assert_called_once_with(
            artist_id, "mb-uuid", "matched"
        )
