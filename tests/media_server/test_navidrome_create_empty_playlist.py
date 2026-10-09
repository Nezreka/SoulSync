"""#1543: Navidrome client allows creating an empty playlist (new mirror)."""
from unittest.mock import Mock, patch

from core.navidrome_client import NavidromeClient


def _client():
    c = NavidromeClient.__new__(NavidromeClient)
    c.ensure_connection = Mock(return_value=True)
    c.base_url = "http://localhost:4533"
    c.username = "admin"
    return c


def test_create_playlist_empty_tracks_creates():
    """Empty track list with no playlist_id creates (not refuses) the playlist."""
    c = _client()
    # get_playlists_by_name: [] before create (new), [Mock] after (verify)
    # _request('getPlaylist'): returns the empty playlist for verification
    with patch.object(
        NavidromeClient, "_make_request",
        return_value={"status": "ok", "playlist": {"id": "srv-1", "entry": []}},
    ) as req, patch.object(
        NavidromeClient, "get_playlists_by_name",
        # 1: decorator pre-check (not found), 2: method post-create resolve,
        # 3: decorator post-write verification
        side_effect=[[], [Mock(id="srv-1")], [Mock(id="srv-1")]],
    ):
        result = c.create_playlist("My Playlist", [])
    assert result is True  # validated_playlist_write normalizes to bool
    # createPlaylist (first _make_request call) sent just a name, no songIds
    create_call = req.call_args_list[0]
    assert create_call[0][0] == "createPlaylist"
    params = create_call[0][1]
    assert params["name"] == "My Playlist"
    assert params.get("songId") in (None, [])


def test_create_playlist_empty_tracks_update_refused():
    """Emptying an EXISTING playlist via update is still refused."""
    c = _client()
    with patch.object(NavidromeClient, "_make_request") as req:
        result = c.create_playlist("My Playlist", [], playlist_id="srv-1")
    assert result is False
    req.assert_not_called()
