import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from services.sync_service import PlaylistSyncService


@pytest.mark.parametrize("mode", ["replace", "reconcile", "append"])
@pytest.mark.parametrize("connected", [True, False])
def test_zero_matches_keeps_playlist_and_still_wishlists(monkeypatch, mode, connected):
    import core.wishlist_service as wishlist_module
    wishlist = Mock()
    wishlist.add_spotify_track_to_wishlist.return_value = True
    monkeypatch.setattr(wishlist_module, "get_wishlist_service", lambda: wishlist)
    service = PlaylistSyncService.__new__(PlaylistSyncService)
    service.syncing_playlists = set()
    service._update_progress = Mock()
    service.clear_progress_callback = Mock()
    service._find_track_in_media_server = AsyncMock(return_value=(None, 0.0))
    client = Mock()
    client.is_connected.return_value = connected
    client.update_playlist.return_value = False
    client.reconcile_playlist.return_value = False
    client.append_to_playlist.return_value = False
    service._get_active_media_client = lambda: (client, "navidrome")
    track = SimpleNamespace(id="source-id", name="Missing Song", artists=["Artist"], album="Album", duration_ms=180000)
    playlist = SimpleNamespace(id="playlist-id", name="Playlist", tracks=[track])
    result = asyncio.run(service.sync_playlist(playlist, sync_mode=mode))
    if not connected:
        assert result.errors
        wishlist.add_spotify_track_to_wishlist.assert_not_called()
        return
    assert result.wishlist_added_count == 1
    assert result.total_tracks == 1
    assert result.failed_tracks == 1
    assert result.synced_tracks == 0
    client.update_playlist.assert_not_called()
    client.reconcile_playlist.assert_not_called()
    client.append_to_playlist.assert_not_called()
