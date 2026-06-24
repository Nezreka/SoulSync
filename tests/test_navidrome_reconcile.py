"""Navidrome reconcile must read the CURRENT playlist via ratingKey (#905).

NavidromeTrack exposes the Subsonic song id as ``ratingKey`` — it has no ``.id``.
reconcile_playlist used ``t.id``, so the current-track list came back empty, the
add/remove plan saw "nothing is here", and every sync re-added the whole matched
set (playlists doubling). These tests drive the real reconcile_playlist with a
stubbed client and assert the Subsonic params reflect the true delta.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.navidrome_client import NavidromeClient, NavidromeTrack


def _track(song_id):
    """A NavidromeTrack as get_playlist_tracks / the matcher produce them."""
    return NavidromeTrack({'id': song_id, 'title': f'Song {song_id}'}, client=None)


def test_navidrome_track_exposes_ratingkey_not_id():
    # Root cause: the attribute is ratingKey, NOT id.
    t = _track('42')
    assert t.ratingKey == '42'
    assert not hasattr(t, 'id')


def _client_with_existing(existing_ids):
    """A NavidromeClient whose server playlist already holds `existing_ids`,
    capturing the Subsonic params reconcile sends."""
    c = NavidromeClient.__new__(NavidromeClient)
    c.ensure_connection = lambda: True
    c.get_playlists_by_name = lambda name: [SimpleNamespace(id='PL1')]
    c.get_playlist_tracks = lambda pid: [_track(i) for i in existing_ids]
    captured = {}

    def _req(method, params):
        captured['method'] = method
        captured['params'] = params
        return {'status': 'ok'}

    c._make_request = _req
    return c, captured


def test_no_change_resync_is_a_noop():
    # Playlist already == desired → reconcile must add/remove NOTHING.
    c, captured = _client_with_existing(['1', '2', '3', '4'])
    desired = [_track('1'), _track('2'), _track('3'), _track('4')]
    assert c.reconcile_playlist('My Playlist', desired) is True
    # plan empty → early return, updatePlaylist never called (no re-add).
    assert 'params' not in captured


def test_removed_source_track_is_removed_not_everything_readded():
    # warl0ck's exact case: server has 1..5, source now has 1..4 (5 removed).
    c, captured = _client_with_existing(['1', '2', '3', '4', '5'])
    desired = [_track('1'), _track('2'), _track('3'), _track('4')]
    assert c.reconcile_playlist('My Playlist', desired) is True
    params = captured['params']
    assert params['playlistId'] == 'PL1'
    assert 'songIdToAdd' not in params           # the bug: would re-add 1..4 → doubling
    assert params['songIndexToRemove'] == [4]    # only song '5' (index 4) is removed


def test_added_source_track_is_appended_once():
    # Server has 1..3, source now has 1..4 → add only '4'.
    c, captured = _client_with_existing(['1', '2', '3'])
    desired = [_track('1'), _track('2'), _track('3'), _track('4')]
    assert c.reconcile_playlist('My Playlist', desired) is True
    params = captured['params']
    assert params['songIdToAdd'] == ['4']
    assert 'songIndexToRemove' not in params
