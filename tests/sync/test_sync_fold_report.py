"""When two source entries land on one library track, the sync says so.

nanomite (Sep 17 2026): 1582 Deezer favourites, "1581 of 1582 synced", and a
1288-track Navidrome playlist. The ratingKey dedupe folded 294 favourites into
tracks already on the playlist and nothing reported it: the log had a count,
the review listed every entry as found, the card said 1581. These pin that a
fold is counted, named in the log, and marked on the entry that got folded.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from services.sync_service import PlaylistSyncService, _fold_matches_by_rating_key


def _lib(rk, title, artist="Artist"):
    return SimpleNamespace(ratingKey=rk, title=title, artist=artist, album="Album")


def _src(sid, name):
    return SimpleNamespace(id=sid, name=name, artists=["Artist"], album="Album", duration_ms=180000)


def _service(matches):
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    service.syncing_playlists = set()
    service.progress_callbacks = {}
    service.clear_progress_callback = Mock()
    service._find_track_in_media_server = AsyncMock(side_effect=matches)
    client = Mock()
    client.is_connected.return_value = True
    client.update_playlist.return_value = True
    service._get_active_media_client = lambda: (client, "navidrome")
    return service, client


def test_fold_helper_pairs_each_dropped_entry_with_its_keeper():
    a = SimpleNamespace(spotify_track=_src("1", "Song"), plex_track=_lib("k1", "Song"))
    b = SimpleNamespace(spotify_track=_src("2", "Song (Live)"), plex_track=_lib("k1", "Song"))
    c = SimpleNamespace(spotify_track=_src("3", "Other"), plex_track=_lib("k2", "Other"))
    kept, folds = _fold_matches_by_rating_key([a, b, c])
    assert kept == [a, c]
    assert folds == [(b, a)]


def test_a_sync_reports_the_fold_in_the_result_the_progress_and_the_log(caplog):
    # three favourites, two of them resolve to the same library file
    file_a = _lib("k1", "Song")
    matches = [(file_a, 1.0), (file_a, 0.91), (_lib("k2", "Other"), 1.0)]
    service, client = _service(matches)
    final = {}

    def _progress(p):
        final.clear()
        final.update(p.__dict__)
    service.progress_callbacks = {"Favourites": _progress}
    playlist = SimpleNamespace(id="pl", name="Favourites",
                               tracks=[_src("1", "Song"), _src("2", "Song (Live)"), _src("3", "Other")])

    with caplog.at_level(logging.INFO, logger="soulsync.sync_service"):
        result = asyncio.run(service.sync_playlist(playlist))

    # the playlist got the two unique files, the numbers add up
    (_name, pushed), = [c.args for c in client.update_playlist.call_args_list]
    assert [t.ratingKey for t in pushed] == ["k1", "k2"]
    assert result.matched_tracks == 3
    assert result.synced_tracks == 2
    assert result.duplicate_tracks == 1
    assert result.synced_tracks + result.duplicate_tracks == result.matched_tracks
    assert final["current_step"] == "Sync completed"
    assert final["duplicate_tracks"] == 1

    # the folded entry says which entry already holds its file
    by_id = {d["source_track_id"]: d for d in result.match_details}
    assert by_id["2"]["status"] == "found"
    assert by_id["2"]["folded_into"] == "Artist - Song"
    assert "folded_into" not in by_id["1"]
    assert "folded_into" not in by_id["3"]

    # and the log names the pair, with the library track it landed on
    line = [r.getMessage() for r in caplog.records if "[Sync fold]" in r.getMessage()]
    assert len(line) == 1
    assert "'Artist - Song (Live)' -> library #k1 'Song' by 'Artist'" in line[0]
    assert "already on the playlist via 'Artist - Song'" in line[0]


def test_no_folds_means_zero_and_no_marks():
    matches = [(_lib("k1", "Song"), 1.0), (_lib("k2", "Other"), 1.0)]
    service, _client = _service(matches)
    playlist = SimpleNamespace(id="pl", name="Favourites", tracks=[_src("1", "Song"), _src("2", "Other")])
    result = asyncio.run(service.sync_playlist(playlist))
    assert result.duplicate_tracks == 0
    assert all("folded_into" not in d for d in result.match_details)
