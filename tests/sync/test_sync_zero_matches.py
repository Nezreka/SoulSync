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


def _make_zero_match_service(monkeypatch, server_playlist_id=None):
    """Build a sync service with zero library matches and a mocked Navidrome client."""
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
    client.is_connected.return_value = True
    client.create_playlist.return_value = "new-server-id"
    service._get_active_media_client = lambda: (client, "navidrome")
    service._resolve_mirrored_server_playlist_id = Mock(return_value=server_playlist_id)
    return service, client


def _make_track():
    return SimpleNamespace(id="source-id", name="Missing Song", artists=["Artist"], album="Album", duration_ms=180000)


def test_zero_matches_new_mirror_creates_empty_server_playlist(monkeypatch):
    """#1543: a brand-new mirror with no server playlist gets an empty one created."""
    service, client = _make_zero_match_service(monkeypatch, server_playlist_id=None)
    playlist = SimpleNamespace(id="auto_mirror_123", name="Playlist", tracks=[_make_track()])
    result = asyncio.run(service.sync_playlist(playlist, sync_mode="reconcile"))
    assert not result.errors
    client.create_playlist.assert_called_once()
    args, _ = client.create_playlist.call_args
    assert args[0] == "Playlist"
    assert args[1] == []
    # linked so the next sync follows the server ID
    service._resolve_mirrored_server_playlist_id.assert_called()


def test_zero_matches_existing_mirror_keeps_server_playlist(monkeypatch):
    """#1543: a mirror that already has a server playlist keeps the legacy skip."""
    service, client = _make_zero_match_service(monkeypatch, server_playlist_id="existing-id")
    playlist = SimpleNamespace(id="auto_mirror_123", name="Playlist", tracks=[_make_track()])
    result = asyncio.run(service.sync_playlist(playlist, sync_mode="reconcile"))
    assert not result.errors
    client.create_playlist.assert_not_called()
