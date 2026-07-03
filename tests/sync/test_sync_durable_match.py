"""The REAL playlist sync matcher (PlaylistSyncService._find_track_in_media_server)
must honor a durable Find & Add / manual match when the volatile sync_match_cache
has been wiped by a library rescan — otherwise the manual pick is re-matched from
scratch on the next auto-sync (#895 follow-up). Jellyfin server-type avoids Plex
fetchItem mocking; the durable block is server-agnostic."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.sync_service import PlaylistSyncService


def _service():
    svc = PlaylistSyncService(spotify_client=MagicMock(), download_orchestrator=MagicMock(),
                              media_server_engine=MagicMock())
    client = MagicMock(); client.is_connected.return_value = True
    svc._get_active_media_client = lambda: (client, "jellyfin")
    svc._cancelled = False
    return svc


def _run(svc, db):
    cm = MagicMock(); cm.get_active_media_server.return_value = "jellyfin"
    track = SimpleNamespace(name="Valió la Pena - Salsa Version", artists=["Marc Anthony"], id="sp16")
    with patch("database.music_database.MusicDatabase", return_value=db), \
         patch("config.settings.config_manager", cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1):
        return asyncio.run(svc._find_track_in_media_server(track, candidate_pool={}))


def test_durable_match_used_when_volatile_cache_wiped():
    dt = SimpleNamespace(id="t99", title="Valió la Pena (Salsa Version)")
    db = MagicMock()
    db.read_sync_match_cache.return_value = None                 # rescan wiped the cache
    db.check_track_exists.return_value = (None, 0.0)             # fuzzy would FAIL
    db.find_manual_library_match_by_source_track_id.return_value = {
        "library_track_id": "t99", "library_file_path": "/m/x.flac"}
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t99" else None
    match, conf = _run(svc=_service(), db=db)
    assert conf == 1.0 and match.id == "t99"                    # manual pick honored across the rescan


def test_durable_match_self_heals_stale_library_id():
    dt = SimpleNamespace(id="newid", title="X")
    db = MagicMock()
    db.read_sync_match_cache.return_value = None
    db.check_track_exists.return_value = (None, 0.0)
    db.find_manual_library_match_by_source_track_id.return_value = {
        "library_track_id": "staleid", "library_file_path": "/m/x.flac"}
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "newid" else None  # stale id gone
    db.find_track_id_by_file_path.return_value = "newid"
    match, conf = _run(svc=_service(), db=db)
    assert conf == 1.0 and match.id == "newid"
    db.find_track_id_by_file_path.assert_called_once_with("/m/x.flac")
