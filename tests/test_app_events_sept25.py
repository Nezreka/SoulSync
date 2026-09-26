"""issues and music requests fire automation triggers (sept 25 2026).

they had none, so nothing could send "your issue was fixed" to discord or
react to a music request. every event type published must exist as a
trigger block, or it fires into the void.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import app_events
from core.automation.blocks import TRIGGERS, block_category


@pytest.fixture
def events():
    got = []
    app_events._reset_for_tests()
    app_events.register_forwarder(lambda t, d: got.append((t, d)))
    yield got
    app_events._reset_for_tests()


def _side():
    from core.issues.service import IssueSide
    rows = {}

    class Store:
        def find_open_duplicate(self, *a): return None
        def follower_ids(self, iid): return []
        def followers(self, iid): return []
        def add_comment(self, *a, **k): return 1
        def set_unread(self, *a): pass
        def list_comments(self, iid): return []

    def create(pid, et, eid, cat, title, desc, snap, prio, name):
        rows[1] = {'id': 1, 'profile_id': pid, 'category': cat, 'title': title, 'status': 'open'}
        return 1
    return IssueSide(get=lambda i: dict(rows[i]) if i in rows else None, create=create, update=lambda i, u: rows[i].update(u) or True,
                     store=Store(), notify=lambda *a: None, categories=('wrong_track',), side='video')


def test_issue_lifecycle_publishes_its_events(events):
    from core.issues import service
    side = _side()
    service.report(side, actor=5, actor_name='kim', is_admin=False, entity_type='movie',
                   entity_id='3', category='wrong_track', body={'title': 't'}, snapshot=dict)
    service.triage(side, actor=1, actor_name='boulder', is_admin=True, issue_id=1,
                   body={'status': 'resolved'})
    service.comment(side, actor=1, actor_name='boulder', is_admin=True, issue_id=1, body={'body': 'done'})
    kinds = [t for t, d in events]
    assert kinds == ['issue_created', 'issue_status_changed', 'issue_commented']
    assert all(d['side'] == 'video' for t, d in events)
    assert events[1][1]['status'] == 'resolved'


def test_every_new_event_has_a_trigger_block_in_a_real_drawer():
    types = {b['type'] for b in TRIGGERS}
    for t in ('issue_created', 'issue_status_changed', 'issue_commented', 'music_request_approved',
              'music_request_declined', 'music_request_available', 'video_request_denied',
              'video_request_available'):
        assert t in types, t
        assert block_category(t) != 'Other', t


def test_video_arrival_publishes_once_per_title(monkeypatch):
    import core.video.download_events as de
    from core.requests.video import sweep_arrivals
    got = []
    de._reset_for_tests()
    de.register_event_forwarder(lambda t, d: got.append((t, d)))
    rows = [{'id': 1, 'kind': 'movie', 'tmdb_id': 9, 'title': 'Dune', 'profile_id': 2, 'requester_name': 'kim'},
            {'id': 2, 'kind': 'movie', 'tmdb_id': 9, 'title': 'Dune', 'profile_id': 3, 'requester_name': 'tom'}]
    db = SimpleNamespace(approved_requests_awaiting_arrival=lambda: [dict(r) for r in rows],
                         annotate_requests_in_library=lambda rs: [r.update(in_library=True) for r in rs],
                         mark_video_requests_available=lambda ids: len(ids))
    try:
        assert sweep_arrivals(db, lambda *a: None) == 2
    finally:
        de._reset_for_tests()
    arrivals = [d for t, d in got if t == 'video_request_available']
    assert len(arrivals) == 1 and arrivals[0]['requester'] == 'kim, tom'
