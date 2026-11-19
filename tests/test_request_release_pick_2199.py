"""#2199: approved requests can wait for an admin to pick the release.

one setting per kind (music, movies, tv, audiobooks), automatic by default.
in manual mode an approval holds the request: nothing automatic downloads it
(the wishlist feeds every music download path reads, the audiobook pass),
the Requests page lists it for whoever may answer it, and a pick goes
through the normal download path. this file: the setting, music and books.
movies and tv: tests/video/test_video_request_release_pick_2199.py.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-pick2199-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'pick2199.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

from core.requests import acquisition  # noqa: E402


class _Config:
    """config_manager's get/set over a dict, so no test touches the real one."""

    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key, default=None):
        node = self.data
        for k in key.split('.'):
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def set(self, key, value):
        node = self.data
        keys = key.split('.')
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value


# ── the setting ──────────────────────────────────────────────────────────

def test_every_kind_is_automatic_until_someone_changes_it():
    assert acquisition.modes(_Config()) == {'music': 'auto', 'movie': 'auto', 'tv': 'auto',
                                            'audiobook': 'auto'}
    # a junk value reads as the default, never as manual
    assert acquisition.mode_for('music', _Config({'requests': {'acquisition': {'music': 'yes'}}})) == 'auto'


def test_set_modes_saves_only_known_kinds_and_modes():
    cfg = _Config()
    assert acquisition.set_modes({'audiobook': 'manual'}, cfg)['audiobook'] == 'manual'
    assert cfg.get('requests.acquisition.audiobook') == 'manual'
    for bad in ({'podcast': 'manual'}, {'music': 'sometimes'}, {}):
        with pytest.raises(ValueError):
            acquisition.set_modes(bad, cfg)
    assert acquisition.modes(cfg)['music'] == 'auto'


def test_the_approvers_choice_wins_over_the_setting():
    cfg = _Config({'requests': {'acquisition': {'tv': 'manual'}}})
    assert acquisition.resolve('tv', None, cfg) == 'manual'
    assert acquisition.resolve('tv', 'auto', cfg) == 'auto'
    assert acquisition.resolve('music', 'manual', cfg) == 'manual'
    assert acquisition.resolve('music', 'bogus', cfg) == 'auto'
    # nothing to pick between: always automatic
    assert acquisition.resolve(None, 'manual', cfg) == 'auto'
    assert [acquisition.video_kind(k) for k in ('movie', 'show', 'episode', 'youtube')] == \
        ['movie', 'tv', 'tv', None]


# ── the routes, for real ─────────────────────────────────────────────────

web_server = pytest.importorskip('web_server')

from core import permission_store as PS  # noqa: E402


def _db():
    return web_server.get_database()


def _as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


@pytest.fixture(autouse=True)
def _wishlist_service_on_this_db(monkeypatch):
    """the wishlist service is one per process and keeps the database it first
    opened. an earlier test can leave it on another one, and then these tests
    read an empty wishlist (dev CI, Nov 19). give it this run's database."""
    from core.wishlist import service as ws
    svc = ws.WishlistService()
    svc._database = _db()
    monkeypatch.setattr(ws, '_wishlist_service', svc)


@pytest.fixture
def cfg(monkeypatch):
    c = _Config()
    monkeypatch.setattr(acquisition, '_config', lambda config=None: config if config is not None else c)
    return c


@pytest.fixture
def notes(monkeypatch):
    import core.profile_notify as pn
    got = []
    monkeypatch.setattr(pn, '_journal', lambda pid, kind, msg: got.append((pid, kind, msg)))
    monkeypatch.setattr(pn, '_emitter', None)
    return got


@pytest.fixture
def made():
    pids = []

    def make(template='adult', **changes):
        db = _db()
        pid = db.create_profile(name='m_%s' % uuid4().hex[:8])
        tid = next(t['id'] for t in db.list_permission_templates() if t['slug'] == template)
        PS.change_profile(db, db.get_profile(pid), {'template_id': tid, 'set': changes})
        pids.append(pid)
        return pid
    yield make
    for pid in pids:
        try:
            _db().clear_wishlist(profile_id=pid)
            _db().delete_profile(pid)
        except Exception:  # noqa: BLE001
            pass


def _album(tag, n=2):
    return [{'id': f'{tag}{i}', 'name': f'song {i}', 'artists': [{'name': 'Radiohead'}],
             'album': {'id': f'alb{tag}', 'name': f'Album {tag}', 'album_type': 'album',
                       'images': [{'url': 'http://img/x.jpg'}]}} for i in range(n)]


@pytest.fixture
def asker(made):
    """an Ask first profile with one album (two tracks) on its wishlist."""
    pid = made(**{'music.get': 'ask'})
    tag = uuid4().hex[:6]
    for t in _album(tag):
        _db().add_to_wishlist(spotify_track_data=t, source_type='album', profile_id=pid,
                              user_initiated=True)
    return pid, tag


def _waiting(client, owner):
    body = client.get('/api/requests/music?status=pending').get_json()
    return next(g for g in body['pending'] if g['profile_id'] == owner)


def _feed(pid, **kw):
    from core.wishlist.service import get_wishlist_service
    return get_wishlist_service().get_wishlist_tracks_for_download(profile_id=pid, **kw)


def test_only_an_admin_changes_the_setting(cfg, made):
    member = _as(made(**{'requests.moderate': True}))
    assert member.get('/api/requests/acquisition').get_json()['may_change'] is False
    assert member.put('/api/requests/acquisition', json={'modes': {'music': 'manual'}}).status_code == 403
    assert acquisition.mode_for('music', cfg) == 'auto'
    admin = _as(1)
    r = admin.put('/api/requests/acquisition', json={'modes': {'music': 'manual', 'movie': 'manual'}})
    assert r.status_code == 200 and r.get_json()['modes']['music'] == 'manual'
    assert admin.put('/api/requests/acquisition', json={'modes': {'music': 'later'}}).status_code == 400
    assert admin.get('/api/requests/acquisition').get_json()['modes']['movie'] == 'manual'


# ── music ────────────────────────────────────────────────────────────────

def test_automatic_by_default_the_approval_downloads_as_before(cfg, asker, notes):
    pid, _tag = asker
    admin = _as(1)
    group = _waiting(admin, pid)
    r = admin.post('/api/requests/music/approve', json={'profile_id': pid, 'key': group['key']})
    assert r.get_json()['acquisition'] == 'auto'
    assert len(_feed(pid, approved_only=True)) == 2
    assert _db().list_music_requests(profile_id=pid)[0]['status'] == 'approved'


def test_manual_mode_holds_the_rows_out_of_every_download_feed(cfg, asker, notes):
    pid, _tag = asker
    cfg.set('requests.acquisition.music', 'manual')
    admin = _as(1)
    group = _waiting(admin, pid)
    r = admin.post('/api/requests/music/approve', json={'profile_id': pid, 'key': group['key']})
    assert r.status_code == 200 and r.get_json()['acquisition'] == 'manual'
    # the scheduled run's feed (asks first: approved only), and the plain
    # feed the manual "download wishlist" and a profile with rights read
    assert _feed(pid, approved_only=True) == []
    assert _feed(pid) == []
    # the owned-track sweeps still see them, so a landed pick clears its row
    from core.wishlist.service import get_wishlist_service
    assert len(get_wishlist_service().get_wishlist_tracks_including_held(profile_id=pid)) == 2
    hist = _db().list_music_requests(profile_id=pid)
    assert [h['status'] for h in hist] == ['awaiting_selection']
    assert any(p == pid and 'picked' in m for p, _k, m in notes)
    # not waiting for an answer any more
    body = admin.get('/api/requests/music?status=pending').get_json()
    assert all(g['profile_id'] != pid for g in body['pending'])


def test_the_approver_can_choose_either_way(cfg, asker):
    pid, _tag = asker
    cfg.set('requests.acquisition.music', 'manual')
    admin = _as(1)
    group = _waiting(admin, pid)
    r = admin.post('/api/requests/music/approve',
                   json={'profile_id': pid, 'key': group['key'], 'acquisition': 'auto'})
    assert r.get_json()['acquisition'] == 'auto' and len(_feed(pid, approved_only=True)) == 2


def test_the_approver_can_hold_one_on_an_automatic_default(cfg, made):
    pid = made(**{'music.get': 'ask'})
    tag = uuid4().hex[:6]
    for t in _album(tag):
        _db().add_to_wishlist(spotify_track_data=t, source_type='album', profile_id=pid, user_initiated=True)
    admin = _as(1)
    group = _waiting(admin, pid)
    r = admin.post('/api/requests/music/approve',
                   json={'profile_id': pid, 'key': group['key'], 'acquisition': 'manual'})
    assert r.get_json()['acquisition'] == 'manual' and _feed(pid, approved_only=True) == []


def test_the_queue_shows_held_asks_to_whoever_may_answer_them(cfg, asker, made):
    pid, tag = asker
    cfg.set('requests.acquisition.music', 'manual')
    admin = _as(1)
    admin.post('/api/requests/music/approve', json={'profile_id': pid, 'key': _waiting(admin, pid)['key']})
    body = admin.get('/api/requests/awaiting').get_json()
    mine = [g for g in body['music'] if g['profile_id'] == pid]
    assert len(mine) == 1 and mine[0]['track_count'] == 2 and mine[0]['status'] == 'awaiting_selection'
    assert mine[0]['requester_name'] and mine[0]['approved_at']
    # the badge counts it for the admin
    assert admin.get('/api/requests/music/counts').get_json()['pending'] >= 1
    # a member: no queue
    assert _as(made()).get('/api/requests/awaiting').status_code == 403
    # a moderator sees someone else's, never picks their own
    mod_pid = made(**{'music.get': 'ask', 'requests.moderate': True})
    for t in _album(uuid4().hex[:6]):
        _db().add_to_wishlist(spotify_track_data=t, source_type='album', profile_id=mod_pid,
                              user_initiated=True)
    admin.post('/api/requests/music/approve',
               json={'profile_id': mod_pid, 'key': _waiting(admin, mod_pid)['key']})
    mod = _as(mod_pid)
    seen = {g['profile_id'] for g in mod.get('/api/requests/awaiting').get_json()['music']}
    assert pid in seen and mod_pid not in seen
    own_track = _db().get_awaiting_request_rows(mod_pid)[0]['spotify_track_id']
    r = mod.post('/api/requests/music/pick/inspect', json={'profile_id': mod_pid, 'track_id': own_track})
    assert r.status_code == 403


def test_a_pick_downloads_into_the_requesters_library_and_moves_the_request_on(cfg, asker, notes, made,
                                                                              monkeypatch):
    pid, tag = asker
    cfg.set('requests.acquisition.music', 'manual')
    admin = _as(1)
    admin.post('/api/requests/music/approve', json={'profile_id': pid, 'key': _waiting(admin, pid)['key']})
    track_id = admin.get('/api/requests/awaiting').get_json()['music'][0]['track_ids'][0]
    started = []
    pb = web_server._pinned_batch
    monkeypatch.setattr(pb, 'create_pinned_batch',
                        lambda files, **kw: started.append((files, kw)) or ('b1', ['t1']))
    monkeypatch.setattr(pb, 'dispatch_pinned_batch', lambda *a, **k: None)
    candidate = {'username': 'peer', 'filename': 'Radiohead/song 0.flac', 'size': 1}
    # a member can't, an unheld track isn't there
    assert _as(made()).post('/api/requests/music/pick/download',
                            json={'profile_id': pid, 'track_id': track_id,
                                  'candidate': candidate}).status_code == 403
    assert admin.post('/api/requests/music/pick/download',
                      json={'profile_id': pid, 'track_id': 'nope', 'candidate': candidate}).status_code == 404
    assert started == []
    r = admin.post('/api/requests/music/pick/download',
                   json={'profile_id': pid, 'track_id': track_id, 'candidate': candidate})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert len(started) == 1 and started[0][1]['profile_id'] == pid
    assert started[0][1]['source_page'] == 'Requests'
    # the request is on its way for the requester; the row stays held until
    # the file lands (a failed pick is picked again, never retried by itself)
    assert _db().list_music_requests(profile_id=pid)[0]['status'] == 'approved'
    assert any(p == pid and 'on the way' in m for p, _k, m in notes)
    assert _feed(pid) == [] and len(_db().get_awaiting_request_rows(pid)) == 2


def test_handing_a_held_ask_back_to_the_automatic_pass(cfg, asker, made):
    pid, _tag = asker
    cfg.set('requests.acquisition.music', 'manual')
    admin = _as(1)
    key = _waiting(admin, pid)['key']
    admin.post('/api/requests/music/approve', json={'profile_id': pid, 'key': key})
    assert _as(made()).post('/api/requests/music/awaiting/auto',
                            json={'profile_id': pid, 'key': key}).status_code == 403
    r = admin.post('/api/requests/music/awaiting/auto', json={'profile_id': pid, 'key': key})
    assert r.get_json()['released'] == 2
    assert len(_feed(pid, approved_only=True)) == 2
    assert _db().list_music_requests(profile_id=pid)[0]['status'] == 'approved'


def test_a_held_ask_settles_like_any_other_once_its_rows_are_gone(cfg, asker):
    pid, _tag = asker
    cfg.set('requests.acquisition.music', 'manual')
    admin = _as(1)
    group = _waiting(admin, pid)
    admin.post('/api/requests/music/approve', json={'profile_id': pid, 'key': group['key']})
    for t in group['track_ids']:
        _db().remove_from_wishlist(t, profile_id=pid)
    from api.music_requests import sweep_fulfillment
    assert sweep_fulfillment(_db(), only_pid=pid) == 1
    assert _db().list_music_requests(profile_id=pid)[0]['status'] == 'removed'


# ── audiobooks ───────────────────────────────────────────────────────────

@pytest.fixture
def book_ask(made, monkeypatch):
    """an Ask first profile's book request, with the wishlist add recorded."""
    pid = made(**{'books.get': 'ask'})
    asin = 'B0' + uuid4().hex[:8].upper()
    rid = _db().add_media_request(profile_id=pid, requester_name='Maya', kind='audiobook',
                                  item_key=asin, title='Wind and Truth', subtitle='Brandon Sanderson',
                                  payload={'book': {'asin': asin, 'title': 'Wind and Truth'},
                                           'narrator_mode': 'exact'})
    started = []
    import core.requests.media as media
    monkeypatch.setattr(media, 'start_approved', lambda req: started.append(req['id']) or None)
    return pid, asin, rid, started


def test_a_book_approval_goes_to_the_wishlist_by_default(cfg, book_ask):
    pid, asin, rid, started = book_ask
    r = _as(1).post(f'/api/requests/media/{rid}/approve')
    assert r.get_json()['acquisition'] == 'auto'
    assert started == [rid] and _db().get_media_request(rid)['status'] == 'approved'


def test_a_held_book_goes_nowhere_until_a_release_is_picked(cfg, book_ask, made):
    pid, asin, rid, started = book_ask
    cfg.set('requests.acquisition.audiobook', 'manual')
    admin = _as(1)
    r = admin.post(f'/api/requests/media/{rid}/approve')
    assert r.get_json()['acquisition'] == 'manual'
    # not on the wishlist, and the audiobook pass would refuse it anyway
    assert started == [] and _db().get_media_request(rid)['status'] == 'awaiting_selection'
    from core.requests.media import approved_book, requested_books
    assert approved_book(_db(), pid, asin) is False
    # still "requested" for the requester (no second ask)
    assert asin in requested_books(pid, _db())
    queue = admin.get('/api/requests/awaiting').get_json()['media']
    assert [q['id'] for q in queue if q['id'] == rid] == [rid] and queue[0]['asin']
    assert _as(made()).post(f'/api/requests/media/{rid}/picked').status_code == 403
    assert admin.post(f'/api/requests/media/{rid}/picked').status_code == 200
    assert _db().get_media_request(rid)['status'] == 'approved'
    assert approved_book(_db(), pid, asin) is True
    # once moved on it's not waiting any more
    assert admin.post(f'/api/requests/media/{rid}/picked').status_code == 404


def test_a_held_book_can_go_to_the_wishlist_pass_after_all(cfg, book_ask):
    pid, asin, rid, started = book_ask
    admin = _as(1)
    admin.post(f'/api/requests/media/{rid}/approve', json={'acquisition': 'manual'})
    assert started == []
    assert admin.post(f'/api/requests/media/{rid}/auto').status_code == 200
    assert started == [rid] and _db().get_media_request(rid)['status'] == 'approved'


def test_a_podcast_episode_is_never_held(cfg, made, monkeypatch):
    cfg.set('requests.acquisition.audiobook', 'manual')
    pid = made(**{'podcasts.get': 'ask'})
    rid = _db().add_media_request(profile_id=pid, requester_name='Maya', kind='podcast',
                                  item_key='g-' + uuid4().hex, title='Ep 1', subtitle='Show',
                                  payload={'enclosure_url': 'http://x/ep1.mp3'})
    import core.requests.media as media
    monkeypatch.setattr(media, 'start_approved', lambda req: None)
    r = _as(1).post(f'/api/requests/media/{rid}/approve', json={'acquisition': 'manual'})
    assert r.get_json()['acquisition'] == 'auto' and _db().get_media_request(rid)['status'] == 'approved'


def test_a_book_already_in_the_library_is_never_held(cfg, book_ask, monkeypatch):
    pid, asin, rid, started = book_ask
    cfg.set('requests.acquisition.audiobook', 'manual')
    import core.requests.media as media
    monkeypatch.setattr(media, 'book_in_library', lambda a: str(a) == asin)
    r = _as(1).post(f'/api/requests/media/{rid}/approve')
    assert r.get_json()['acquisition'] == 'auto'
    assert started == [rid] and _db().get_media_request(rid)['status'] == 'approved'


def test_a_held_book_that_arrives_another_way_settles(cfg, book_ask, monkeypatch):
    pid, asin, rid, started = book_ask
    cfg.set('requests.acquisition.audiobook', 'manual')
    admin = _as(1)
    admin.post(f'/api/requests/media/{rid}/approve')
    assert _db().get_media_request(rid)['status'] == 'awaiting_selection'
    import core.requests.media as media
    monkeypatch.setattr(media, 'book_in_library', lambda a: str(a) == asin)
    from api.music_requests import sweep_fulfillment
    sweep_fulfillment(_db(), only_pid=pid)
    assert _db().get_media_request(rid)['status'] == 'available'
    assert all(q['id'] != rid for q in admin.get('/api/requests/awaiting').get_json()['media'])
