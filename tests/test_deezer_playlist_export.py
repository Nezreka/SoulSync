"""Deezer playlist export via the gw-light gateway (#945) — the write half of
'sync a mirrored playlist back to Deezer'. Mocks the gateway call (the live API is the
unofficial ARL gw-light path; we test the wiring, not Deezer)."""

from core.deezer_download_client import DeezerDownloadClient


def _client(gw, authed=True):
    c = DeezerDownloadClient.__new__(DeezerDownloadClient)
    c._authenticated = authed
    c._gw_call = gw
    return c


def test_create_new_playlist():
    calls = []
    def gw(method, params):
        calls.append((method, params))
        return 12345  # gw returns the new playlist id
    res = _client(gw).create_or_update_playlist('My Mix', ['100', '200'])
    assert res['success'] and res['playlist_id'] == '12345'
    assert res['url'] == 'https://www.deezer.com/playlist/12345'
    assert res['added'] == 2
    method, params = calls[0]
    assert method == 'playlist.create'
    assert params['title'] == 'My Mix'
    assert params['songs'] == [['100', 0], ['200', 1]]


def test_update_existing_appends_no_create():
    calls = []
    def gw(method, params):
        calls.append((method, params))
        return {}
    res = _client(gw).create_or_update_playlist('My Mix', ['100'], existing_id='999')
    assert res['success'] and res['playlist_id'] == '999'
    assert calls[0][0] == 'playlist.addSongs'
    assert calls[0][1]['playlist_id'] == 999
    assert not any(m == 'playlist.create' for m, _ in calls)


def test_empty_tracks_errors_no_gw_call():
    calls = []
    res = _client(lambda *a: calls.append(a)).create_or_update_playlist('X', [])
    assert not res['success'] and 'No matching' in res['error']
    assert calls == []


def test_not_authed_errors():
    res = _client(lambda *a: None, authed=False).create_or_update_playlist('X', ['1'])
    assert not res['success'] and 'not connected' in res['error']


def test_gw_rejection_is_an_error():
    res = _client(lambda *a: None).create_or_update_playlist('X', ['1'])
    assert not res['success'] and 'rejected' in res['error']
