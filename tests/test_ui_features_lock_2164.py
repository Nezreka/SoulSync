"""#2164 (SeadogsBooty), the follow-up to #2157: locking a profile kept the
member's old pick (parts they hid stayed hidden), Customize stayed in their
sidebar read only, and access vs what shows wasn't clear for the admin.

the member's own pick and the admin's set are two layers now. these pin
which one wins in every lock path, and the server side of page access.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-features-2164-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'features.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

from core import ui_features as uf  # noqa: E402
from core.permissions import page_denied, page_for_path  # noqa: E402


class _FakeDb:
    def __init__(self, profiles=None):
        self.rows = {}
        self.profiles = {p['id']: p for p in (profiles or [])}

    def set_metadata(self, k, v):
        self.rows[k] = v

    def get_metadata(self, k):
        return self.rows.get(k)

    def delete_metadata(self, k):
        self.rows.pop(k, None)
        return True

    def get_profile(self, pid):
        return self.profiles.get(pid)

    def get_all_profiles(self):
        return list(self.profiles.values())


def _member(pid):
    return {'id': pid, 'is_admin': False, 'allowed_sides': 'both', 'allowed_pages': None}


ADMIN = {'id': 1, 'is_admin': True, 'allowed_sides': 'both', 'allowed_pages': None}


# ── the member's old pick never wins while locked ────────────────────────────

def test_admin_locks_without_editing_drops_the_members_old_pick():
    """the reporter: a member hid parts, the admin locked the profile, the
    parts stayed hidden. locking alone now shows the admin's set (here the
    default for new profiles, everything on)."""
    db = _FakeDb([_member(2)])
    uf.save_off(db, 2, ['discover', 'stats'])
    assert uf.load_off(db, 2) == ['discover', 'stats']
    uf.admin_lock(db, 2, True)
    assert uf.load_off(db, 2) == []
    assert uf.is_locked(db, 2) is True
    assert uf.follows_default(db, 2) is True
    # and unlocking doesn't bring the old pick back
    uf.admin_lock(db, 2, False)
    assert uf.load_off(db, 2) == []


def test_admin_edits_then_locks_shows_the_admins_set():
    db = _FakeDb([_member(2)])
    uf.save_off(db, 2, ['discover'])
    uf.save_admin_off(db, 2, ['chat'])
    uf.admin_lock(db, 2, True)
    assert uf.load_off(db, 2) == ['chat']
    assert uf.own_off(db, 2) is None


def test_an_own_pick_left_in_storage_never_wins_while_locked():
    """belt and braces: even a pick written behind the lock's back (an old
    row, a race) is ignored until the lock comes off."""
    db = _FakeDb([_member(2)])
    uf.save_admin_off(db, 2, ['chat'])
    uf.set_locked(db, 2, True)
    db.rows['ui_features_off:2'] = json.dumps(['discover', 'stats'])
    assert uf.load_off(db, 2) == ['chat']
    assert uf.profile_payload(db, 2)['off'] == ['chat']


def test_the_default_lock_wins_over_an_own_pick():
    """a locked default reaches everyone the admin never set by hand, their
    own picks included. lifting it gives them their picks back: the admin
    never touched those profiles, so nothing of theirs was thrown away."""
    db = _FakeDb([_member(2)])
    uf.save_off(db, 2, ['discover'])
    uf.save_default(db, ['podcasts'], locked=True)
    assert uf.is_locked(db, 2) is True
    assert uf.load_off(db, 2) == ['podcasts']
    assert uf.follows_default(db, 2) is True
    uf.save_default(db, ['podcasts'], locked=False)
    assert uf.load_off(db, 2) == ['discover']


def test_unlocking_keeps_what_the_admin_set():
    """least surprise: unlocking changes nothing they see, it just lets them
    change it again. their pick from before the lock doesn't come back."""
    db = _FakeDb([_member(2)])
    uf.save_off(db, 2, ['discover', 'stats'])
    uf.save_admin_off(db, 2, ['chat'])
    uf.admin_lock(db, 2, True)
    uf.admin_lock(db, 2, False)
    assert uf.is_locked(db, 2) is False
    assert uf.load_off(db, 2) == ['chat']
    # and from there their own pick wins again
    uf.save_off(db, 2, [])
    assert uf.load_off(db, 2) == []


def test_an_admin_set_on_an_unlocked_profile_shows_now():
    """the admin changing the switches has to change what they see, or the
    editor would save something nobody sees."""
    db = _FakeDb([_member(2)])
    uf.save_off(db, 2, ['discover'])
    uf.save_admin_off(db, 2, ['tools'])
    assert uf.load_off(db, 2) == ['tools'] and uf.is_locked(db, 2) is False


# ── resets ───────────────────────────────────────────────────────────────────

def test_the_members_reset_only_drops_their_own_pick():
    db = _FakeDb([_member(2)])
    uf.save_admin_off(db, 2, ['chat'])
    uf.set_locked(db, 2, False)
    uf.save_off(db, 2, ['discover'])
    uf.reset_own(db, 2)
    assert uf.load_off(db, 2) == ['chat']
    # the admin's unlock by hand stays, even with a locked default
    uf.save_default(db, [], locked=True)
    assert uf.is_locked(db, 2) is False


def test_the_admins_reset_drops_all_three():
    db = _FakeDb([_member(2)])
    uf.save_default(db, ['podcasts'], locked=False)
    uf.save_admin_off(db, 2, ['chat'])
    uf.admin_lock(db, 2, True)
    db.rows['ui_features_off:2'] = json.dumps(['discover'])
    uf.reset(db, 2)
    assert not {k for k in db.rows if k.endswith(':2')}
    assert uf.load_off(db, 2) == ['podcasts'] and uf.follows_default(db, 2) is True


# ── what the admin's editor reads ────────────────────────────────────────────

def test_the_admin_view_keeps_the_two_layers_apart():
    db = _FakeDb([_member(2)])
    uf.save_default(db, ['podcasts'], locked=False)
    uf.save_off(db, 2, ['discover'])
    body = uf.admin_payload(db, 2)
    # what they see now is their own pick, the switches are the admin's set
    assert body['off'] == ['discover']
    assert body['set_off'] == ['podcasts'] and body['admin_set'] is False
    assert body['own_off'] == ['discover'] and body['lock_set'] is False
    uf.save_admin_off(db, 2, ['chat'])
    uf.admin_lock(db, 2, True)
    body = uf.admin_payload(db, 2)
    assert body['set_off'] == ['chat'] and body['admin_set'] is True
    assert body['own_off'] is None and body['lock_set'] is True and body['locked'] is True


def test_an_admin_set_counts_as_chosen_and_blocks_setup():
    db = _FakeDb([_member(2)])
    uf.save_admin_off(db, 2, ['chat'])
    assert uf.has_chosen(db, 2) is True
    assert uf.seed_from_purposes(db, 2, ['music']) is None
    assert uf.load_off(db, 2) == ['chat']


def test_admins_have_one_layer():
    db = _FakeDb([ADMIN])
    uf.save_admin_off(db, 1, ['chat'])
    assert db.get_metadata('ui_features_admin:1') is None
    assert uf.load_off(db, 1) == ['chat']
    assert uf.own_off(db, 1) is None


# ── #2157-era data carries over once ─────────────────────────────────────────

def _old_locked(db, pid, off):
    """what #2157 stored: the admin's set in the member key, locked by hand"""
    db.rows[f'ui_features_off:{pid}'] = json.dumps(off)
    db.rows[f'ui_features_locked:{pid}'] = '1'


def test_a_2157_locked_set_carries_over_and_shows_the_same():
    db = _FakeDb([_member(2)])
    _old_locked(db, 2, ['chat', 'stats'])
    assert uf.load_off(db, 2) == uf.normalize_off(['chat', 'stats'])
    assert db.get_metadata('ui_features_off:2') is None
    assert json.loads(db.get_metadata('ui_features_admin:2')) == uf.normalize_off(['chat', 'stats'])
    body = uf.admin_payload(db, 2)
    assert body['set_off'] == uf.normalize_off(['chat', 'stats']) and body['admin_set'] is True
    assert body['own_off'] is None and body['locked'] is True


def test_the_carry_over_leaves_unlocked_and_already_set_profiles_alone():
    db = _FakeDb([_member(2), _member(3), _member(4), ADMIN])
    db.rows['ui_features_off:2'] = json.dumps(['discover'])          # unlocked: theirs
    _old_locked(db, 3, ['chat'])
    db.rows['ui_features_admin:3'] = json.dumps(['tools'])           # already carried
    db.rows['ui_features_off:4'] = json.dumps(['podcasts'])
    db.rows['ui_features_locked:4'] = '0'                             # unlocked by hand
    _old_locked(db, 1, ['chat'])                                      # admins have one layer
    uf.carry_over_2157(db)
    assert db.get_metadata('ui_features_off:2') == json.dumps(['discover'])
    assert db.get_metadata('ui_features_admin:2') is None
    assert db.get_metadata('ui_features_admin:3') == json.dumps(['tools'])
    assert db.get_metadata('ui_features_off:4') == json.dumps(['podcasts'])
    assert db.get_metadata('ui_features_admin:4') is None
    assert db.get_metadata('ui_features_off:1') == json.dumps(['chat'])
    assert uf.load_off(db, 2) == ['discover'] and uf.load_off(db, 4) == ['podcasts']


def test_the_carry_over_runs_once():
    db = _FakeDb([_member(2)])
    assert uf.carry_over_2157(db) is True
    assert db.get_metadata('ui_features_admin_carried_2164') == '1'
    # a member key written after the pass is never touched again
    _old_locked(db, 2, ['chat'])
    uf.load_off(db, 2)
    assert db.get_metadata('ui_features_off:2') == json.dumps(['chat'])
    assert db.get_metadata('ui_features_admin:2') is None


def test_a_failed_carry_over_tries_again_next_read():
    class Flaky(_FakeDb):
        fail = True

        def get_all_profiles(self):
            if self.fail:
                raise RuntimeError('db busy')
            return super().get_all_profiles()

    db = Flaky([_member(2)])
    _old_locked(db, 2, ['chat'])
    assert uf.carry_over_2157(db) is False
    assert db.get_metadata('ui_features_admin_carried_2164') is None
    db.fail = False
    assert uf.load_off(db, 2) == ['chat']
    assert db.get_metadata('ui_features_admin:2') == json.dumps(['chat'])


# ── page access, the other layer ─────────────────────────────────────────────

def test_sample_studio_apis_belong_to_its_page():
    assert page_for_path('/api/sample/stems') == 'sample-studio'
    assert page_for_path('/api/sample/stash/3/audio') == 'sample-studio'
    assert page_for_path('/api/samples-other') is None
    assert page_denied('/api/sample/stems', ['dashboard'], False)
    assert not page_denied('/api/sample/stems', None, False)
    assert not page_denied('/api/sample/stems', ['sample-studio'], False)


# ── routes, for real ─────────────────────────────────────────────────────────

web_server = pytest.importorskip('web_server')


@pytest.fixture
def db():
    database = web_server.get_database()
    database.delete_metadata('ui_features_default')
    yield database
    database.delete_metadata('ui_features_default')


def _client(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s['profile_id'] = pid
    return c


def _new(db, **kw):
    return db.create_profile(name=f'p_{os.urandom(3).hex()}', **kw)


def test_locking_through_the_route_drops_the_members_pick(db):
    member = _new(db)
    cm, admin = _client(member), _client(1)
    assert cm.post('/api/profiles/me/features', json={'off': ['discover', 'stats']}).status_code == 200

    seen = admin.get(f'/api/profiles/{member}/features').get_json()
    assert seen['own_off'] == ['discover', 'stats'] and seen['set_off'] == []

    r = admin.post(f'/api/profiles/{member}/features', json={'locked': True}).get_json()
    assert r['locked'] is True and r['off'] == [] and r['own_off'] is None
    mine = cm.get('/api/profiles/me/features').get_json()
    assert mine['locked'] is True and mine['off'] == []
    # unlock: what the admin set stays, the old pick doesn't come back
    admin.post(f'/api/profiles/{member}/features', json={'locked': False})
    assert cm.get('/api/profiles/me/features').get_json()['off'] == []


def test_the_members_reset_route_keeps_the_admins_set(db):
    member = _new(db)
    admin = _client(1)
    admin.post(f'/api/profiles/{member}/features', json={'off': ['chat'], 'locked': False})
    cm = _client(member)
    cm.post('/api/profiles/me/features', json={'off': []})
    r = cm.post('/api/profiles/me/features', json={'reset': True}).get_json()
    assert r['off'] == ['chat'] and r['locked'] is False


def test_a_sample_studio_api_refuses_a_profile_without_the_page(db):
    member = _new(db, allowed_pages=['dashboard', 'library'])
    r = _client(member).get('/api/sample/stash')
    assert r.status_code == 403 and r.get_json()['error'] == 'page_not_allowed'
    db.update_profile(member, allowed_pages=None)
    assert _client(member).get('/api/sample/stash').status_code != 403
