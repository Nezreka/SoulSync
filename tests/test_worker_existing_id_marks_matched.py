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

    def test_lastfm_worker_reads_its_url_from_lib2_external_ids(self, db):
        """Last.fm has moved to Library v2 (docs §32.3.1 stage 2), so its
        existence check reads ``lib2_*.external_ids`` rather than the legacy
        ``lastfm_url`` column. The behaviour being pinned is unchanged: the check
        must actually find the id, or every row is re-processed forever."""
        import json

        from core import lastfm_worker as lw

        ids = json.dumps({"lastfm": "https://last.fm/a"})
        with db._get_connection() as conn:
            cur = conn.cursor()
            artist_id = cur.execute(
                "INSERT INTO lib2_artists(name, sort_name, external_ids) "
                "VALUES('A','A',?)", (ids,)).lastrowid
            album_id = cur.execute(
                "INSERT INTO lib2_albums(primary_artist_id,title,album_type,external_ids) "
                "VALUES(?,'Album','album',?)", (artist_id, ids)).lastrowid
            track_id = cur.execute(
                "INSERT INTO lib2_tracks(album_id,title,external_ids) "
                "VALUES(?,'Track',?)", (album_id, ids)).lastrowid
            conn.commit()

        with patch.object(lw.LastFMWorker, "_init_client", return_value=None):
            worker = lw.LastFMWorker(db)
            assert worker._get_existing_id("artist", artist_id) == "https://last.fm/a"
            assert worker._get_existing_id("album", album_id) == "https://last.fm/a"
            assert worker._get_existing_id("track", track_id) == "https://last.fm/a"

    def test_musicbrainz_worker_finds_the_mbid_on_every_entity(self, db):
        """The original bug was a per-entity column map that queried all three as
        ``musicbrainz_id``, so albums and tracks silently never found theirs. lib2
        keeps the mbid under one name on every entity, which removes the map — but
        the guarantee still has to hold, so it is still pinned."""
        from core import musicbrainz_worker as mbw

        with db._get_connection() as conn:
            cur = conn.cursor()
            artist_id = cur.execute(
                "INSERT INTO lib2_artists(name, sort_name, musicbrainz_id) "
                "VALUES('A','A','mb-artist')").lastrowid
            album_id = cur.execute(
                "INSERT INTO lib2_albums(primary_artist_id,title,album_type,"
                "musicbrainz_id) VALUES(?,'Album','album','mb-release')",
                (artist_id,)).lastrowid
            track_id = cur.execute(
                "INSERT INTO lib2_tracks(album_id,title,musicbrainz_id) "
                "VALUES(?,'Track','mb-rec')", (album_id,)).lastrowid
            conn.commit()

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
        """Same guarantee as before, in the new home: finding an existing id must
        record the attempt, or the worker re-selects that row on every loop. The
        status now lives in the provider-attempt ledger instead of
        ``artists.lastfm_match_status``."""
        import json

        from core import lastfm_worker as lw
        from core.library2.provider_attempts import attempt_state

        with db._get_connection() as conn:
            artist_id = conn.execute(
                "INSERT INTO lib2_artists(name, sort_name, external_ids) "
                "VALUES('A','A',?)",
                (json.dumps({"lastfm": "https://last.fm/a"}),)).lastrowid
            conn.commit()

        with patch.object(lw.LastFMWorker, "_init_client", return_value=None):
            worker = lw.LastFMWorker(db)
            worker.client = MagicMock()
            worker._process_artist(artist_id, "A")
            # Client must NOT be called because we short-circuited.
            worker.client.get_artist_info.assert_not_called()

        with db._get_connection() as conn:
            state = attempt_state(conn, entity_type="artist", entity_id=artist_id)
        assert state["lastfm"]["status"] == "matched"


class TestTidalWorkerMarksMatched:
    def test_existing_tidal_id_triggers_matched_status(self, db):
        """Finding an existing id must record the attempt, or the worker re-selects
        that row on every loop. The status lives in the provider-attempt ledger now
        instead of ``artists.tidal_match_status``."""
        from core import tidal_worker as tw
        from core.library2.provider_attempts import attempt_state

        with db._get_connection() as conn:
            artist_id = conn.execute(
                "INSERT INTO lib2_artists(name, sort_name, external_ids) "
                "VALUES('A','A',?)", ('{"tidal": "tidal-xyz"}',)).lastrowid
            conn.commit()

        fake_client = MagicMock()
        worker = tw.TidalWorker(db, client=fake_client)
        worker._process_artist(artist_id, "A")

        fake_client.search_artist.assert_not_called()
        with db._get_connection() as conn:
            state = attempt_state(conn, entity_type="artist", entity_id=artist_id)
        assert state["tidal"]["status"] == "matched"

class TestQobuzWorkerMarksMatched:
    def test_existing_qobuz_id_triggers_matched_status(self, db):
        """Finding an existing id must record the attempt, or the worker re-selects
        that row on every loop. The status lives in the provider-attempt ledger now
        instead of ``artists.qobuz_match_status``."""
        from core import qobuz_worker as qw
        from core.library2.provider_attempts import attempt_state

        with db._get_connection() as conn:
            artist_id = conn.execute(
                "INSERT INTO lib2_artists(name, sort_name, external_ids) "
                "VALUES('A','A',?)", ('{"qobuz": "qobuz-xyz"}',)).lastrowid
            conn.commit()

        fake_client = MagicMock()
        worker = qw.QobuzWorker(db, client=fake_client)
        worker._process_artist(artist_id, "A")

        fake_client.search_artist.assert_not_called()
        with db._get_connection() as conn:
            state = attempt_state(conn, entity_type="artist", entity_id=artist_id)
        assert state["qobuz"]["status"] == "matched"

class TestMusicBrainzWorkerMarksMatched:
    def test_existing_mbid_triggers_matched_status_via_service(self, db):
        from core import musicbrainz_worker as mbw

        with db._get_connection() as conn:
            cur = conn.cursor()
            artist_id = cur.execute(
                "INSERT INTO lib2_artists(name, sort_name, musicbrainz_id) "
                "VALUES('A','A','mb-uuid')").lastrowid
            conn.commit()

        fake_service = MagicMock()
        with patch.object(mbw, "MusicBrainzService", return_value=fake_service):
            worker = mbw.MusicBrainzWorker(db)
            # mb_service on the instance is the MagicMock
            worker._process_item({"type": "artist", "id": artist_id, "name": "A"})

        fake_service.update_artist_mbid.assert_called_once_with(
            artist_id, "mb-uuid", "matched"
        )
