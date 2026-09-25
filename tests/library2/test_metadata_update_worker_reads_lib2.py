"""The metadata-update worker reads lib2, not the legacy tables.

It is the only path that carries anything back into Plex/Jellyfin: genres from
the stored artist row, plus that row's Spotify id as a shortcut past a provider
search. It read both through ``MusicDatabase.search_artists`` /
``api_get_artist``, i.e. the legacy ``artists`` table.

Both fields live on the lib2 row, so this is a reader moving over (user
decision, 11 August 2026) — independent of the still-open question of whether a
media-server scan may create lib2 rows at all.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.schema import ensure_library_v2_schema


class _Db:
    """Stands in for ``MusicDatabase``, offering only what the worker may use."""

    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def worker(tmp_path, monkeypatch):
    path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, genres, spotify_id, external_ids) "
        "VALUES('Massive Attack','Massive Attack',?,?,?)",
        (json.dumps(["trip hop", "downtempo"]), "sp-1",
         json.dumps({"spotify": "sp-1"})),
    )
    conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Nameless','Nameless')")
    conn.commit()
    conn.close()

    import database.music_database as music_database
    monkeypatch.setattr(music_database, "MusicDatabase", lambda: _Db(path))

    from core.workers.metadata_update import WebMetadataUpdateWorker

    return WebMetadataUpdateWorker(
        artists=[], media_client=None, spotify_client=None, server_type="plex")


def test_genres_and_spotify_id_come_from_the_lib2_row(worker):
    db_artist, has_genres, spotify_id = worker._check_db_artist("Massive Attack")

    assert has_genres is True
    assert db_artist["genres"] == ["trip hop", "downtempo"]
    assert spotify_id == "sp-1"


def test_a_row_without_genres_reports_so(worker):
    db_artist, has_genres, spotify_id = worker._check_db_artist("Nameless")

    assert db_artist is not None
    assert has_genres is False
    assert spotify_id is None


def test_an_unknown_name_is_a_clean_miss(worker):
    assert worker._check_db_artist("Someone Else Entirely") == (None, False, None)


def test_a_name_too_far_off_is_not_accepted(worker):
    """The 0.85 similarity floor is what keeps the worker from pushing one
    artist's genres onto another. Porting the read must not soften it."""
    assert worker._check_db_artist("Massive Wagons")[0] is None


def test_the_worker_no_longer_calls_the_legacy_lookups(worker):
    """``search_artists``/``api_get_artist`` read the legacy ``artists`` table.
    The stand-in database offers neither, so a leftover call would raise rather
    than quietly fall back."""
    assert not hasattr(worker._db, "search_artists")
    assert not hasattr(worker._db, "api_get_artist")
