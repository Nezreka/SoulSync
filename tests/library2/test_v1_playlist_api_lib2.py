"""The public API's play-oriented endpoints answer from the Library-v2 catalogue.

Upstream added /library/recently-played, curated playlist tracks and mirrored
playlist tracks against the legacy ``tracks`` table (774ddcf4e, e59a69220,
c97233157). Here a play links to the catalogue by ``lib2_track_id`` and a
playlist entry resolves to a catalogue track with a live file.
"""

from __future__ import annotations

import json

import pytest

flask = pytest.importorskip("flask")

from database.music_database import MusicDatabase  # noqa: E402
from tests import lib2_seed  # noqa: E402


@pytest.fixture
def api(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "music.db"))
    import api.library as v1_library
    monkeypatch.setattr(v1_library, "require_api_key", lambda view: view)
    monkeypatch.setattr(v1_library, "get_database", lambda: db)
    bp = flask.Blueprint("v1_playlists_test", __name__)
    v1_library.register_routes(bp)
    app = flask.Flask("v1")
    app.register_blueprint(bp, url_prefix="/api/v1")
    return db, app.test_client()


def _data(response):
    body = response.get_json()
    assert body["success"], body
    return body["data"]


def test_recently_played_links_the_play_to_the_catalogue_track(api):
    db, client = api
    with db._get_connection() as conn:
        track_id = lib2_seed.track(conn, "Massive Attack", "Mezzanine", "Angel",
                                   path="/music/ma/angel.flac")
        conn.execute(
            "INSERT INTO listening_history (title, artist, album, played_at, lib2_track_id)"
            " VALUES ('Angel', 'Massive Attack', 'Mezzanine', '2026-10-04 10:00:00', ?)",
            (track_id,))
        conn.commit()

    tracks = _data(client.get("/api/v1/library/recently-played"))["tracks"]

    assert [(t["id"], t["file_path"]) for t in tracks] == [(track_id, "/music/ma/angel.flac")]


def test_curated_playlist_resolves_provider_ids_and_names_to_owned_tracks(api):
    db, client = api
    with db._get_connection() as conn:
        by_spotify = lib2_seed.track(conn, "Portishead", "Dummy", "Roads", spotify_id="sp-roads")
        by_deezer = lib2_seed.track(conn, "Portishead", "Dummy", "Sour Times",
                                    external_ids=json.dumps({"deezer": "123"}))
        by_name = lib2_seed.track(conn, "Portishead", "Dummy", "Glory Box")
        # a wanted track without a file is not playable
        lib2_seed.track(conn, "Portishead", "Third", "Machine Gun", owned=False,
                        spotify_id="sp-unowned")
        payload = [{"id": "sp-roads"}, {"id": 123}, {"track_name": "Glory Box",
                                                     "artist_name": "Portishead"},
                   {"id": "sp-unowned"}, {"id": by_name}]
        conn.execute(
            "INSERT INTO discovery_curated_playlists (playlist_type, track_ids_json, profile_id)"
            " VALUES ('test_mix', ?, 1)", (json.dumps(payload),))
        playlist_id = conn.execute("SELECT id FROM discovery_curated_playlists").fetchone()[0]
        conn.commit()

    tracks = _data(client.get(f"/api/v1/library/playlists/{playlist_id}/tracks"))["tracks"]

    # the bare number is a provider id, never a catalogue id
    assert [t["id"] for t in tracks] == [by_spotify, by_deezer, by_name]


def test_mirrored_playlist_tracks_resolve_external_id_then_name(api):
    db, client = api
    with db._get_connection() as conn:
        first = lib2_seed.track(conn, "Björk", "Homogenic", "Joga", spotify_id="sp-joga")
        second = lib2_seed.track(conn, "Björk", "Homogenic", "Bachelorette")
        conn.execute("INSERT INTO mirrored_playlists (id, source, source_playlist_id, name, profile_id)"
                     " VALUES (5, 'spotify', 'pl5', 'Mix', 1)")
        for position, (name, source_id) in enumerate((("Joga", "sp-joga"),
                                                       ("Bachelorette", "sp-missing"))):
            conn.execute(
                "INSERT INTO mirrored_playlist_tracks"
                " (playlist_id, position, track_name, artist_name, source_track_id)"
                " VALUES (5, ?, ?, 'Björk', ?)", (position, name, source_id))
        conn.commit()

    tracks = _data(client.get("/api/v1/library/mirrored-playlists/5/tracks"))["tracks"]

    assert [t["id"] for t in tracks] == [first, second]
