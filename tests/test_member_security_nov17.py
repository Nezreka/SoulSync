"""member security gaps from the nov 17 permissions review (PLAN.md section 3).

a "member" is a signed-in profile that is not an admin. every test here drives
the real route through the web_server app with a session set the way a browser
would have it: the member is refused, the admin (or the member on their own
data) is not.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-memsec-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'memsec.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.permissions import (  # noqa: E402
    DOWNLOADS_OFF, PROFILE_UNREADABLE, download_denied_reason, strict_profile_getter,
)
from core.security import oauth_pending  # noqa: E402


def _db():
    return web_server.get_database()


def _new_profile(**kw):
    return _db().create_profile(name=f'm_{os.urandom(3).hex()}', **kw)


def _login(client, pid):
    with client.session_transaction() as sess:
        sess['profile_id'] = pid


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def member(client):
    pid = _new_profile()
    _login(client, pid)
    return pid


@pytest.fixture
def admin(client):
    _new_profile()          # more than one profile: no session is nobody
    _login(client, 1)
    return 1


# ── S2 S6 S10 S11 S13 S14 S22: install-wide routes are the admin's ─────────

ADMIN_ONLY = [
    # S2 library metadata edits
    ('PUT', '/api/library/artist/1'),
    ('PUT', '/api/library/album/1'),
    ('PUT', '/api/library/track/1'),
    ('PUT', '/api/library/tracks/batch'),
    # S6 S14 shared library jobs
    ('POST', '/api/duplicate-cleaner/start'),
    ('POST', '/api/duplicate-cleaner/stop'),
    ('POST', '/api/library/blacklist'),
    ('DELETE', '/api/library/blacklist/1'),
    ('POST', '/api/metadata/start'),
    ('POST', '/api/metadata/stop'),
    ('POST', '/api/scan/request'),
    ('POST', '/api/library/reconcile-embedded-ids'),
    ('POST', '/api/searches/maintain'),
    ('POST', '/api/library/enrich'),
    ('POST', '/api/library/album/1/import-existing-track'),
    ('POST', '/api/library/album/1/missing-track/import-existing'),
    # S10 install-wide service accounts
    ('POST', '/api/spotify/disconnect'),
    ('GET', '/auth/spotify/export'),
    ('GET', '/auth/deezer'),
    ('GET', '/api/lastfm/auth-url'),
    ('GET', '/api/lastfm/callback'),
    ('POST', '/api/tidal/download/auth/start'),
    ('GET', '/api/tidal/download/auth/check'),
    ('POST', '/api/qobuz/auth/login'),
    ('POST', '/api/qobuz/auth/token'),
    ('POST', '/api/qobuz/auth/logout'),
    ('POST', '/api/tidal/disconnect'),
    ('POST', '/api/hydrabase/connect'),
    ('POST', '/api/hydrabase/disconnect'),
    ('POST', '/api/hydrabase/send'),
    # S11 media server config, setup, dev mode
    ('POST', '/api/setup/complete'),
    ('POST', '/api/detect-media-server'),
    ('POST', '/api/plex/pin/start'),
    ('GET', '/api/plex/pin/status'),
    ('POST', '/api/plex/select-music-library'),
    ('POST', '/api/jellyfin/select-user'),
    ('POST', '/api/jellyfin/select-music-library'),
    ('POST', '/api/navidrome/select-music-folder'),
    ('POST', '/api/dev-mode'),
    # S13 art and locks
    ('POST', '/api/album/1/art'),
    ('DELETE', '/api/album/1/art'),
    ('POST', '/api/artist/1/art'),
    ('DELETE', '/api/artist/1/art'),
    ('DELETE', '/api/album/1/metadata-lock'),
    ('POST', '/api/artist/1/write-image-to-disk'),
    # S22 install-wide settings
    ('POST', '/api/wishlist/cycle'),
    ('PUT', '/api/wishlist/retry-profile'),
    ('POST', '/api/discovery/lookback-period'),
    ('POST', '/api/discovery/hemisphere'),
    ('POST', '/api/image-cache/clear'),
    ('POST', '/api/image-cache/prune'),
]


@pytest.mark.parametrize('method,path', ADMIN_ONLY)
def test_a_member_is_refused(client, member, method, path):
    r = client.open(path, method=method, json={})
    assert r.status_code == 403, f'{method} {path} answered {r.status_code} for a member'


@pytest.mark.parametrize('method,path,body,expect', [
    ('PUT', '/api/library/artist/1', {}, 400),                 # "No data provided"
    ('DELETE', '/api/library/blacklist/987654', None, 200),
    ('POST', '/api/discovery/hemisphere', {'hemisphere': 'east'}, 400),
    ('PUT', '/api/wishlist/retry-profile', {'profile': 'nope'}, 400),
    ('GET', '/api/dev-mode', None, 200),
])
def test_the_admin_gets_past_the_gate(client, admin, method, path, body, expect):
    r = client.open(path, method=method, json=body)
    assert r.status_code == expect, (path, r.status_code, r.data[:200])


def test_reading_dev_mode_stays_open_to_a_member(client, member):
    assert client.get('/api/dev-mode').status_code == 200


# ── S1: start-missing-process checks the download switch ─────────────────────

def test_start_missing_process_refuses_a_profile_with_downloads_off(client):
    pid = _new_profile(can_download=False)
    _login(client, pid)
    r = client.post('/api/playlists/pl1/start-missing-process',
                    json={'tracks': [{'name': 'x', 'artists': ['y']}]})
    assert r.status_code == 403
    assert r.get_json()['error'] == DOWNLOADS_OFF


def test_start_missing_process_lets_a_downloading_profile_through(client, member):
    r = client.post('/api/playlists/pl1/start-missing-process', json={'tracks': []})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'No tracks provided'


# ── S3: reorganize every album of an artist, and the queue ───────────────────

class _Queue:
    def __init__(self):
        self.enqueued = None

    def enqueue_many(self, albums):
        self.enqueued = list(albums)
        return {'enqueued': len(albums), 'already_queued': 0, 'total': len(albums)}


@pytest.fixture
def artist_albums(monkeypatch):
    import core.library_reorganize as lr
    import core.reorganize_queue as rq
    owners = {}
    albums = [{'album_id': '11', 'album_title': 'A', 'artist_id': '5', 'artist_name': 'X'},
              {'album_id': '12', 'album_title': 'B', 'artist_id': '5', 'artist_name': 'X'}]
    monkeypatch.setattr(type(_db()), 'get_artist_albums_for_reorganize',
                        lambda self, artist_id: list(albums))
    monkeypatch.setattr(lr, 'resolve_album_profile_id',
                        lambda db, album_id, **k: owners.get(str(album_id)))
    queue = _Queue()
    monkeypatch.setattr(rq, 'get_queue', lambda: queue)
    return owners, queue


def test_reorganize_all_refuses_a_member_with_none_of_the_albums(client, member, artist_albums):
    owners, queue = artist_albums
    r = client.post('/api/library/artist/5/reorganize-all', json={})
    assert r.status_code == 403
    assert queue.enqueued is None


def test_reorganize_all_moves_only_a_members_own_albums(client, member, artist_albums):
    owners, queue = artist_albums
    owners['12'] = member
    r = client.post('/api/library/artist/5/reorganize-all', json={})
    assert r.status_code == 200
    assert [a['album_id'] for a in queue.enqueued] == ['12']


def test_reorganize_all_moves_everything_for_the_admin(client, admin, artist_albums):
    owners, queue = artist_albums
    r = client.post('/api/library/artist/5/reorganize-all', json={})
    assert r.status_code == 200
    assert [a['album_id'] for a in queue.enqueued] == ['11', '12']


@pytest.fixture
def real_queue(monkeypatch):
    import time

    import core.reorganize_queue as rq
    q = rq.ReorganizeQueue()

    def add(library):
        item = rq.QueueItem(queue_id=os.urandom(4).hex(), album_id='1', album_title='t',
                            artist_id='1', artist_name='a', enqueued_at=time.time(),
                            library=library)
        q._items.append(item)
        return item
    monkeypatch.setattr(rq, 'get_queue', lambda: q)
    return q, add


def test_a_member_cancels_only_their_own_reorganize(client, member, real_queue):
    q, add = real_queue
    theirs, mine = add('shared'), add(member)
    r = client.post(f'/api/library/reorganize/queue/{theirs.queue_id}/cancel')
    assert r.status_code == 403 and theirs.status == 'queued'
    r = client.post(f'/api/library/reorganize/queue/{mine.queue_id}/cancel')
    assert r.status_code == 200 and mine.status == 'cancelled'


def test_a_members_clear_leaves_other_libraries_queued(client, member, real_queue):
    q, add = real_queue
    theirs, mine = add('shared'), add(member)
    r = client.post('/api/library/reorganize/queue/clear')
    assert r.get_json()['cancelled'] == 1
    assert theirs.status == 'queued' and mine.status == 'cancelled'


def test_the_admin_clears_the_whole_queue(client, admin, real_queue):
    q, add = real_queue
    a, b = add('shared'), add(7)
    assert client.post('/api/library/reorganize/queue/clear').get_json()['cancelled'] == 2
    assert client.post(f'/api/library/reorganize/queue/{a.queue_id}/cancel').status_code == 409


# ── S10: oauth only binds the caller's own account ───────────────────────────

class _OAuth:
    def get_authorize_url(self):
        return 'https://accounts.example/authorize'


def test_spotify_connect_for_another_profile_is_refused(client, member):
    other = _new_profile()
    assert client.get(f'/auth/spotify?profile_id={other}').status_code == 403
    assert client.get('/auth/spotify').status_code == 403          # the install's account


def test_spotify_connect_for_your_own_profile_starts(client, member, monkeypatch):
    monkeypatch.setattr(web_server, '_profile_spotify_oauth', lambda pid: _OAuth())
    oauth_pending.clear()
    r = client.get(f'/auth/spotify?profile_id={member}')
    assert r.status_code == 302
    assert oauth_pending.consume(f'spotify:profile:{member}')


@pytest.fixture
def exchange(monkeypatch):
    done = []
    monkeypatch.setattr(web_server, '_complete_profile_spotify_auth',
                        lambda pid, code: done.append(pid) or 'CONNECTED')
    oauth_pending.clear()
    return done


def test_spotify_callback_will_not_bind_another_profile(client, member, exchange):
    other = _new_profile()
    oauth_pending.mark(f'spotify:profile:{other}')       # even with their start pending
    r = client.get(f'/callback?code=c&state=profile_{other}')
    assert r.status_code == 403 and exchange == []


def test_spotify_callback_will_not_bind_the_install_account_for_a_member(client, member, exchange):
    oauth_pending.mark('spotify:global')
    assert client.get('/callback?code=c').status_code == 403


def test_spotify_callback_binds_your_own_profile(client, member, exchange):
    r = client.get(f'/callback?code=c&state=profile_{member}')
    assert r.status_code == 200 and exchange == [member]


def test_a_cookieless_callback_needs_a_started_flow(client, exchange):
    other = _new_profile()
    _new_profile()           # several profiles: no session is nobody
    with client.session_transaction() as sess:
        sess.pop('profile_id', None)   # the provider sent the browser to another host
    r = client.get(f'/callback?code=c&state=profile_{other}')
    assert r.status_code == 403 and exchange == []
    oauth_pending.mark(f'spotify:profile:{other}')
    r = client.get(f'/callback?code=c&state=profile_{other}')
    assert r.status_code == 200 and exchange == [other]


def test_a_started_flow_is_used_once():
    oauth_pending.clear()
    oauth_pending.mark('deezer:global', now=100.0)
    assert oauth_pending.consume('deezer:global', now=101.0)
    assert not oauth_pending.consume('deezer:global', now=102.0)
    oauth_pending.mark('deezer:global', now=100.0)
    assert not oauth_pending.consume('deezer:global', now=100.0 + oauth_pending.TTL_SECONDS + 1)


def test_deezer_callback_refuses_a_member(client, member):
    oauth_pending.mark('deezer:global')
    assert client.get('/deezer/callback?code=c').status_code == 403


class _NoTidal:
    client_id = None


def test_tidal_connect_is_yours_or_the_admins(client, member, monkeypatch):
    import core.tidal_client as tc
    monkeypatch.setattr(tc, 'TidalClient', _NoTidal)
    other = _new_profile()
    assert client.get('/auth/tidal').status_code == 403
    assert client.get(f'/auth/tidal?profile_id={other}').status_code == 403
    assert client.get(f'/auth/tidal?profile_id={member}').status_code == 400  # past the gate


def test_a_profiles_tidal_connect_never_writes_the_install_tokens(monkeypatch):
    saved_global, saved_profile = [], []

    class _Tidal:
        def __init__(self):
            self.access_token = self.refresh_token = None
            self.redirect_uri = ''

        def _save_tokens(self):
            saved_global.append(True)

        def fetch_token_from_code(self, code):
            self.access_token, self.refresh_token = 'a', 'r'
            self._save_tokens()
            return True

    monkeypatch.setattr(web_server, 'TidalClient', _Tidal)
    monkeypatch.setattr(web_server, 'clear_profile_tidal_client', lambda pid: None)
    monkeypatch.setattr(type(_db()), 'set_profile_tidal_tokens',
                        lambda self, pid, a, r: saved_profile.append(pid) or True)
    monkeypatch.setitem(web_server.tidal_oauth_state, 'profile_id', '42')
    monkeypatch.setitem(web_server.tidal_oauth_state, 'code_verifier', 'v')
    monkeypatch.setitem(web_server.tidal_oauth_state, 'code_challenge', 'c')
    assert web_server._finish_tidal_auth('code') == 'profile'
    assert saved_profile == [42] and saved_global == []


# ── S12: live log lines go to the subscribed admins only ─────────────────────

def test_live_log_lines_go_to_the_log_room_only(monkeypatch):
    sent = []
    monkeypatch.setattr(web_server.socketio, 'emit', lambda *a, **k: sent.append((a, k)))
    web_server._push_live_log_lines(['a line'], 'app')
    assert sent and all(k.get('to') == 'logs:live' for _a, k in sent)


# ── S13: art from a url is never fetched from a private address ──────────────

@pytest.mark.parametrize('url', ['http://127.0.0.1/a.jpg', 'http://169.254.169.254/latest',
                                 'http://10.0.0.5/cover.jpg', 'file:///etc/passwd'])
def test_the_art_fetch_refuses_a_private_address(client, admin, url):
    r = client.post('/api/album/1/art', json={'url': url})
    assert r.status_code == 400
    assert 'public' in r.get_json()['error']


# ── S18: a jellyfin or plex identity is a real, unclaimed user ───────────────

class _Jellyfin:
    def get_available_users(self):
        return [{'id': 'abc-1', 'name': 'Kid'}, {'id': 'def-2', 'name': 'Teen'}]


class _Engine:
    def client(self, name):
        return _Jellyfin() if name == 'jellyfin' else None


@pytest.fixture
def jellyfin(monkeypatch):
    import api.user_profiles as up
    monkeypatch.setattr(up, '_media_server_engine', lambda: _Engine())


def _save_jf(client, user_id):
    return client.post('/api/profiles/me/server-library',
                       json={'server_type': 'jellyfin', 'user_id': user_id})


def test_a_jellyfin_user_must_exist(client, member, jellyfin):
    assert _save_jf(client, 'made-up').status_code == 400


def test_a_jellyfin_user_another_profile_is_cannot_be_claimed(client, member, jellyfin):
    other = _new_profile()
    _db().set_profile_server_library(other, 'jellyfin', None, 'abc1')
    assert _save_jf(client, 'ABC-1').status_code == 409
    assert _save_jf(client, 'def-2').status_code == 200
    assert _db().get_profile_server_library(member)['jellyfin_user_id'] == 'def-2'


def test_a_plex_home_user_another_profile_is_cannot_be_claimed(client, member):
    other = _new_profile()
    _db().set_profile_plex_home_user(other, '77', 'Kid', 'tok')
    r = client.post('/api/profiles/me/plex-home-user', json={'user_id': '77'})
    assert r.status_code == 409


# ── S21: fail closed ─────────────────────────────────────────────────────────

def test_an_unreadable_profile_cannot_download():
    def boom(pid):
        raise RuntimeError('db locked')
    assert download_denied_reason(5, boom) == PROFILE_UNREADABLE


def test_the_strict_getter_turns_a_swallowed_read_error_into_a_raise(monkeypatch):
    class _Db:
        def get_profile(self, pid, raise_errors=False):
            if raise_errors:
                raise RuntimeError('db locked')
            return None
    assert download_denied_reason(5, strict_profile_getter(lambda: _Db())) == PROFILE_UNREADABLE

    class _Plain:   # a stand-in without the flag still answers
        def get_profile(self, pid):
            return {'id': pid, 'can_download': False}
    assert download_denied_reason(5, strict_profile_getter(lambda: _Plain())) == DOWNLOADS_OFF


def test_a_download_with_an_unreadable_profile_says_try_again(client, member, monkeypatch):
    real = type(_db()).get_profile

    def flaky(self, pid, raise_errors=False):
        if raise_errors:
            raise RuntimeError('db locked')
        return real(self, pid)
    monkeypatch.setattr(type(_db()), 'get_profile', flaky)
    r = client.post('/api/playlists/pl1/start-missing-process', json={'tracks': [{'name': 'x'}]})
    assert r.status_code == 503
    assert r.get_json()['error'] == PROFILE_UNREADABLE


def _context(path, pid):
    from flask import g, session
    ctx = web_server.app.test_request_context(path)
    ctx.push()
    session['profile_id'] = pid
    rv = web_server._set_profile_context()
    return ctx, rv, g


def test_a_failed_profile_check_fails_closed(member, monkeypatch):
    import core.permissions as perms

    def boom(*a, **k):
        raise RuntimeError('broken')
    monkeypatch.setattr(perms, 'page_denied', boom)
    ctx, rv, g = _context('/api/library/anything', member)
    try:
        assert rv is None
        assert g.can_download is False and g.is_admin is False and g.allowed_sides == 'none'
    finally:
        ctx.pop()
    ctx, rv, g = _context('/api/sample/stems', member)   # a page-only api
    try:
        assert rv is not None and rv[1] == 403
    finally:
        ctx.pop()


def test_a_turned_off_profile_is_signed_out_on_the_profile_routes_too():
    pid = _new_profile()
    _db().update_profile(pid, disabled=1)
    ctx, rv, g = _context('/api/profiles/current', pid)
    try:
        assert g.profile_id is None and g.can_download is False
    finally:
        ctx.pop()


def test_an_unreadable_request_limit_refuses_the_ask(monkeypatch):
    pid = _new_profile(can_download=False)

    def boom(*a, **k):
        raise RuntimeError('db locked')
    monkeypatch.setattr(type(_db()), '_music_request_quota_blocks', boom)
    out = _db().add_to_wishlist_detailed(
        {'id': f'trk{os.urandom(3).hex()}', 'name': 'Song', 'artists': [{'name': 'Band'}],
         'album': {'name': 'Record'}},
        source_type='manual', profile_id=pid, user_initiated=True)
    assert out['status'] == 'rejected'
    assert 'request limit' in out['reason']


# ── S22: a member sees their own activity and downloads ──────────────────────

def test_the_activity_feed_shows_a_member_their_own(client, member, monkeypatch):
    feed = [{'title': 'mine', 'profile_id': member, 'timestamp': 0},
            {'title': 'admins', 'profile_id': 1, 'timestamp': 0},
            {'title': 'system', 'profile_id': None, 'timestamp': 0}]
    monkeypatch.setattr(web_server, 'activity_feed', feed)
    titles = [a['title'] for a in client.get('/api/activity/feed').get_json()['activities']]
    assert titles == ['mine']
    logs = client.get('/api/logs').get_json()['logs']
    assert len(logs) == 1 and 'mine' in logs[0]


def test_the_admin_sees_all_activity(client, admin, monkeypatch):
    feed = [{'title': 'mine', 'profile_id': 5, 'timestamp': 0},
            {'title': 'system', 'profile_id': None, 'timestamp': 0}]
    monkeypatch.setattr(web_server, 'activity_feed', feed)
    assert len(client.get('/api/activity/feed').get_json()['activities']) == 2


def test_an_activity_toast_goes_to_admins_and_its_owner(monkeypatch):
    sent = []
    monkeypatch.setattr(web_server.socketio, 'emit', lambda ev, data, **k: sent.append(k.get('to')))
    web_server._emit_activity_toast('dashboard:toast', {'profile_id': 9})
    web_server._emit_activity_toast('dashboard:toast', {'profile_id': None})
    assert sent == ['role:admin', 'member:9', 'role:admin']


@pytest.fixture
def batches(monkeypatch, member):
    other = _new_profile()
    monkeypatch.setitem(web_server.download_batches, 'b-mine', {'profile_id': member, 'queue': ['t-mine']})
    monkeypatch.setitem(web_server.download_batches, 'b-theirs', {'profile_id': other, 'queue': ['t-theirs']})
    monkeypatch.setitem(web_server.download_tasks, 't-mine',
                        {'batch_id': 'b-mine', 'status': 'queued', 'track_info': {'name': 'Mine'}})
    monkeypatch.setitem(web_server.download_tasks, 't-theirs',
                        {'batch_id': 'b-theirs', 'status': 'queued', 'track_info': {'name': 'Theirs'}})
    return member, other


def test_the_downloads_page_shows_a_member_their_own(client, batches):
    body = client.get('/api/downloads/all').get_json()
    assert {d['batch_id'] for d in body['downloads']} == {'b-mine'}
    assert {b['batch_id'] for b in body['batches']} == {'b-mine'}
    assert client.get('/api/downloads/batch-history').get_json()['history'] == []


def test_another_profiles_batch_status_is_not_there(client, batches):
    assert client.get('/api/playlists/b-theirs/download_status').status_code == 404
    procs = client.get('/api/active-processes').get_json()['active_processes']
    assert {p.get('batch_id') for p in procs if p['type'] == 'batch'} <= {'b-mine'}


def test_the_admin_sees_every_download(client, batches):
    _login(client, 1)
    body = client.get('/api/downloads/all').get_json()
    assert {'b-mine', 'b-theirs'} <= {d['batch_id'] for d in body['downloads']}


def test_batch_rooms_are_the_owners_and_the_admins():
    assert web_server._batch_visible_to({'profile_id': 4}, 4, False)
    assert not web_server._batch_visible_to({'profile_id': 4}, 5, False)
    assert not web_server._batch_visible_to({}, 5, False)
    assert web_server._batch_visible_to({'profile_id': 4}, 5, True)


class _Importer:
    def __init__(self):
        self.started = []

    def start_import(self, username=None, full=False):
        self.started.append(username)
        return {'status': 'started'}


def test_a_member_runs_the_shared_import_without_rewriting_its_account(client, member, monkeypatch):
    import api.stats as stats
    worker = _Importer()
    monkeypatch.setattr(stats, '_import_target', lambda *a: ('shared', worker))
    monkeypatch.setattr(stats, '_lastfm_import_worker', lambda: worker)
    written = []
    monkeypatch.setattr(stats.config_manager, 'set', lambda k, v: written.append(k))
    monkeypatch.setattr(stats.config_manager, 'get',
                        lambda k, d=None: 'house' if k == 'lastfm.username' else d)
    r = client.post('/api/lastfm/listening-import/run', json={'username': 'someone-else', 'enabled': True})
    assert r.status_code == 403 and worker.started == []
    r = client.post('/api/lastfm/listening-import/run', json={'username': 'house', 'enabled': True})
    assert r.status_code == 200 and worker.started == ['house']
    assert written == []
