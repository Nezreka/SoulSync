"""Tests for SoulSync issue #1754 — follow-up to #1715/#1721 (kevin2xk).

On 3.5.7 the playlist sync reported 157/157 matched and 100% success, yet
the Navidrome playlist held only 145 entries and 13 source tracks never
arrived. The reporter's app.log shows why: the tracks he had deleted in
Review (verification) — and older ones Navidrome had already dropped —
were still matched from SoulSync's database rows, and the Navidrome
guarded write (core.library.navidrome_identity) skipped their dead
server IDs with only a log line:

    Playlist 'Reading Soundtrack': skipped 13 track(s) navidrome no
    longer has (...)

    12:51 sync: matched 114, guard skipped 9,  wishlist added the 43 unmatched
    17:11 sync: matched 157, guard skipped 13, added 40, 104 already present
                -> 144 delivered + the pre-fix wrong entry still sitting in
                the append-mode playlist = the 145 the reporter counted

The skipped tracks were counted as matched AND synced, so they were
never wishlisted and never re-downloaded — a permanent silent hole.

Contract pinned here:
  * a matched track the guarded write proves server-dead (no live
    candidate in the verified inventory) is treated as missing by the
    same sync: it leaves the matched/synced counts, joins the unmatched
    set the wishlist step consumes, and its history detail reads
    not_found with server_missing set (the dead library id stays
    recorded, per #1417). Ambiguous IDs — a rekey might explain them —
    are NOT reclassified; the guard keeps those log-only.
  * the cached pairing is dropped only when the cache row actually
    points at the dead server ID (the delete carries the predicate;
    a row pointing at a live track survives)
  * a folded pair sharing one dead ratingKey goes missing together and
    stops counting as a duplicate
  * this holds for append, replace and reconcile, and when the guard
    refuses a replace/reconcile write over the dead IDs the tracks
    still resurface on the wishlist (the write error itself stands)
  * an all-dead write (the guard raises inside resolve) still hands
    its drops over — see tests/test_navidrome_identity.py
  * two playlists syncing concurrently through one client each get
    their own write's drops (locked in-thread readback)
  * a write that drops nothing changes nothing
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services.sync_service import PlaylistSyncService


def _lib(rk, title, artist="Artist"):
    return SimpleNamespace(ratingKey=rk, title=title, artist=artist, album="Album")


def _src(sid, name):
    return SimpleNamespace(id=sid, name=name, artists=["Artist"], album="Album",
                           duration_ms=180000)


def _service(matches, *, skipped=None, write_ok=True):
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    service.syncing_playlists = set()
    service.progress_callbacks = {}
    service.clear_progress_callback = Mock()
    if callable(matches):
        service._find_track_in_media_server = AsyncMock(side_effect=matches)
    else:
        service._find_track_in_media_server = AsyncMock(side_effect=matches)
    client = Mock()
    client.is_connected.return_value = True
    client._last_validated_write_skipped = []

    def _write(name, tracks, **kwargs):
        # what the real guarded write does: succeed past the dead IDs and
        # record them on the client
        client._last_validated_write_skipped = list(skipped or [])
        return write_ok

    client.append_to_playlist.side_effect = _write
    client.update_playlist.side_effect = _write
    client.reconcile_playlist.side_effect = _write
    service._get_active_media_client = lambda: (client, "navidrome")
    service._wishlist_unmatched = Mock(return_value=0)
    return service, client


def _run(service, names=("Song", "Other", "Third"), *, mode="append",
         playlist_name="Mix"):
    playlist = SimpleNamespace(
        id="pl", name=playlist_name,
        tracks=[_src(str(i + 1), n) for i, n in enumerate(names)])
    final = {}

    def _progress(p):
        final.clear()
        final.update(p.__dict__)

    service.progress_callbacks = {playlist_name: _progress}
    with patch("database.music_database.MusicDatabase", Mock(return_value=Mock())), \
         patch("core.sync.match_overrides.drop_stale_cached_match") as drop:
        result = asyncio.run(service.sync_playlist(playlist, sync_mode=mode))
    return result, final, drop


def test_a_track_the_server_no_longer_has_counts_as_missing_and_is_wishlisted():
    matches = [(_lib("k1", "Song"), 1.0), (_lib("k2", "Other"), 1.0),
               (_lib("k3", "Third"), 1.0)]
    service, client = _service(matches, skipped=["k2"])
    result, final, drop = _run(service)

    # the write was still offered every matched track; the guard's job is
    # the live check, the sync's job is believing its answer
    (_name, pushed), = [c.args for c in client.append_to_playlist.call_args_list]
    assert [t.ratingKey for t in pushed] == ["k1", "k2", "k3"]

    assert result.matched_tracks == 2
    assert result.synced_tracks == 2
    assert result.failed_tracks == 1
    assert result.success_rate < 100.0
    assert final["matched_tracks"] == 2
    assert final["synced_tracks"] == 2
    assert final["failed_tracks"] == 1

    # the dead track went to the wishlist step as an unmatched track
    (wishlist_call,) = service._wishlist_unmatched.call_args_list
    unmatched = wishlist_call.args[1]
    assert [r.spotify_track.id for r in unmatched] == ["2"]

    # the cached pairing drop is scoped to the dead server track
    assert [(c.args[1], c.kwargs.get("server_track_id"))
            for c in drop.call_args_list] == [("2", "k2")]

    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert by_id["1"]["status"] == "found"
    assert "server_missing" not in by_id["1"]
    assert by_id["2"]["status"] == "not_found"
    assert by_id["2"]["server_missing"] is True
    assert by_id["2"]["download_status"] == "wishlist"
    # the dead library id stays on the detail so the deletion is traceable
    assert by_id["2"]["matched_track"]["id"] == "k2"
    assert by_id["3"]["status"] == "found"


def test_the_scoped_cache_delete_only_lands_on_the_dead_pairing(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / "music.db"))
    # the pairing on file points at a LIVE track under the same source id
    db.save_sync_match_cache("src-1", "song", "artist", "navidrome",
                             "k-live", "Song", 1.0)
    from core.sync.match_overrides import drop_stale_cached_match
    drop_stale_cached_match(db, "src-1", "navidrome", server_track_id="k-dead")
    assert db.read_sync_match_cache("src-1", "navidrome") is not None
    drop_stale_cached_match(db, "src-1", "navidrome", server_track_id="k-live")
    assert db.read_sync_match_cache("src-1", "navidrome") is None


def test_replace_mode_reclassifies_the_same_way():
    matches = [(_lib("k1", "Song"), 1.0), (_lib("k2", "Other"), 1.0),
               (_lib("k3", "Third"), 1.0)]
    service, client = _service(matches, skipped=["k2"])
    result, _final, _drop = _run(service, mode="replace")

    client.update_playlist.assert_called_once()
    assert result.matched_tracks == 2
    assert result.synced_tracks == 2
    assert result.failed_tracks == 1
    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert by_id["2"]["server_missing"] is True


def test_reconcile_mode_reclassifies_the_same_way():
    matches = [(_lib("k1", "Song"), 1.0), (_lib("k2", "Other"), 1.0),
               (_lib("k3", "Third"), 1.0)]
    service, client = _service(matches, skipped=["k2"])
    result, _final, _drop = _run(service, mode="reconcile")

    client.reconcile_playlist.assert_called_once()
    assert result.matched_tracks == 2
    assert result.synced_tracks == 2
    assert result.failed_tracks == 1
    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert by_id["2"]["server_missing"] is True


def test_mixed_unmatched_and_dead_tracks_all_reach_the_wishlist():
    # src2 was never in the library; src3 matched a since-deleted file
    matches = [(_lib("k1", "Song"), 1.0), (None, 0.0),
               (_lib("k3", "Third"), 1.0), (_lib("k4", "Fourth"), 1.0)]
    service, _client = _service(matches, skipped=["k3"])
    result, _final, _drop = _run(
        service, names=("Song", "Never Owned", "Third", "Fourth"))

    assert result.matched_tracks == 2
    assert result.synced_tracks == 2
    assert result.failed_tracks == 2
    (wishlist_call,) = service._wishlist_unmatched.call_args_list
    # original unmatched first, the proven-dead match appended after them
    assert [r.spotify_track.id for r in wishlist_call.args[1]] == ["2", "3"]
    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert by_id["2"]["status"] == "not_found"
    assert "server_missing" not in by_id["2"]
    assert by_id["3"]["server_missing"] is True


def test_a_write_that_drops_nothing_changes_nothing():
    matches = [(_lib("k1", "Song"), 1.0), (_lib("k2", "Other"), 1.0),
               (_lib("k3", "Third"), 1.0)]
    service, _client = _service(matches, skipped=[])
    result, _final, drop = _run(service)

    assert result.matched_tracks == 3
    assert result.synced_tracks == 3
    assert result.failed_tracks == 0
    assert result.success_rate == 100.0
    service._wishlist_unmatched.assert_not_called()
    drop.assert_not_called()
    assert all(d["status"] == "found" for d in result.match_details)


def test_a_refused_write_still_resurfaces_the_dead_track():
    # replace/reconcile shape: the guard refuses the write because a dead
    # ID might be a rekeyed song already on the playlist. The write error
    # stands — but the proven-dead track is missing all the same, so it
    # still goes to the wishlist instead of vanishing for another cycle.
    matches = [(_lib("k1", "Song"), 1.0), (_lib("k2", "Other"), 1.0),
               (_lib("k3", "Third"), 1.0)]
    service, _client = _service(matches, skipped=["k2"], write_ok=False)
    result, _final, _drop = _run(service, mode="replace")

    assert result.errors  # the write failure is reported as before
    assert result.synced_tracks == 0
    assert result.matched_tracks == 2
    assert result.failed_tracks == 1
    (wishlist_call,) = service._wishlist_unmatched.call_args_list
    assert [r.spotify_track.id for r in wishlist_call.args[1]] == ["2"]
    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert by_id["2"]["status"] == "not_found"
    assert by_id["2"]["server_missing"] is True


def test_concurrent_syncs_each_get_their_own_writes_drops():
    # two playlists, one shared client: playlist A's write is still in
    # flight (holding its drops on the client) while playlist B's sync
    # reaches its own write. Without the locked readback, B would reset
    # or replace A's record before A reads it.
    libs = {"1": _lib("kA", "Song A"), "2": _lib("kB", "Song B")}

    async def _find(track, candidate_pool=None):
        return libs[track.id], 1.0

    service, client = _service(_find)
    a_inside_write = threading.Event()
    release_a = threading.Event()
    b_attempted = threading.Event()

    def _append(name, tracks, **kwargs):
        if name == "A":
            client._last_validated_write_skipped = ["kA"]
            a_inside_write.set()
            assert release_a.wait(10)
        else:
            b_attempted.set()
            client._last_validated_write_skipped = ["kB"]
        return True

    client.append_to_playlist.side_effect = _append
    playlist_a = SimpleNamespace(id="pa", name="A", tracks=[_src("1", "Song A")])
    playlist_b = SimpleNamespace(id="pb", name="B", tracks=[_src("2", "Song B")])

    async def _both():
        task_a = asyncio.create_task(
            service.sync_playlist(playlist_a, sync_mode="append"))
        # B only starts once A is inside its write
        await asyncio.to_thread(a_inside_write.wait, 10)
        task_b = asyncio.create_task(
            service.sync_playlist(playlist_b, sync_mode="append"))
        await asyncio.sleep(0.3)  # B reaches (and must wait at) the lock
        release_a.set()
        return await asyncio.gather(task_a, task_b)

    with patch("database.music_database.MusicDatabase", Mock(return_value=Mock())), \
         patch("core.sync.match_overrides.drop_stale_cached_match"):
        result_a, result_b = asyncio.run(_both())

    assert b_attempted.is_set()
    assert result_a.matched_tracks == 0
    assert result_a.failed_tracks == 1
    assert result_b.matched_tracks == 0
    assert result_b.failed_tracks == 1
    wishlisted = sorted(
        r.spotify_track.id
        for call in service._wishlist_unmatched.call_args_list
        for r in call.args[1])
    assert wishlisted == ["1", "2"]


def test_a_folded_pair_sharing_a_dead_key_goes_missing_together():
    file_a = _lib("k1", "Song")
    matches = [(file_a, 1.0), (file_a, 0.91), (_lib("k2", "Other"), 1.0)]
    service, _client = _service(matches, skipped=["k1"])
    result, _final, _drop = _run(service, names=("Song", "Song (Live)", "Other"))

    assert result.matched_tracks == 1
    assert result.synced_tracks == 1
    assert result.failed_tracks == 2
    assert result.duplicate_tracks == 0
    (wishlist_call,) = service._wishlist_unmatched.call_args_list
    assert [r.spotify_track.id for r in wishlist_call.args[1]] == ["1", "2"]
    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert by_id["1"]["server_missing"] is True
    assert by_id["2"]["server_missing"] is True
    assert "folded_into" not in by_id["2"]
    assert by_id["3"]["status"] == "found"
