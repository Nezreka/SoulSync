"""the second follow-up list (sept 25 2026): turning a profile off, signing
one device out, bulk issue triage, kids limits on soulseek streams and deep
artist payloads, and video request progress + quality profile."""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-followb-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'followb.db'))
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

from core.security.devices import device_is_live, device_label  # noqa: E402


def test_device_labels_read_like_a_person_would_say_them():
    assert device_label('Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537') == 'Chrome on Windows'
    assert device_label('Mozilla/5.0 (iPhone; CPU iPhone OS 17) Safari/604') == 'Safari on iOS'
    assert device_label('') == 'Browser'


def test_a_device_read_error_never_signs_anyone_out():
    from core.security import devices
    devices._reset_for_tests()
    assert device_is_live(None, lambda d: {'revoked_at': 'x'}) is True
    assert device_is_live('a', lambda d: 1 / 0) is True
    assert device_is_live('b', lambda d: {'revoked_at': '2026-09-25'}) is False
    assert device_is_live('c', lambda d: None) is True


def test_deep_clean_drops_explicit_cards_at_any_depth():
    from api.content_guard import _DEEP, _deep_clean
    data = {'albums': [{'name': 'a', 'explicit': True},
                       {'name': 'b', 'tracks': [{'name': 't1', 'explicit': 1}, {'name': 't2'}]}]}
    out = _deep_clean(data)
    assert [a['name'] for a in out['albums']] == ['b']
    assert [t['name'] for t in out['albums'][0]['tracks']] == ['t2']
    for p in ('/api/enhanced-search/by-id', '/api/artist-detail/5', '/api/artist/5/discography'):
        assert _DEEP.match(p), p


web_server = pytest.importorskip('web_server')


@pytest.fixture(autouse=True)
def _fresh_caches():
    from core.security import devices, session_epoch
    devices._reset_for_tests()
    session_epoch._reset_for_tests()
    yield


def _as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


def _signed_in(pid):
    """a browser that picked the card itself (so it gets a device row)."""
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    assert c.post('/api/profiles/select', json={'profile_id': pid},
                  headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0) Chrome/120'}).status_code == 200
    return c


@pytest.fixture
def member():
    db = web_server.get_database()
    pid = db.create_profile(name=f'm_{uuid4().hex[:8]}')
    yield pid
    db.delete_profile(pid)


def test_a_turned_off_profile_can_not_be_used(member):
    """there was no way to stop someone using a profile short of deleting
    everything they had."""
    c = _signed_in(member)
    admin = _as(1)
    assert admin.put(f'/api/profiles/{member}', json={'disabled': True}).get_json()['success']
    r = c.get('/api/profiles/me/connections')
    assert r.status_code == 401
    r = _as(None).post('/api/profiles/select', json={'profile_id': member})
    assert r.status_code == 403 and r.get_json()['disabled'] is True
    assert admin.put('/api/profiles/1', json={'disabled': True}).status_code == 400
    assert admin.put(f'/api/profiles/{member}', json={'disabled': False}).get_json()['success']
    assert _signed_in(member).get('/api/profiles/me/connections').status_code == 200
    actions = [e['action'] for e in admin.get('/api/profiles/audit').get_json()['entries']
               if e['target_id'] == member]
    assert 'profile_disabled' in actions and 'profile_enabled' in actions


def test_one_device_can_be_signed_out_alone(member):
    """sign out everywhere was the only option: losing a phone meant
    signing out every browser in the house."""
    laptop = _signed_in(member)
    phone = _signed_in(member)
    devices = laptop.get(f'/api/profiles/{member}/devices').get_json()['devices']
    assert len(devices) == 2 and sum(1 for d in devices if d['current']) == 1
    phone_id = next(d['id'] for d in devices if not d['current'])
    assert laptop.delete(f'/api/profiles/{member}/devices/{phone_id}').get_json()['success']
    assert phone.get('/api/profiles/me/connections').status_code == 401
    assert laptop.get('/api/profiles/me/connections').status_code == 200


def test_devices_are_private(member):
    db = web_server.get_database()
    other = db.create_profile(name=f'o_{uuid4().hex[:8]}')
    try:
        assert _as(other).get(f'/api/profiles/{member}/devices').status_code == 403
        assert _as(1).get(f'/api/profiles/{member}/devices').status_code == 200
    finally:
        db.delete_profile(other)


def test_logging_out_takes_the_device_off_the_list(member):
    c = _signed_in(member)
    assert len(_as(1).get(f'/api/profiles/{member}/devices').get_json()['devices']) == 1
    c.post('/api/profiles/logout')
    assert _as(1).get(f'/api/profiles/{member}/devices').get_json()['devices'] == []


def test_bulk_issue_triage(member):
    db = web_server.get_database()
    ids = [db.create_issue(profile_id=member, entity_type='album', entity_id=str(uuid4()),
                           category='other', title=f't{i}')['id'] for i in range(3)]
    assert _as(member).post('/api/issues/bulk', json={'ids': ids, 'status': 'resolved'}).status_code == 403
    r = _as(1).post('/api/issues/bulk', json={'ids': ids[:2], 'status': 'resolved'}).get_json()
    assert r['done'] == 2
    assert [db.get_issue(i)['status'] for i in ids] == ['resolved', 'resolved', 'open']
    assert _as(1).post('/api/issues/bulk', json={'ids': ids, 'status': 'closed'}).get_json()['failed'] == 3
    r = _as(1).post('/api/issues/bulk', json={'ids': ids, 'delete': True}).get_json()
    assert r['done'] == 3 and all(db.get_issue(i) is None for i in ids)


def test_kid_cannot_stream_soulseek_results(member):
    db = web_server.get_database()
    db.update_profile(member, hide_explicit=1)
    r = _as(member).post('/api/stream/start', json={'username': 'x', 'filename': 'y.flac'})
    assert r.status_code == 403 and r.get_json()['restricted'] is True
    assert _as(member).get('/stream/audio').status_code == 403
    db.update_profile(member, hide_explicit=0)
    assert _as(member).post('/api/stream/start', json={}).status_code != 403
