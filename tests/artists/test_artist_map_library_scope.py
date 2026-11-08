"""The genre map's library artists are the caller's library, not the catalogue.

review 5 R5-C-4: step 4 read every lib2_artists row, which also holds
discography, watchlist and catalogue-only artists and artists that live only
in another profile's own library. 3.6.1 read only the library's artists.
"""
import json

import flask
import pytest

import core.artists.map as artist_map
from core.library_scope import reset_library_scope, set_library_scope
from database.music_database import MusicDatabase
from tests import lib2_seed as seed


@pytest.fixture()
def db(tmp_path, monkeypatch):
    database = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr(artist_map, "get_database", lambda: database)
    monkeypatch.setattr(artist_map, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr("core.library_scope.any_own_library_exists", lambda: True)
    artist_map._artist_map_cache.clear()
    yield database
    artist_map._artist_map_cache.clear()


def _genre_map_names(scope):
    token = set_library_scope(scope)
    try:
        with flask.Flask(__name__).test_request_context():
            resp = artist_map.get_artist_map_genres()
    finally:
        reset_library_scope(token)
    return {n["name"] for n in json.loads(resp.get_data())["nodes"]}


def test_only_the_callers_library_artists_join_the_genre_map(db):
    rock = json.dumps(["rock"])
    with db._get_connection() as conn:
        seed.track(conn, "Owned Band", "LP", "Song")
        seed.artist(conn, "Owned Band", genres=rock)
        seed.artist(conn, "Catalogue Only", genres=rock)
        seed.track(conn, "Their Band", "LP", "Song")
        seed.artist(conn, "Their Band", genres=rock)
        conn.execute(
            "UPDATE lib2_track_files SET owner_profile_id=2 WHERE track_id IN ("
            "SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id "
            "JOIN lib2_artists ar ON ar.id=al.primary_artist_id WHERE ar.name='Their Band')")
        conn.commit()

    assert _genre_map_names("shared") == {"Owned Band"}
    # another library is another cache entry, not the shared one's answer
    assert _genre_map_names(2) == {"Their Band"}
