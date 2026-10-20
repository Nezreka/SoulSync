"""request lists: the sync itself knows one, and auto-remove only trusts ids.

a request list downloads what's missing and never builds a server playlist.
the flag used to ride along by hand through every entry point, and the one
that forgot (/api/sync/start) built a server playlist anyway. the sync now
reads it off the mirror row.

auto-remove deletes songs from the user's real spotify playlist. the sync
matcher is fuzzy, so only a library track stored under the same spotify id
counts as fulfilled.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

import services.sync_service as ss
from database.music_database import MusicDatabase
from services.sync_service import PlaylistSyncService

UPSTREAM = "37i9dQZF1DXcBWIGoYBM5M"
SP_A = "4uLU6hMCjMI75M1A2tKUQC"
SP_B = "7ouMYWpwJ422jRcDASZB7P"


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _mirror(db, *, request, sid="p1"):
    return db.mirror_playlist(
        source="spotify", source_playlist_id=sid, name="Mix",
        tracks=[{"track_name": "Song", "artist_name": "Artist", "source_track_id": SP_A}],
        profile_id=1, is_request_list=1 if request else 0, upstream_playlist_id=UPSTREAM,
    )


# ── the row decides ──────────────────────────────────────────────────────────

def test_only_a_prefixed_sync_id_of_a_request_list_resolves(mdb, monkeypatch):
    import database.music_database as mdbmod
    monkeypatch.setattr(mdbmod, "MusicDatabase", lambda *a, **k: mdb)
    req = _mirror(mdb, request=True, sid="p1_req_ab12")
    plain = _mirror(mdb, request=False, sid="p2")

    assert ss._request_list_row(f"auto_mirror_{req}")["id"] == req
    assert ss._request_list_row(f"mirrored_{req}")["id"] == req
    assert ss._request_list_row(f"auto_mirror_{plain}") is None
    # bare digits are an upstream id (deezer), never a mirror pk
    assert ss._request_list_row(str(req)) is None
    assert ss._request_list_row(None) is None


def _lib(rk, title):
    return SimpleNamespace(ratingKey=rk, title=title, artist="Artist", album="Album")


def _src(sid, name):
    return SimpleNamespace(id=sid, name=name, artists=["Artist"], album="Album",
                           duration_ms=180000, source_track_id=None)


def _service():
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    service.syncing_playlists = set()
    service.progress_callbacks = {}
    service.clear_progress_callback = Mock()
    service._find_track_in_media_server = AsyncMock(
        side_effect=[(_lib("k1", "Song"), 1.0), (None, 0.0)])
    client = Mock()
    client.is_connected.return_value = True
    client._last_validated_write_skipped = []
    for write in ("append_to_playlist", "update_playlist", "reconcile_playlist", "create_playlist"):
        getattr(client, write).return_value = True
    service._get_active_media_client = lambda: (client, "navidrome")
    service._wishlist_unmatched = Mock(return_value=1)
    service.spotify_client = None
    return service, client


def _run(service, row, pid="auto_mirror_7"):
    playlist = SimpleNamespace(id=pid, name="Mix",
                               tracks=[_src(SP_A, "Song"), _src(SP_B, "Gone")])
    with patch.object(ss, "_request_list_row", lambda pid: row), \
         patch("database.music_database.MusicDatabase", Mock(return_value=Mock())):
        # no is_request_list kwarg: the caller forgot, like /api/sync/start did
        return asyncio.run(service.sync_playlist(playlist, sync_mode="append"))


def _server_writes(client):
    return sum(getattr(client, w).call_count for w in
               ("append_to_playlist", "update_playlist", "reconcile_playlist", "create_playlist"))


def test_a_request_list_never_writes_a_server_playlist_even_when_the_caller_forgets():
    service, client = _service()
    result = _run(service, {"id": 7, "is_request_list": True, "auto_remove_fulfilled": False})
    assert _server_writes(client) == 0
    assert result.matched_tracks == 1


def test_a_normal_mirror_still_writes_its_server_playlist():
    service, client = _service()
    # a plain id: an auto_mirror_ one sends the write through the server-link
    # lookup, which this mock client can't answer
    _run(service, None, pid="pl")
    assert _server_writes(client) == 1


def test_auto_remove_runs_off_the_shared_loop():
    service, _client = _service()
    seen = {}

    def _remove(row, matched, profile_id):
        seen["thread"] = threading.current_thread()

    service._handle_request_list_spotify_removal = _remove
    _run(service, {"id": 7, "is_request_list": True, "auto_remove_fulfilled": True})
    assert seen["thread"] is not threading.main_thread()


def test_the_row_turns_auto_remove_off_even_if_the_caller_says_on():
    service, _client = _service()
    service._handle_request_list_spotify_removal = Mock()
    playlist = SimpleNamespace(id="auto_mirror_7", name="Mix", tracks=[_src(SP_A, "Song")])
    service._find_track_in_media_server = AsyncMock(return_value=(_lib("k1", "Song"), 1.0))
    with patch.object(ss, "_request_list_row",
                      lambda pid: {"id": 7, "is_request_list": True, "auto_remove_fulfilled": False}), \
         patch("database.music_database.MusicDatabase", Mock(return_value=Mock())):
        asyncio.run(service.sync_playlist(playlist, sync_mode="append",
                                          is_request_list=True, auto_remove_fulfilled=True))
    service._handle_request_list_spotify_removal.assert_not_called()


# ── auto-remove trusts ids, not the fuzzy matcher ────────────────────────────

def _match(spotify_id, library_key):
    return SimpleNamespace(spotify_track=SimpleNamespace(id=spotify_id, source_track_id=None),
                           plex_track=SimpleNamespace(ratingKey=library_key))


def _remove(held_ids, matched, *, profile_id=3, row=None):
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    service.spotify_client = MagicMock(name="app account")
    profile_client = MagicMock(name="profile account")
    profile_client.remove_playlist_tracks.return_value = {"success": True}
    asked = []

    def _client_for(pid):
        asked.append(pid)
        return profile_client

    row = row or {"source": "spotify", "upstream_playlist_id": UPSTREAM, "description": ""}
    with patch.object(ss, "_library_spotify_id", lambda db, key: held_ids.get(key)), \
         patch("database.music_database.MusicDatabase", Mock(return_value=Mock())), \
         patch("core.metadata.registry.get_spotify_client_for_profile", _client_for):
        service._handle_request_list_spotify_removal(row, matched, profile_id)
    return profile_client, service.spotify_client, asked


def test_only_a_track_held_under_the_same_spotify_id_is_removed():
    # k1 holds the playlist's id; k2 is a fuzzy match to a different recording
    profile_client, app_client, _asked = _remove(
        {"k1": SP_A, "k2": "0000000000000000000000"},
        [_match(SP_A, "k1"), _match(SP_B, "k2")])
    profile_client.remove_playlist_tracks.assert_called_once_with(UPSTREAM, [SP_A])
    app_client.remove_playlist_tracks.assert_not_called()


def test_a_library_track_with_no_spotify_id_is_kept():
    profile_client, _app, _asked = _remove({}, [_match(SP_A, "k1")])
    profile_client.remove_playlist_tracks.assert_not_called()


def test_removal_uses_the_profiles_own_spotify_login():
    _pc, _app, asked = _remove({"k1": SP_A}, [_match(SP_A, "k1")], profile_id=3)
    assert asked == [3]


def test_a_non_spotify_request_list_removes_nothing():
    profile_client, _app, _asked = _remove(
        {"k1": SP_A}, [_match(SP_A, "k1")],
        row={"source": "deezer", "upstream_playlist_id": "908622995", "description": ""})
    profile_client.remove_playlist_tracks.assert_not_called()


def test_library_spotify_id_reads_the_tracks_table(mdb):
    with mdb._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Artist')")
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (1, 1, 'Album')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, spotify_track_id) "
                     "VALUES (5, 1, 1, 'Song', ?)", (SP_A,))
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title) VALUES (6, 1, 1, 'Other')")
        conn.commit()
    assert ss._library_spotify_id(mdb, 5) == SP_A
    assert ss._library_spotify_id(mdb, "5") == SP_A
    assert ss._library_spotify_id(mdb, 6) is None
    assert ss._library_spotify_id(mdb, None) is None


# ── the wishlist step is the job ─────────────────────────────────────────────

def _handler_kwargs(db, pk, config=None, *, wishlist_toggle=False):
    from core.automation.handlers.sync_playlist import auto_sync_playlist

    started, calls = threading.Event(), []

    def _run_sync_task(*a, **k):
        calls.append(k)
        started.set()

    deps = MagicMock()
    deps.get_database.return_value = db
    deps.run_sync_task = _run_sync_task
    deps.load_sync_status_file.return_value = {}
    deps.config_manager.get.side_effect = (
        lambda key, default=None: wishlist_toggle
        if key == "playlist_sync.wishlist_missing_tracks" else default)
    deps.config_manager.get_active_media_server.return_value = "navidrome"
    auto_sync_playlist({"playlist_id": str(pk), **(config or {})}, deps)
    assert started.wait(5)
    return calls[0]


def test_a_request_list_wishlists_even_with_the_global_toggle_off(mdb):
    pk = _mirror(mdb, request=True, sid="p1_req_ab12")
    assert _handler_kwargs(mdb, pk)["skip_wishlist_add"] is False


def test_a_normal_mirror_still_follows_the_global_toggle(mdb):
    pk = _mirror(mdb, request=False)
    assert _handler_kwargs(mdb, pk)["skip_wishlist_add"] is True


def test_an_explicit_skip_still_wins_on_a_request_list(mdb):
    # the post-download re-sync passes skip_wishlist_add=True so it can't loop
    pk = _mirror(mdb, request=True, sid="p1_req_ab12")
    assert _handler_kwargs(mdb, pk, {"skip_wishlist_add": True})["skip_wishlist_add"] is True
