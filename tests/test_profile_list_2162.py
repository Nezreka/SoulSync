"""#2162: a member in login mode saw every profile in the sidebar's
"switch to" menu, admins included, and GET /api/profiles listed them all.

login mode: a member gets their own profile only, the server says so (not
just the menu). the household picker keeps listing every card: it shows them
before anyone opens a profile, that is what the mode is for. admins always
see everyone.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-list2162-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'list2162.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

from core.permissions import profiles_listed_for  # noqa: E402

_ROWS = [
    {'id': 1, 'name': 'Owner', 'is_admin': 1, 'recovery_question': 'pet?'},
    {'id': 2, 'name': 'Sam', 'is_admin': 0, 'recovery_question': 'car?'},
    {'id': 3, 'name': 'Kim', 'is_admin': 0},
]


# ── the rule ─────────────────────────────────────────────────────────────────

def test_login_mode_member_lists_only_themselves():
    out = profiles_listed_for(_ROWS, viewer_id=2, viewer_is_admin=False, login_mode=True)
    assert [p['id'] for p in out] == [2]
    # their own row is whole, it's theirs
    assert out[0]['recovery_question'] == 'car?'


def test_login_mode_member_id_as_a_string_still_matches():
    out = profiles_listed_for(_ROWS, viewer_id='3', viewer_is_admin=False, login_mode=True)
    assert [p['id'] for p in out] == [3]


def test_login_mode_nobody_lists_nobody():
    assert profiles_listed_for(_ROWS, viewer_id=None, viewer_is_admin=False, login_mode=True) == []
    assert profiles_listed_for(_ROWS, viewer_id='x', viewer_is_admin=False, login_mode=True) == []


def test_login_mode_admin_lists_everyone():
    out = profiles_listed_for(_ROWS, viewer_id=1, viewer_is_admin=True, login_mode=True)
    assert [p['id'] for p in out] == [1, 2, 3]
    assert out[1]['recovery_question'] == 'car?'


def test_picker_mode_lists_every_card_without_private_fields():
    out = profiles_listed_for(_ROWS, viewer_id=2, viewer_is_admin=False, login_mode=False)
    assert [p['id'] for p in out] == [1, 2, 3]
    assert 'recovery_question' not in out[0]
    assert out[1]['recovery_question'] == 'car?'


# ── the route, for real ──────────────────────────────────────────────────────

web_server = pytest.importorskip('web_server')


def _client_as(pid, **extra):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
        s.update(extra)
    return c


def _config(monkeypatch, **overrides):
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: overrides[k] if k in overrides else real_get(k, d))


@pytest.fixture
def members():
    db = web_server.get_database()
    ids = [db.create_profile(name=f'm2162_{uuid4().hex[:8]}') for _ in range(2)]
    yield ids
    for pid in ids:
        db.delete_profile(pid)


def _listed(client):
    r = client.get('/api/profiles')
    assert r.status_code == 200
    return [p['id'] for p in r.get_json()['profiles']]


def test_route_login_mode_member_sees_only_themselves(members, monkeypatch):
    _config(monkeypatch, **{'security.require_login': True})
    me, other = members
    ids = _listed(_client_as(me, login_authenticated=True))
    assert ids == [me]
    assert 1 not in ids and other not in ids


def test_route_login_mode_admin_sees_everyone(members, monkeypatch):
    _config(monkeypatch, **{'security.require_login': True})
    ids = _listed(_client_as(1, login_authenticated=True))
    assert 1 in ids and set(members) <= set(ids)


def test_route_login_mode_signed_out_gets_no_list(members, monkeypatch):
    _config(monkeypatch, **{'security.require_login': True})
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    assert c.get('/api/profiles').status_code == 401


def test_route_picker_mode_member_still_sees_the_family(members, monkeypatch):
    _config(monkeypatch, **{'security.require_login': False})
    me, other = members
    ids = _listed(_client_as(me))
    assert 1 in ids and me in ids and other in ids
