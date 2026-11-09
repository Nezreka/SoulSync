"""review 5 W2-WJ-5: the public api's lookup by external id reads through an
index for every provider it accepts.

tracks by genius or amazon, albums by audiodb, discogs, amazon or jiosaavn,
and bandcamp's two-part OR had no index and read every row: about 1.9 s a
call on a 316k-track library. 3.6.1 had a column index for each."""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase
from tests.library2.test_guard_query_plans import _big_scans

LOOKUPS = {
    "artists": ("spotify", "musicbrainz", "itunes", "deezer", "audiodb", "tidal", "qobuz",
                "genius", "discogs", "amazon", "jiosaavn"),
    "albums": ("spotify", "musicbrainz", "itunes", "deezer", "audiodb", "tidal", "qobuz",
               "discogs", "amazon", "jiosaavn", "bandcamp"),
    "tracks": ("spotify", "musicbrainz", "itunes", "deezer", "audiodb", "tidal", "qobuz",
               "genius", "amazon", "jiosaavn", "bandcamp"),
}


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.mark.parametrize("table,provider", [
    (table, provider) for table, providers in LOOKUPS.items() for provider in providers])
def test_the_lookup_reads_through_an_index(db, monkeypatch, table, provider):
    statements: list = []
    real_get = type(db)._get_connection

    def traced(self, *a, **k):
        conn = real_get(self, *a, **k)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(type(db), "_get_connection", traced)
    db.api_lookup_by_external_id(table, provider, "some-id")
    monkeypatch.setattr(type(db), "_get_connection", real_get)

    lookups = [s for s in statements if s.lstrip().upper().startswith("SELECT")
               and f"lib2_{table} e" in s]
    assert lookups, statements
    with db._get_connection() as conn:
        assert _big_scans(conn, lookups[0]) == [], lookups[0]


def test_a_bandcamp_album_is_found_by_either_id(db):
    with db._get_connection() as conn:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name, name_key) VALUES('A', 'a')").lastrowid
        first = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, origin, enrichment) "
            "VALUES(?, 'One', 'library', '{\"bandcamp\": {\"id\": 4242}}')", (artist,)).lastrowid
        second = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, origin, external_ids) "
            "VALUES(?, 'Two', 'library', '{\"bandcamp\": \"https://a.bandcamp.com/album/two\"}')",
            (artist,)).lastrowid
        for album in (first, second):
            track = conn.execute(
                "INSERT INTO lib2_tracks(album_id, title) VALUES(?, 't')", (album,)).lastrowid
            conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, ?)",
                         (track, f"/music/{album}.flac"))
        conn.commit()
    by_id = db.api_lookup_by_external_id("albums", "bandcamp", "4242")
    by_url = db.api_lookup_by_external_id("albums", "bandcamp", "https://a.bandcamp.com/album/two")
    assert by_id and str(by_id["id"]) == str(first)
    assert by_url and str(by_url["id"]) == str(second)


def test_an_album_musicbrainz_id_matches_whatever_its_case(db):
    with db._get_connection() as conn:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name, name_key) VALUES('A', 'a')").lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, musicbrainz_id) "
            "VALUES(?, 'One', 'f6b47f3a-53a9-4d09-9c87-18a2b63d8b48')", (artist,)).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title) VALUES(?, 't')", (album,)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, '/m/1.flac')", (track,))
        conn.commit()
    got = db.api_lookup_by_external_id("albums", "musicbrainz", "F6B47F3A-53A9-4D09-9C87-18A2B63D8B48")
    assert got and str(got["id"]) == str(album)
