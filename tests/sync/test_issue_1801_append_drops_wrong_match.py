"""#1801 (kevin2xk, after #1715, #1721, #1754): the wrong "Seville" never left.

the very first broken sync put "Seville" by El Puffin on the navidrome
playlist for the source track "Seville" by Sami Kaur (same title, same album
name). later syncs matched the right Sami Kaur copy, but kevin's playlist
syncs in append mode, and append never removed anything. so the playlist
held 144 matched tracks plus the El Puffin one, sync after sync.

append now drops an entry only when it is a proven wrong match: same title
as a source track, no shared artist, and the right track for that source is
on the playlist. anything a user added by hand stays.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.sync.playlist_edit import plan_append_superseded

SAMI = ("Seville", ["Sami Kaur"], "sami")


def test_the_el_puffin_seville_goes_once_sami_kaur_is_on_the_playlist():
    current = [("puffin", "Seville", ["El Puffin", "El Puffin; Brendan Mills"]),
               ("other", "Other Song", ["Someone"])]
    assert plan_append_superseded(current, [SAMI], ["sami", "other"]) == ["puffin"]


def test_nothing_goes_while_the_right_track_is_not_on_the_playlist():
    # the source track has no match yet: the wrong one is all there is,
    # and append has no business guessing
    current = [("puffin", "Seville", ["El Puffin"])]
    assert plan_append_superseded(current, [("Seville", ["Sami Kaur"], "")], []) == []
    # matched to an id this write is not putting on the playlist (dead id)
    assert plan_append_superseded(current, [SAMI], ["other"]) == []


def test_a_same_artist_version_the_user_added_stays():
    # a live cut of the same song by the same artist is a choice, not a mismatch
    current = [("live", "Seville", ["Sami Kaur"])]
    assert plan_append_superseded(current, [SAMI], ["sami"]) == []
    # featured credit still counts as the same artist
    current = [("feat", "Seville", ["Sami Kaur feat. Someone"])]
    assert plan_append_superseded(current, [SAMI], ["sami"]) == []


def test_unrelated_user_tracks_stay():
    current = [("mine", "Something Else", ["El Puffin"])]
    assert plan_append_superseded(current, [SAMI], ["sami"]) == []


def test_an_entry_with_no_artist_to_compare_stays():
    current = [("bare", "Seville", [])]
    assert plan_append_superseded(current, [SAMI], ["sami"]) == []


def test_a_wanted_track_is_never_dropped():
    # two source tracks both called Seville, by different artists: both are
    # wanted, neither is a wrong match for the other
    hints = [SAMI, ("Seville", ["El Puffin"], "puffin")]
    current = [("puffin", "Seville", ["El Puffin"]), ("sami", "Seville", ["Sami Kaur"])]
    assert plan_append_superseded(current, hints, ["sami", "puffin"]) == []


def test_titles_compare_loosely_and_each_id_comes_back_once():
    current = [("puffin", "SEVILLE", ["El Puffin"]), ("puffin", "Seville", ["El Puffin"])]
    assert plan_append_superseded(current, [SAMI], ["sami"]) == ["puffin"]


# --- the real navidrome append + its write guard -------------------------


def _navidrome(monkeypatch, playlist):
    """a NavidromeClient over a fake subsonic server holding ``playlist``
    (raw song dicts), same shape as tests/test_navidrome_identity.py"""
    import sqlite3

    import database.music_database as database
    from core.navidrome_client import NavidromeClient, NavidromeTrack

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE tracks(id TEXT, file_path TEXT, title TEXT, duration INTEGER, server_source TEXT)')
    monkeypatch.setattr(database, 'get_database', lambda: SimpleNamespace(_get_connection=lambda: conn))

    songs = {s['id']: s for s in playlist}
    songs['sami'] = {'id': 'sami', 'title': 'Seville', 'artist': 'Sami Kaur', 'path': '/sami'}
    current = [s['id'] for s in playlist]
    writes = []
    client = NavidromeClient.__new__(NavidromeClient)
    client.ensure_connection = lambda: True
    client.music_folder_id = None
    client.get_playlists_by_name = lambda name: [SimpleNamespace(id='pl')]

    def request(endpoint, params=None, timeout=None):
        if endpoint == 'getScanStatus':
            return {'scanStatus': {'scanning': False, 'count': len(songs)}}
        if endpoint == 'search3':
            return {'searchResult3': {'song': [] if params['songOffset'] else list(songs.values())}}
        if endpoint == 'getPlaylist':
            return {'playlist': {'entry': [songs[sid] for sid in current]}}
        if endpoint == 'updatePlaylist':
            writes.append(params)
            for index in params.get('songIndexToRemove', []):
                current.pop(index)
            current.extend(params.get('songIdToAdd', []))
            return {'status': 'ok'}
        raise AssertionError(endpoint)

    client._make_request = request
    client.get_playlist_tracks = lambda pid: [NavidromeTrack(songs[sid], client) for sid in current]
    return client, current, writes


PUFFIN = {'id': 'puffin', 'title': 'Seville', 'artist': 'El Puffin; Brendan Mills',
          'artists': [{'id': 'a1', 'name': 'El Puffin'}, {'id': 'a2', 'name': 'Brendan Mills'}],
          'path': '/music/El Puffin/El Puffin - Seville.mp3'}
MINE = {'id': 'mine', 'title': 'Picked By Hand', 'artist': 'Someone', 'path': '/mine'}


def test_append_takes_the_wrong_match_off_and_the_guard_accepts_it(monkeypatch):
    client, current, writes = _navidrome(monkeypatch, [PUFFIN, MINE])
    ok = client.append_to_playlist(
        'Reading Soundtrack', [SimpleNamespace(ratingKey='sami')],
        source_hints=[SAMI])
    # the guard reads the playlist back after the write: without being told
    # about the removal it would call this a failed sync
    assert ok is True
    assert current == ['mine', 'sami']
    (write,) = writes
    assert write['songIdToAdd'] == ['sami']
    assert write['songIndexToRemove'] == [0]
    assert client._last_append_removed == ['puffin']


def test_the_wrong_match_goes_even_when_nothing_is_new(monkeypatch):
    # kevin's state: sami kaur is already there, el puffin sits next to it
    client, current, writes = _navidrome(
        monkeypatch, [PUFFIN, {'id': 'sami', 'title': 'Seville', 'artist': 'Sami Kaur', 'path': '/sami'}])
    assert client.append_to_playlist('P', [SimpleNamespace(ratingKey='sami')], source_hints=[SAMI])
    assert current == ['sami']
    assert 'songIdToAdd' not in writes[0]


def test_append_without_hints_still_never_removes(monkeypatch):
    client, current, writes = _navidrome(monkeypatch, [PUFFIN, MINE])
    assert client.append_to_playlist('P', [SimpleNamespace(ratingKey='sami')])
    assert current == ['puffin', 'mine', 'sami']
    assert client._last_append_removed == []


# --- the sync service hands the hints over ---------------------------------


def test_an_append_sync_tells_navidrome_what_each_source_track_matched():
    from services.sync_service import PlaylistSyncService

    service = PlaylistSyncService.__new__(PlaylistSyncService)
    service.syncing_playlists = set()
    service.progress_callbacks = {}
    service.clear_progress_callback = Mock()
    lib = SimpleNamespace(ratingKey='sami', title='Seville', artist='Sami Kaur', album='Seville')
    service._find_track_in_media_server = AsyncMock(return_value=(lib, 1.0))
    client = Mock()
    client.is_connected.return_value = True
    client._last_validated_write_skipped = []
    client.append_to_playlist.return_value = True
    service._get_active_media_client = lambda: (client, 'navidrome')
    service._resolve_mirrored_server_playlist_id = lambda *a, **k: 'pl'
    service._wishlist_unmatched = Mock(return_value=0)
    src = SimpleNamespace(id='sp1', name='Seville', artists=[{'name': 'Sami Kaur'}],
                          album='Seville', duration_ms=137000)
    playlist = SimpleNamespace(id='pl', name='Reading Soundtrack', tracks=[src])

    with patch('database.music_database.MusicDatabase', Mock(return_value=Mock())):
        asyncio.run(service.sync_playlist(playlist, sync_mode='append'))

    (call,) = client.append_to_playlist.call_args_list
    assert call.kwargs['source_hints'] == [('Seville', ['Sami Kaur'], 'sami')]
    assert call.kwargs['playlist_id'] == 'pl'
