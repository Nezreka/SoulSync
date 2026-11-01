"""Upstream e573bd5fc on Library V2: a duplicate pair says which version a
media-server playlist points at, because removing that version's file drops
it from the playlist.

Upstream tags the retired duplicate detector's copies by library row id (its
``tracks.id`` is the server id). A catalogue track reaches the server through
``lib2_media_server_mappings``; the Manage Tracks duplicates list is where the
user picks the version to keep."""

from __future__ import annotations

import sqlite3

from core.library2.media_mappings import track_server_playlists
from core.library2.schema import ensure_library_v2_schema
from tests.library2.test_api_routes import _conn, api  # noqa: F401 - shared fixture


def _map(conn, track_id, server, server_id, *, library="", status="recognized"):
    conn.execute(
        "INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source, "
        "server_library_id, server_id, match_status) VALUES('track', ?, ?, ?, ?, ?)",
        (track_id, server, library, server_id, status))


def test_playlists_follow_the_tracks_server_mappings():
    conn = sqlite3.connect(":memory:")
    ensure_library_v2_schema(conn)
    _map(conn, 1, "plex", "rk-1")
    _map(conn, 1, "plex", "rk-9", library="2")       # a second server library
    _map(conn, 2, "jellyfin", "jf-2")                 # not the active server
    _map(conn, 3, "plex", "rk-3", status="ambiguous")  # not a recognized match
    membership = {"rk-1": ["Road Trip"], "rk-9": ["Chill", "Road Trip"],
                  "jf-2": ["Gym"], "rk-3": ["Party"], "1": ["Wrong id space"]}

    assert track_server_playlists(conn, [1, 2, 3], "plex", membership) == {
        1: ["Road Trip", "Chill"], 2: [], 3: [],
    }


def test_the_duplicates_list_names_each_versions_playlists(api, monkeypatch):  # noqa: F811
    client, db, ids = api
    conn = _conn(db)
    _map(conn, ids["album_track"], "plex", "rk-album")
    conn.commit()
    conn.close()
    import core.library.playlist_membership as membership

    monkeypatch.setattr(membership, "_active_server_and_client", lambda: ("plex", object()))
    monkeypatch.setattr(membership, "server_playlist_membership",
                        lambda **_kw: {"rk-album": ["Road Trip"]})

    pairs = client.get(f"/api/library/v2/artists/{ids['artist']}/duplicates").get_json()["pairs"]

    pair = next(p for p in pairs if p["album"]["track_id"] == ids["album_track"])
    assert pair["album"]["playlists"] == ["Road Trip"]
    assert pair["single"]["playlists"] == []


def test_without_a_media_server_the_list_still_loads(api, monkeypatch):  # noqa: F811
    client, _db, ids = api
    import core.library.playlist_membership as membership

    monkeypatch.setattr(membership, "_active_server_and_client", lambda: (None, None))

    payload = client.get(f"/api/library/v2/artists/{ids['artist']}/duplicates").get_json()

    assert payload["success"] is True
    assert all(p["album"]["playlists"] == [] for p in payload["pairs"])
