"""music requests (sept 24 review): a profile without download rights asks,
an admin approves or declines, the requester hears about it.

before this the scheduled wishlist run downloaded every profile's list, so
"can't download" only removed a button. now their rows wait for an admin.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-musicreq-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'musicreq.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

from core.requests.music import fulfillment_status, group_key, group_rows  # noqa: E402


def _row(tid, album_id='alb1', album='In Rainbows', artist='Radiohead', album_type='album',
         source_type='album', name=None, added='2026-09-24 10:00:00'):
    return {'spotify_track_id': tid, 'source_type': source_type, 'date_added': added,
            'spotify_data': {'id': tid, 'name': name or f'song {tid}',
                             'artists': [{'name': artist}],
                             'album': {'id': album_id, 'name': album, 'album_type': album_type,
                                       'images': [{'url': 'http://img/x.jpg'}]}}}


# ── pure grouping + fulfillment ──────────────────────────────────────────────

def test_tracks_of_one_album_are_one_request():
    groups = group_rows([_row('t1'), _row('t2'), _row('s1', album_id='single1', album='Lone',
                                                    album_type='single', source_type='manual')],
                        profile_id=5, requester_name='thomas')
    kinds = sorted((g['kind'], g['track_count']) for g in groups)
    assert kinds == [('album', 2), ('track', 1)]
    album = next(g for g in groups if g['kind'] == 'album')
    assert album['title'] == 'In Rainbows' and album['artist'] == 'Radiohead'
    assert album['image_url'] == 'http://img/x.jpg' and album['requester_name'] == 'thomas'


def test_a_single_from_an_album_wishlisted_alone_is_a_track_request():
    assert group_key(_row('t9', source_type='manual', album_type='single')).startswith('track:')


def test_fulfillment_waits_while_anything_is_still_wishlisted():
    tracks = [{'id': 'a', 'title': 'A', 'artist': 'X'}, {'id': 'b', 'title': 'B', 'artist': 'X'}]
    assert fulfillment_status(tracks, lambda t: t == 'b', lambda ti, ar: True) is None
    assert fulfillment_status(tracks, lambda t: False, lambda ti, ar: ti == 'A') == 'available'
    assert fulfillment_status(tracks, lambda t: False, lambda ti, ar: False) == 'removed'


# ── the routes, for real ─────────────────────────────────────────────────────

web_server = pytest.importorskip('web_server')


def _client_as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


@pytest.fixture
def notes(monkeypatch):
    import core.profile_notify as pn
    got = []
    monkeypatch.setattr(pn, '_journal', lambda pid, kind, msg: got.append((pid, kind, msg)))
    monkeypatch.setattr(pn, '_emitter', None)
    return got


@pytest.fixture
def asker():
    """a profile that can't download, with an album and a single wishlisted."""
    db = web_server.get_database()
    pid = db.create_profile(name=f'asker_{uuid4().hex[:8]}', can_download=False)
    tag = uuid4().hex[:6]
    for r in (_row(f'{tag}a', album_id=f'alb{tag}'), _row(f'{tag}b', album_id=f'alb{tag}'),
              _row(f'{tag}s', album_id=f'sgl{tag}', album='Lone', album_type='single',
                   source_type='manual', name='Lone')):
        db.add_to_wishlist(spotify_track_data=r['spotify_data'], source_type=r['source_type'],
                           profile_id=pid, user_initiated=True)
    yield pid, tag
    db.clear_wishlist(profile_id=pid)
    db.delete_profile(pid)


def _pending_for(client, pid):
    body = client.get('/api/requests/music?status=pending').get_json()
    return [g for g in body['pending'] if g['profile_id'] == pid]


def test_admin_sees_the_waiting_requests_grouped(asker):
    pid, tag = asker
    groups = _pending_for(_client_as(1), pid)
    assert sorted((g['kind'], g['track_count']) for g in groups) == [('album', 2), ('track', 1)]


def test_member_sees_only_their_own(asker):
    pid, tag = asker
    other = web_server.get_database().create_profile(name=f'other_{uuid4().hex[:8]}', can_download=False)
    try:
        body = _client_as(other).get('/api/requests/music?status=pending').get_json()
        assert body['pending'] == [] and body['asks_first'] is True
        assert len(_client_as(pid).get('/api/requests/music?status=pending').get_json()['pending']) == 2
    finally:
        web_server.get_database().delete_profile(other)


def test_member_cannot_approve_their_own_request(asker):
    pid, tag = asker
    c = _client_as(pid)
    key = _pending_for(c, pid)[0]['key']
    assert c.post('/api/requests/music/approve', json={'profile_id': pid, 'key': key}).status_code == 403


def test_approve_makes_rows_downloadable_and_tells_the_requester(asker, notes):
    pid, tag = asker
    db = web_server.get_database()
    admin = _client_as(1)
    album = next(g for g in _pending_for(admin, pid) if g['kind'] == 'album')
    r = admin.post('/api/requests/music/approve', json={'profile_id': pid, 'key': album['key']})
    assert r.get_json()['approved'] == 2
    approved = {t['spotify_track_id'] for t in db.get_wishlist_tracks(profile_id=pid, approved_only=True)}
    assert approved == {f'{tag}a', f'{tag}b'}
    assert [g['kind'] for g in _pending_for(admin, pid)] == ['track']
    assert any(p == pid and 'approved' in m for p, k, m in notes)
    # approving again is a clean refusal, not a second history row
    again = admin.post('/api/requests/music/approve', json={'profile_id': pid, 'key': album['key']})
    assert again.status_code in (404, 409)
    assert len(db.list_music_requests(profile_id=pid, status='approved')) == 1


def test_decline_removes_and_ignore_lists_the_rows(asker, notes):
    pid, tag = asker
    db = web_server.get_database()
    admin = _client_as(1)
    single = next(g for g in _pending_for(admin, pid) if g['kind'] == 'track')
    r = admin.post('/api/requests/music/decline',
                   json={'profile_id': pid, 'key': single['key'], 'response': 'not on this server'})
    assert r.get_json()['success']
    assert not db.wishlist_has_track(pid, f'{tag}s')
    assert db.is_track_ignored(f'{tag}s', profile_id=pid)
    hist = db.list_music_requests(profile_id=pid, status='declined')
    assert hist and hist[0]['admin_response'] == 'not on this server'
    assert any(p == pid and 'not on this server' in m for p, k, m in notes)


def test_member_can_withdraw_a_waiting_request(asker):
    pid, tag = asker
    c = _client_as(pid)
    key = next(g for g in _pending_for(c, pid) if g['kind'] == 'track')['key']
    assert c.post('/api/requests/music/withdraw', json={'key': key}).get_json()['success']
    assert not web_server.get_database().wishlist_has_track(pid, f'{tag}s')


def test_arrival_flips_the_request_to_available_and_says_so(asker, notes, monkeypatch):
    pid, tag = asker
    db = web_server.get_database()
    admin = _client_as(1)
    album = next(g for g in _pending_for(admin, pid) if g['kind'] == 'album')
    admin.post('/api/requests/music/approve', json={'profile_id': pid, 'key': album['key']})
    # the download lands: the rows leave the wishlist and the library has them
    db.remove_from_wishlist(f'{tag}a', profile_id=pid)
    db.remove_from_wishlist(f'{tag}b', profile_id=pid)
    monkeypatch.setattr(type(db), 'check_track_exists', lambda self, *a, **k: (object(), 1.0))
    counts = _client_as(pid).get('/api/requests/music/counts').get_json()
    assert counts['updates'] >= 1
    assert db.list_music_requests(profile_id=pid, status='available')
    assert any(p == pid and 'in your library' in m for p, k, m in notes)
    _client_as(pid).post('/api/requests/music/seen')
    assert _client_as(pid).get('/api/requests/music/counts').get_json()['updates'] == 0
