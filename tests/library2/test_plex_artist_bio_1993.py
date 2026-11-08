"""#1993: the plex metadata updater fills an empty artist summary with the bio
enrichment already stored, and never replaces text that's there."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import row_conn, RowDb as _Db

LASTFM = ('Massive Attack are a trip hop group from Bristol. '
          '<a href="https://www.last.fm/music/Massive+Attack">Read more on Last.fm</a>.')


@pytest.fixture
def worker(tmp_path, monkeypatch):
    path = str(tmp_path / "lib2.db")
    conn = row_conn(path)
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists(name, sort_name, enrichment) VALUES(?,?,?)",
                 ("Massive Attack", "Massive Attack", json.dumps({"lastfm": {"bio": LASTFM}})))
    conn.execute("INSERT INTO lib2_artists(name, sort_name, summary) VALUES(?,?,?)",
                 ("Nameless", "Nameless", "-updatedAt2026-04-13"))
    conn.commit()
    conn.close()
    import database.music_database as music_database
    monkeypatch.setattr(music_database, "MusicDatabase", lambda: _Db(path))
    from core.workers.metadata_update import WebMetadataUpdateWorker
    return WebMetadataUpdateWorker(artists=[], media_client=None, spotify_client=None, server_type="plex")


def test_the_stored_lastfm_bio_comes_back_as_plain_text(worker):
    db_artist, _, _ = worker._check_db_artist("Massive Attack")
    assert worker._db_artist_bio(db_artist) == "Massive Attack are a trip hop group from Bristol."


def test_a_summary_that_is_only_our_marker_is_no_bio(worker):
    db_artist, _, _ = worker._check_db_artist("Nameless")
    assert worker._db_artist_bio(db_artist) is None


class _Artist(SimpleNamespace):
    def edit(self, **kwargs):
        self.summary = kwargs["summary.value"]

    def reload(self):
        pass


@pytest.mark.parametrize("current,expected_start", [
    ("", "Massive Attack are"),
    ("-updatedAt2026-01-01", "Massive Attack are"),
    ("Written by me.", "Written by me."),
])
def test_plex_only_fills_an_empty_summary(monkeypatch, current, expected_start):
    from core.plex_client import PlexClient
    monkeypatch.setattr("time.sleep", lambda s: None)
    artist = _Artist(title="Massive Attack", summary=current)
    assert PlexClient.update_artist_biography(object.__new__(PlexClient), artist,
                                              biography="Massive Attack are a trip hop group.")
    assert artist.summary.startswith(expected_start)
    assert artist.summary.rstrip().split("\n")[-1].startswith("-updatedAt")
