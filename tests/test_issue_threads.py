"""issue threads (sept 24 2026 review): an issue held one admin_response the
admin overwrote, the reporter never heard anything, and two people hitting
the same broken album filed two issues. pins the new behaviour on BOTH sides
through the shared core, plus the music routes for real.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-threads-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'threads.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

from core.issues.activity import fix_action, recipients, timeline_events  # noqa: E402


def test_triage_changes_read_as_sentences():
    before = {'status': 'open', 'priority': 'normal'}
    assert timeline_events(before, {'status': 'resolved'}) == ['marked this fixed']
    assert timeline_events(before, {'status': 'open'}) == []
    assert timeline_events(before, {'priority': 'high'}) == ['set the priority to high']


def test_the_actor_is_never_told_about_their_own_change():
    assert recipients({'profile_id': 5}, [6, 5, 7], actor_id=6) == [5, 7]
    assert recipients({'profile_id': 1}, [], actor_id=1) == []


def test_every_category_with_a_tool_points_at_it():
    assert fix_action('wrong_track')['id'] == 'reidentify'
    assert fix_action('wrong_match')['id'] == 'fix_match'
    assert fix_action('other') is None


web_server = pytest.importorskip('web_server')


def _as(pid, name=None):
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
def two_members():
    db = web_server.get_database()
    a = db.create_profile(name=f'kim_{uuid4().hex[:6]}')
    b = db.create_profile(name=f'tom_{uuid4().hex[:6]}')
    yield a, b
    db.delete_profile(a)
    db.delete_profile(b)


def _report(c, entity_id, category='wrong_track', title='plays a live version'):
    return c.post('/api/issues', json={'entity_type': 'track', 'entity_id': entity_id,
                                       'category': category, 'title': title})


def test_second_reporter_follows_instead_of_filing_a_duplicate(two_members):
    a, b = two_members
    eid = uuid4().hex[:8]
    first = _report(_as(a), eid).get_json()
    second = _report(_as(b), eid, title='crowd noise at the start').get_json()
    assert second['merged'] is True and second['id'] == first['id']
    # b can now see it, with their words in the thread
    body = _as(b).get(f"/api/issues/{first['id']}").get_json()['issue']
    assert any('crowd noise' in c['body'] for c in body['comments'])
    assert any(f['follower_id'] == b for f in body['followers'])
    assert any(i['id'] == first['id'] for i in _as(b).get('/api/issues').get_json()['issues'])
    # the reporter re-filing is a no-op
    assert _report(_as(a), eid).get_json()['already'] is True


def test_admin_reply_and_resolve_reach_reporter_and_followers(two_members, notes):
    a, b = two_members
    eid = uuid4().hex[:8]
    iid = _report(_as(a), eid).get_json()['id']
    _report(_as(b), eid)
    admin = _as(1)
    assert admin.post(f'/api/issues/{iid}/comments', json={'body': 'on it'}).status_code == 201
    assert {p for p, k, m in notes} == {a, b}
    notes.clear()
    assert admin.put(f'/api/issues/{iid}', json={'status': 'resolved'}).get_json()['success']
    assert {p for p, k, m in notes} == {a, b}
    assert any('marked this fixed' in m for p, k, m in notes)
    # the reporter's badge lights up, and opening the issue clears it
    assert _as(a).get('/api/issues/counts').get_json()['counts']['updates'] == 1
    thread = _as(a).get(f'/api/issues/{iid}').get_json()['issue']['comments']
    assert [c['kind'] for c in thread][-2:] == ['comment', 'event']
    assert _as(a).get('/api/issues/counts').get_json()['counts']['updates'] == 0


def test_admin_can_reply_without_changing_status(two_members, notes):
    a, _b = two_members
    iid = _report(_as(a), uuid4().hex[:8]).get_json()['id']
    _as(1).put(f'/api/issues/{iid}', json={'status': 'in_progress'})
    notes.clear()
    assert _as(1).post(f'/api/issues/{iid}/comments', json={'body': 'still looking'}).status_code == 201
    assert web_server.get_database().get_issue(iid)['status'] == 'in_progress'
    assert notes and notes[0][0] == a


def test_a_stranger_cannot_read_or_reply(two_members):
    a, b = two_members
    iid = _report(_as(a), uuid4().hex[:8]).get_json()['id']
    assert _as(b).get(f'/api/issues/{iid}').status_code == 404
    assert _as(b).post(f'/api/issues/{iid}/comments', json={'body': 'hi'}).status_code == 404


def test_open_reports_survive_their_reporter_being_deleted():
    db = web_server.get_database()
    name = f'leaving_{uuid4().hex[:6]}'
    pid = db.create_profile(name=name)
    keep = _report(_as(pid), uuid4().hex[:8]).get_json()['id']
    done = _report(_as(pid), uuid4().hex[:8], category='wrong_metadata').get_json()['id']
    _as(1).put(f'/api/issues/{done}', json={'status': 'resolved'})
    db.delete_profile(pid)
    kept = db.get_issue(keep)
    assert kept is not None and kept['profile_id'] == 1 and kept['reporter_name'] == name
    assert db.get_issue(done) is None
