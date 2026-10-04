"""music video requests: a profile without download rights asks for a music
video; an admin approves (which fires the download) or declines; the
requester hears about it; a member can withdraw their own waiting ask.

mirrors tests/test_music_requests.py: the same db-env setup, the same
client helper, the same notes capture. one deliberate difference: there is
no video wishlist to derive from, so pending video requests are *stored*
rows in music_video_requests rather than grouped wishlist rows. the
contracts come from the music-video-requests scope (section 3 api, section
7 test plan).
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-musicvideoreq-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'musicvideoreq.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


def _client_as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


def _video(tag):
    return {'video_id': f'vid{tag}', 'url': f'https://youtu.be/vid{tag}',
            'title': f'Windowlicker {tag}', 'channel': 'Warp',
            'thumbnail_url': f'https://img/vid{tag}.jpg'}


@pytest.fixture
def notes(monkeypatch):
    import core.profile_notify as pn
    got = []
    monkeypatch.setattr(pn, '_journal', lambda pid, kind, msg: got.append((pid, kind, msg)))
    monkeypatch.setattr(pn, '_emitter', None)
    return got


@pytest.fixture
def asker():
    """a profile that can't download. files video requests with
    POST /api/requests/music/videos."""
    db = web_server.get_database()
    pid = db.create_profile(name=f'vasker_{uuid4().hex[:8]}', can_download=False)
    yield pid
    # withdraw anything still pending so it can't leak into the badge-count
    # assertions of later tests. teardown must never fail the test.
    try:
        member, admin = _client_as(pid), _client_as(1)
        rows = admin.get('/api/requests/music').get_json().get('pending_videos') or []
        for v in rows:
            if v.get('profile_id') == pid:
                member.post(f"/api/requests/music/videos/{v['id']}/withdraw")
    except Exception:  # noqa: BLE001
        pass
    finally:
        db.delete_profile(pid)


def _file(client, tag):
    r = client.post('/api/requests/music/videos', json=_video(tag))
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _pending_videos_for(client, pid):
    body = client.get('/api/requests/music').get_json()
    return [v for v in body.get('pending_videos', []) if v.get('profile_id') == pid]


def _video_history_for(client, pid):
    body = client.get('/api/requests/music').get_json()
    return [v for v in body.get('video_history', []) if v.get('profile_id') == pid]


def test_member_files_video_request(asker):
    pid = asker
    tag = uuid4().hex[:6]
    filed = _file(_client_as(pid), tag)
    assert filed['success'] is True and filed['id']
    # the admin sees it on the shared requests page, in the video array
    rows = _pending_videos_for(_client_as(1), pid)
    assert [v['video_id'] for v in rows] == [f'vid{tag}']
    row = rows[0]
    assert row['status'] == 'pending'
    assert row['title'] == f'Windowlicker {tag}' and row['channel'] == 'Warp'


def test_duplicate_video_request_returns_already(asker):
    pid = asker
    tag = uuid4().hex[:6]
    _file(_client_as(pid), tag)
    again = _client_as(pid).post('/api/requests/music/videos', json=_video(tag))
    assert again.status_code == 200
    body = again.get_json()
    assert body.get('success') is False and body.get('already') is True
    assert len(_pending_videos_for(_client_as(1), pid)) == 1


def test_video_request_over_quota(asker):
    pid = asker
    web_server.get_database().update_profile(pid, request_limit=1, request_limit_days=7)
    _file(_client_as(pid), uuid4().hex[:6])
    second = _client_as(pid).post('/api/requests/music/videos', json=_video(uuid4().hex[:6]))
    assert second.status_code == 429
    body = second.get_json()
    assert body.get('success') is False and 'error' in body


def test_admin_approves_video_request(asker, notes, monkeypatch):
    import api.music_requests as mr

    pid = asker
    tag = uuid4().hex[:6]
    rid = _file(_client_as(pid), tag)['id']

    calls = []

    def fake_starter(data):
        calls.append(data)
        return {'success': True, 'video_id': data.get('video_id'), 'batch_id': 'b1'}

    # the approval handler uses the starter injected via configure(), not
    # core.downloads.music_video.start directly
    monkeypatch.setattr(mr, '_start_music_video', fake_starter)

    r = _client_as(1).post(f'/api/requests/music/videos/{rid}/approve')
    assert r.status_code == 200 and r.get_json()['success'] is True

    # the download fired with the stored request's video details
    assert len(calls) == 1
    sent = calls[0]
    assert sent['video_id'] == f'vid{tag}'
    assert sent['url'] == f'https://youtu.be/vid{tag}'
    assert sent['title'] == f'Windowlicker {tag}' and sent['channel'] == 'Warp'

    approved = [v for v in _video_history_for(_client_as(1), pid) if v['id'] == rid]
    assert approved and approved[0]['status'] == 'approved'
    assert any(p == pid and 'approved' in m and f'Windowlicker {tag}' in m
               for p, k, m in notes)


def test_admin_declines_video_request(asker, notes):
    pid = asker
    tag = uuid4().hex[:6]
    rid = _file(_client_as(pid), tag)['id']
    r = _client_as(1).post(f'/api/requests/music/videos/{rid}/decline',
                           json={'response': 'not on this server'})
    assert r.status_code == 200 and r.get_json()['success'] is True
    declined = [v for v in _video_history_for(_client_as(1), pid) if v['id'] == rid]
    assert declined and declined[0]['status'] == 'declined'
    assert declined[0].get('admin_response') == 'not on this server'
    assert any(p == pid and 'declined' in m and 'not on this server' in m
               for p, k, m in notes)


def test_member_withdraws_own_video_request(asker):
    pid = asker
    tag = uuid4().hex[:6]
    rid = _file(_client_as(pid), tag)['id']
    r = _client_as(pid).post(f'/api/requests/music/videos/{rid}/withdraw')
    assert r.status_code == 200 and r.get_json()['success'] is True
    assert f'vid{tag}' not in [v['video_id'] for v in _pending_videos_for(_client_as(1), pid)]


def test_non_admin_cannot_approve_video(asker):
    pid = asker
    rid = _file(_client_as(pid), uuid4().hex[:6])['id']
    r = _client_as(pid).post(f'/api/requests/music/videos/{rid}/approve')
    assert r.status_code == 403


def test_can_download_profile_cannot_file_video_request():
    db = web_server.get_database()
    pid = db.create_profile(name=f'dler_{uuid4().hex[:8]}', can_download=True)
    try:
        r = _client_as(pid).post('/api/requests/music/videos', json=_video(uuid4().hex[:6]))
        assert r.status_code == 403
    finally:
        db.delete_profile(pid)


def test_video_request_counts_in_badge(asker):
    pid = asker
    admin, member = _client_as(1), _client_as(pid)
    before_admin = admin.get('/api/requests/music/counts').get_json()['pending']
    before_member = member.get('/api/requests/music/counts').get_json()['pending']
    _file(member, uuid4().hex[:6])
    assert admin.get('/api/requests/music/counts').get_json()['pending'] == before_admin + 1
    assert member.get('/api/requests/music/counts').get_json()['pending'] == before_member + 1
    # the shared list response carries the video count alongside the tracks
    body = admin.get('/api/requests/music').get_json()
    assert body['counts'].get('pending_videos') == 1
