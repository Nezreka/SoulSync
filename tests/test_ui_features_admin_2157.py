"""#2157 (SeadogsBooty): a member hid a part in Customize, the sidebar lost
its sections and switching it back on didn't bring them back. they asked for
admin control: an admin sets what a profile shows and can lock it, sets what
new profiles start with, and a reset. these pin the rules and the routes.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-features-2157-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'features.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

from core import ui_features as uf  # noqa: E402


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


def _member(pid, sides='both', pages=None):
    return {'id': pid, 'is_admin': False, 'allowed_sides': sides, 'allowed_pages': pages}


ADMIN = {'id': 1, 'is_admin': True, 'allowed_sides': 'both', 'allowed_pages': None}


# ── the sidebar that never came back ─────────────────────────────────────────

def test_a_video_only_profile_cant_hide_its_only_side():
    """the reporter's profile could only open the video side. hiding movies &
    tv moved it onto the music side it can't open, with nothing in the
    sidebar and no switch back. its only side is never hideable now, and an
    old saved choice that hid it is ignored."""
    kid = _member(5, sides='video', pages=['video-dashboard', 'video-discover', 'help'])
    db = _FakeDb([kid])
    assert 'video' not in uf.offered(kid)
    assert uf.save_off(db, 5, ['video', 'video_discover']) == ['video_discover']
    # what was stored before the fix
    db.rows['ui_features_off:5'] = json.dumps(['video'])
    assert uf.load_off(db, 5) == []
    assert 'video-dashboard' not in uf.profile_payload(db, 5)['hidden_pages']


def test_offered_follows_sides_and_pages():
    music_only = _member(6, sides='music', pages=None)
    assert not {'video', 'video_discover', 'video_calendar'} & set(uf.offered(music_only))
    video_only = _member(7, sides='video', pages=None)
    # music side parts aren't offered to a profile that can't open music
    assert not {'discover', 'playlists', 'music_videos', 'podcasts'} & set(uf.offered(video_only))
    assert {'video_discover', 'chat'} <= set(uf.offered(video_only))
    picky = _member(8, sides='both', pages=['search', 'video-discover'])
    assert uf.offered(picky) == ['music_videos', 'video', 'video_discover']
    # admins and the new-profile default get every switch
    assert uf.offered(ADMIN) == list(uf.FEATURE_IDS)
    assert uf.offered(None) == list(uf.FEATURE_IDS)


# ── what new profiles start with ─────────────────────────────────────────────

def test_a_profile_that_never_chose_follows_the_default():
    db = _FakeDb([ADMIN, _member(2), _member(3)])
    assert uf.load_off(db, 2) == []
    uf.save_default(db, uf.PRESETS['simple'], locked=False)
    assert uf.load_off(db, 2) == uf.PRESETS['simple']
    assert uf.follows_default(db, 2) is True
    # the admin's say counts as chosen, no offer card for them
    assert uf.has_chosen(db, 2) is True
    # admins never follow it
    assert uf.load_off(db, 1) == []
    # a profile that picked its own keeps it
    uf.save_off(db, 3, ['chat'])
    assert uf.load_off(db, 3) == ['chat'] and uf.follows_default(db, 3) is False
    # and a change to the default reaches the followers only
    uf.save_default(db, ['podcasts'], locked=False)
    assert uf.load_off(db, 2) == ['podcasts'] and uf.load_off(db, 3) == ['chat']


def test_reset_goes_back_to_the_default():
    db = _FakeDb([_member(2)])
    uf.save_default(db, ['audiobooks'], locked=True)
    uf.save_off(db, 2, ['chat'])
    uf.set_locked(db, 2, False)
    assert uf.load_off(db, 2) == ['chat'] and uf.is_locked(db, 2) is False
    uf.reset(db, 2)
    assert uf.load_off(db, 2) == ['audiobooks']
    assert uf.is_locked(db, 2) is True
    assert uf.follows_default(db, 2) is True


def test_lock_rules():
    db = _FakeDb([ADMIN, _member(2), _member(3)])
    assert uf.is_locked(db, 2) is False
    uf.save_default(db, [], locked=True)
    assert uf.is_locked(db, 2) is True
    # set by hand beats the default either way
    uf.set_locked(db, 3, False)
    assert uf.is_locked(db, 3) is False
    # an admin is never locked out of their own
    db.rows['ui_features_locked:1'] = '1'
    assert uf.is_locked(db, 1) is False
    assert uf.is_locked(db, None) is False


def test_setup_never_overwrites_a_locked_profile():
    db = _FakeDb([_member(2)])
    uf.save_default(db, ['chat'], locked=True)
    assert uf.seed_from_purposes(db, 2, ['music']) is None
    assert uf.load_off(db, 2) == ['chat']


def test_a_lost_default_write_is_refused():
    class Broken(_FakeDb):
        def set_metadata(self, k, v):
            return None
    with pytest.raises(RuntimeError):
        uf.save_default(Broken(), ['chat'], locked=False)


# ── routes, for real ─────────────────────────────────────────────────────────

web_server = pytest.importorskip('web_server')


@pytest.fixture
def db():
    database = web_server.get_database()
    database.delete_metadata('ui_features_default')
    yield database
    # the default reaches every profile in this shared db, never leave one
    database.delete_metadata('ui_features_default')


def _client(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s['profile_id'] = pid
    return c


def _new(db, **kw):
    return db.create_profile(name=f'p_{os.urandom(3).hex()}', **kw)


def test_a_locked_profile_cant_change_its_own(db):
    member = _new(db)
    admin = _client(1)
    r = admin.post(f'/api/profiles/{member}/features', json={'preset': 'simple', 'locked': True})
    assert r.status_code == 200 and r.get_json()['locked'] is True

    cm = _client(member)
    body = cm.get('/api/profiles/me/features').get_json()
    assert body['locked'] is True and body['preset'] == 'simple' and body['chosen'] is True
    for payload in ({'off': []}, {'preset': 'full'}, {'reset': True}):
        r = cm.post('/api/profiles/me/features', json=payload)
        assert r.status_code == 403
        assert 'admin' in r.get_json()['error'].lower()
    assert uf.load_off(db, member) == uf.PRESETS['simple']

    # the admin can still change it, locked or not
    r = admin.post(f'/api/profiles/{member}/features', json={'off': ['chat']})
    assert r.get_json()['off'] == ['chat']
    # and unlock it, then the member can
    admin.post(f'/api/profiles/{member}/features', json={'locked': False})
    r = cm.post('/api/profiles/me/features', json={'off': []})
    assert r.status_code == 200 and r.get_json()['off'] == []


def test_only_an_admin_sets_someone_else(db):
    a, b = _new(db), _new(db)
    ca = _client(a)
    assert ca.get(f'/api/profiles/{b}/features').status_code == 403
    assert ca.post(f'/api/profiles/{b}/features', json={'off': ['chat']}).status_code == 403
    # not even for itself, that's what /me is for
    assert ca.post(f'/api/profiles/{a}/features', json={'locked': False}).status_code == 403
    assert ca.get('/api/profiles/feature-defaults').status_code == 403
    assert ca.post('/api/profiles/feature-defaults', json={'preset': 'simple'}).status_code == 403
    assert uf.load_off(db, b) == []

    admin = _client(1)
    assert admin.get('/api/profiles/999999/features').status_code == 404
    # an admin has nothing to lock
    boss = _new(db, is_admin=True)
    assert admin.post(f'/api/profiles/{boss}/features', json={'locked': True}).status_code == 400
    assert admin.post(f'/api/profiles/{b}/features', json={}).status_code == 400
    assert admin.post(f'/api/profiles/{b}/features', json={'preset': 'tiny'}).status_code == 400


def test_new_profiles_start_with_the_default(db):
    admin = _client(1)
    r = admin.post('/api/profiles/feature-defaults', json={'preset': 'simple', 'locked': True})
    body = r.get_json()
    assert body['success'] and body['set'] is True and body['locked'] is True
    assert admin.get('/api/profiles/feature-defaults').get_json()['off'] == uf.PRESETS['simple']

    # made through the real create route
    made = admin.post('/api/profiles', json={'name': f'n_{os.urandom(3).hex()}'}).get_json()
    pid = made['profile_id']
    mine = _client(pid).get('/api/profiles/me/features').get_json()
    assert mine['off'] == uf.PRESETS['simple']
    assert mine['locked'] is True and mine['follows_default'] is True
    assert _client(pid).post('/api/profiles/me/features', json={'off': []}).status_code == 403

    # the admin's own view doesn't follow it
    assert admin.get('/api/profiles/me/features').get_json()['follows_default'] is False


def test_reset_puts_a_profile_back_on_the_default(db):
    member = _new(db)
    admin = _client(1)
    admin.post('/api/profiles/feature-defaults', json={'off': ['podcasts'], 'locked': False})
    _client(member).post('/api/profiles/me/features', json={'off': ['chat']})
    assert uf.load_off(db, member) == ['chat']
    # the member can reset itself while it's unlocked
    r = _client(member).post('/api/profiles/me/features', json={'reset': True}).get_json()
    assert r['off'] == ['podcasts'] and r['follows_default'] is True
    # and the admin can for them
    admin.post(f'/api/profiles/{member}/features', json={'off': ['chat'], 'locked': True})
    r = admin.post(f'/api/profiles/{member}/features', json={'reset': True}).get_json()
    assert r['off'] == ['podcasts'] and r['locked'] is False and r['follows_default'] is True


def test_deleting_a_profile_drops_its_lock_too(db):
    member = _new(db)
    _client(1).post(f'/api/profiles/{member}/features', json={'off': ['chat'], 'locked': True})
    assert _client(1).delete(f'/api/profiles/{member}').get_json()['success']
    assert db.get_metadata(f'ui_features_off:{member}') is None
    assert db.get_metadata(f'ui_features_locked:{member}') is None
