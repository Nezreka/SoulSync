"""#1909 (neonmaus): soulsync shows every feature to everyone and new users
find it overwhelming. each profile can now hide the parts it doesn't use, with
a simple and a full preset. these pin the rules and the real routes.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-features-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'features.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

from core import ui_features as uf  # noqa: E402


# ── pure rules ───────────────────────────────────────────────────────────────

def test_the_core_can_never_be_hidden():
    """search, library, watchlist, wishlist, downloads and settings are what
    soulsync is. no feature owns them, so no choice can hide them."""
    owned = {p for f in uf.FEATURES for p in f['pages']}
    for page in ('dashboard', 'search', 'library', 'watchlist', 'wishlist',
                 'active-downloads', 'settings', 'help', 'issues', 'requests'):
        assert page not in owned


def test_simple_is_what_the_thread_asked_for():
    """neonmaus: downloading, library, artist following, discover and
    playlists for plexamp. simple keeps exactly discover and playlist sync on
    top of the core and hides the rest."""
    on = [fid for fid in uf.FEATURE_IDS if fid not in uf.PRESETS['simple']]
    assert on == ['discover', 'playlists']
    assert uf.PRESETS['full'] == []


def test_off_list_is_cleaned():
    assert uf.normalize_off(['video', 'nope', 'video', 'chat']) == ['video', 'chat']
    assert uf.normalize_off('["podcasts"]') == ['podcasts']
    # anything unreadable is the shipped app, never a blank sidebar
    assert uf.normalize_off(None) == []
    assert uf.normalize_off('not json') == []
    assert uf.normalize_off({'video': True}) == []


def test_preset_detection():
    assert uf.preset_for([]) == 'full'
    assert uf.preset_for(list(reversed(uf.PRESETS['simple']))) == 'simple'
    assert uf.preset_for(['chat']) == 'custom'


def test_hidden_pages_follow_the_features():
    pages = uf.hidden_pages(['video', 'chat'])
    assert 'video-dashboard' in pages and 'chat' in pages
    # video-chat is owned by both, listed once
    assert pages.count('video-chat') == 1
    assert uf.hidden_pages([]) == []


def test_setup_answer_hides_what_wasnt_picked():
    """setup asks what soulsync is for and then used to show everything
    anyway. music only now means no podcasts, audiobooks or video side."""
    assert uf.off_from_purposes(['music']) == ['podcasts', 'audiobooks', 'video']
    # youtube channels live on the video side
    assert 'video' not in uf.off_from_purposes(['music', 'youtube'])
    # picking everything hides nothing
    assert uf.off_from_purposes(['music', 'video', 'podcasts', 'audiobooks']) == []
    # a video-only install drops the music extras but keeps the core
    off = uf.off_from_purposes(['video'])
    assert 'discover' in off and 'video' not in off


class _FakeDb:
    def __init__(self, broken=False):
        self.rows, self.broken = {}, broken

    def set_metadata(self, k, v):
        if not self.broken:
            self.rows[k] = v

    def get_metadata(self, k):
        return self.rows.get(k)


def test_save_reads_back_and_refuses_a_lost_write():
    """set_metadata logs and swallows a failed write. the save must not say
    saved when nothing was written."""
    db = _FakeDb()
    assert uf.save_off(db, 3, ['chat', 'bogus']) == ['chat']
    assert json.loads(db.rows['ui_features_off:3']) == ['chat']
    assert uf.load_off(db, 3) == ['chat']
    with pytest.raises(RuntimeError):
        uf.save_off(_FakeDb(broken=True), 3, ['chat'])


def test_setup_seeds_once_and_never_overwrites_a_choice():
    db = _FakeDb()
    assert uf.seed_from_purposes(db, 1, ['music']) == ['podcasts', 'audiobooks', 'video']
    # someone turns podcasts back on by hand, then setup runs again
    uf.save_off(db, 1, ['audiobooks', 'video'])
    assert uf.seed_from_purposes(db, 1, ['music']) is None
    assert uf.load_off(db, 1) == ['audiobooks', 'video']
    assert uf.seed_from_purposes(db, None, ['music']) is None


def test_profiles_dont_share_a_choice():
    db = _FakeDb()
    uf.save_off(db, 2, ['video'])
    assert uf.load_off(db, 3) == []
    assert uf.load_off(db, None) == []


# ── routes, for real ─────────────────────────────────────────────────────────

web_server = pytest.importorskip('web_server')


@pytest.fixture
def db():
    return web_server.get_database()


def _client(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s['profile_id'] = pid
    return c


def test_routes_save_per_profile(db):
    a = db.create_profile(name=f'a_{os.urandom(3).hex()}')
    b = db.create_profile(name=f'b_{os.urandom(3).hex()}')
    ca, cb = _client(a), _client(b)

    first = ca.get('/api/profiles/me/features').get_json()
    assert first['success'] and first['off'] == [] and first['preset'] == 'full'
    assert {f['id'] for f in first['features']} == set(uf.FEATURE_IDS)

    r = ca.post('/api/profiles/me/features', json={'preset': 'simple'}).get_json()
    assert r['preset'] == 'simple' and 'video-dashboard' in r['hidden_pages']

    # b never sees a's choice
    assert cb.get('/api/profiles/me/features').get_json()['off'] == []

    r = cb.post('/api/profiles/me/features', json={'off': ['chat', 'junk']}).get_json()
    assert r['off'] == ['chat'] and r['preset'] == 'custom'

    r = cb.post('/api/profiles/me/features', json={'purposes': ['music']}).get_json()
    assert r['off'] == ['podcasts', 'audiobooks', 'video']


def test_bad_payloads_are_refused(db):
    pid = db.create_profile(name=f'c_{os.urandom(3).hex()}')
    c = _client(pid)
    assert c.post('/api/profiles/me/features', json={'preset': 'tiny'}).status_code == 400
    assert c.post('/api/profiles/me/features', json={}).status_code == 400
    assert c.get('/api/profiles/me/features').get_json()['off'] == []


def test_deleting_a_profile_drops_its_choice(db):
    pid = db.create_profile(name=f'd_{os.urandom(3).hex()}')
    _client(pid).post('/api/profiles/me/features', json={'preset': 'simple'})
    assert uf.load_off(db, pid) == uf.PRESETS['simple']
    assert _client(1).delete(f'/api/profiles/{pid}').get_json()['success']
    assert uf.load_off(db, pid) == []


def test_finishing_setup_hides_what_wasnt_picked(db):
    """the purpose step promised the rest would be out of the way. finishing
    setup as a music-only install now hides podcasts, audiobooks and video."""
    pid = db.create_profile(name=f'e_{os.urandom(3).hex()}')
    c = _client(pid)
    assert c.post('/api/setup/complete', json={'purposes': ['music', 'podcasts']}).get_json()['success']
    assert uf.load_off(db, pid) == ['audiobooks', 'video']
