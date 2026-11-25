"""people and permissions follow-ups, nov 18 (#2188 #2189 #2190 #2191).

#2188  audiobooks and podcasts have Ask first: a wish or an episode becomes a
       request on the Requests page, and an approval does what a download
       would have (the book goes on their wishlist, the episode starts).
#2189  a policy held at Ask first holds a template that says download at Ask
       first everywhere it counts: the map, the routes, the wishlist pass.
#2190  a personal change that says what its template says isn't one.
#2191  chat's live push only reaches profiles that have Chat & Arcade.

driven through web_server.app with real sessions, like
tests/test_permissions_enforcement.py. every test starts from the open
policy and an empty request table (the shared test database outlives a
test, and an earlier one's leftovers must never decide this one).
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-nov18-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'nov18.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')

from core import permission_store as PS  # noqa: E402
from core import permissions as P  # noqa: E402
from core.audiobook_database import AudiobookDatabase  # noqa: E402
from tests.test_audiobook_wishlist_worker import _book  # noqa: E402

APP = web_server.app


def _db():
    return web_server.get_database()


def _clear_requests():
    db = _db()
    for r in db.list_media_requests(limit=10000):
        db.delete_media_request(r['id'])


@pytest.fixture(autouse=True)
def _clean():
    """the open policy and no requests, before and after"""
    PS.change_policy(_db(), {'reset': 'all'})
    _clear_requests()
    yield
    PS.change_policy(_db(), {'reset': 'all'})
    _clear_requests()


@pytest.fixture
def made():
    pids = []

    def make(template='adult', **changes):
        db = _db()
        pid = db.create_profile(name='n18_%s' % uuid4().hex[:8])
        tid = next(t['id'] for t in db.list_permission_templates() if t['slug'] == template)
        PS.change_profile(db, db.get_profile(pid), {'template_id': tid, 'set': changes})
        pids.append(pid)
        return pid
    yield make
    for pid in pids:
        try:
            _db().delete_profile(pid)
        except Exception:  # noqa: BLE001
            pass


def _as(pid):
    c = APP.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


@pytest.fixture
def books(tmp_path):
    """a throwaway audiobook database and catalogue for every path that reads them"""
    adb = AudiobookDatabase(str(tmp_path / 'audiobooks.db'))
    catalog = MagicMock()
    catalog.get_book.return_value = MagicMock(to_dict=lambda: _book())
    with patch('api.audiobooks.get_audiobook_db', return_value=adb), \
         patch('core.audiobook_database.get_audiobook_db', return_value=adb), \
         patch('api.audiobooks.get_audiobook_client', return_value=catalog):
        yield adb
    adb.close()


EPISODE = {'enclosure_url': 'https://cdn.example/ep12.mp3', 'title': 'Episode 12', 'guid': 'ep-12',
           'show_title': 'Some Show', 'feed_url': 'https://feeds.example/show.xml'}


# ── #2188: the catalogue offers it ───────────────────────────────────────

def test_books_and_episodes_offer_ask_first():
    for pid in ('books.get', 'podcasts.get'):
        assert P.CATALOGUE[pid].values == P.MODES
        assert P.coerce(pid, 'ask') == 'ask'
    rows = {r['id']: r for r in P.catalogue_payload()['rows']}
    assert set(rows['podcasts.get']['value_labels']) == {'none', 'ask', 'download'}
    # the policy clamps a download template to ask, like music and video
    eff = P.effective({'books.get': 'ask', 'podcasts.get': 'ask'}, P.TEMPLATE_DEFAULTS, {}, {'id': 50})
    assert eff['books.get'] == 'ask' and eff['podcasts.get'] == 'ask'


# ── #2188: a book ask ────────────────────────────────────────────────────

def test_an_ask_first_book_wish_is_a_request_not_a_wish(made, books):
    pid = made(**{'books.get': 'ask'})
    r = _as(pid).post('/api/audiobooks/wishlist', json={'asin': 'B1'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['requested'] is True and r.get_json()['wishlisted'] is False
    assert books.get_wishlist(pid) == [], 'nothing reaches the wishlist before an admin says yes'
    waiting = _db().list_media_requests(profile_id=pid, status='pending')
    assert [(w['kind'], w['item_key']) for w in waiting] == [('audiobook', 'B1')]
    # asking again doesn't file a second one
    again = _as(pid).post('/api/audiobooks/wishlist', json={'asin': 'B1'}).get_json()
    assert again['already'] is True
    assert len(_db().list_media_requests(profile_id=pid, status='pending')) == 1
    # the wishlist page knows it's waiting
    assert _as(pid).get('/api/audiobooks/wishlist').get_json()['requested'] == ['B1']


def test_a_book_profile_that_cannot_request_is_still_refused(made, books):
    pid = made(**{'books.get': 'none'})
    r = _as(pid).post('/api/audiobooks/wishlist', json={'asin': 'B1'})
    assert r.status_code == 403 and r.get_json()['permission'] == 'books.get'
    assert _db().list_media_requests(profile_id=pid) == []


def test_approving_a_book_puts_it_on_their_wishlist_and_the_pass_may_grab_it(made, books):
    from core.requests.media import may_get_book
    pid = made(**{'books.get': 'ask'})
    _as(pid).post('/api/audiobooks/wishlist', json={'asin': 'B1'})
    rid = _db().list_media_requests(profile_id=pid)[0]['id']
    assert may_get_book(pid, 'B1') is False, 'an ask first owner gets nothing unapproved'

    listed = _as(1).get('/api/requests/music').get_json()
    assert [m['id'] for m in listed['pending_media']] == [rid]
    assert 'payload' not in listed['pending_media'][0]

    r = _as(1).post('/api/requests/media/%d/approve' % rid)
    assert r.status_code == 200, r.get_json()
    assert [b['asin'] for b in books.get_wishlist(pid)] == ['B1']
    assert _db().get_media_request(rid)['status'] == 'approved'
    assert may_get_book(pid, 'B1') is True
    assert may_get_book(pid, 'B2') is False, 'only the book that was approved'
    # turning it off after the approval counts, like a download switched off
    db = _db()
    PS.change_profile(db, db.get_profile(pid), {'set': {'books.get': 'none'}})
    assert may_get_book(pid, 'B1') is False


def test_a_member_cannot_approve_their_own_ask_and_can_withdraw_it(made, books):
    pid = made(**{'books.get': 'ask'})
    _as(pid).post('/api/audiobooks/wishlist', json={'asin': 'B1'})
    rid = _db().list_media_requests(profile_id=pid)[0]['id']
    r = _as(pid).post('/api/requests/media/%d/approve' % rid)
    assert r.status_code == 403
    other = made(**{'books.get': 'ask'})
    assert _as(other).post('/api/requests/media/%d/withdraw' % rid).status_code == 403
    assert _as(pid).post('/api/requests/media/%d/withdraw' % rid).status_code == 200
    assert _db().get_media_request(rid) is None


def test_declining_ends_it_and_they_see_why(made, books):
    pid = made(**{'books.get': 'ask'})
    _as(pid).post('/api/audiobooks/wishlist', json={'asin': 'B1'})
    rid = _db().list_media_requests(profile_id=pid)[0]['id']
    assert _as(1).post('/api/requests/media/%d/decline' % rid, json={'response': 'not this one'}).status_code == 200
    mine = _as(pid).get('/api/requests/music').get_json()
    assert mine['pending_media'] == []
    assert [(m['status'], m['admin_response']) for m in mine['media_history']] == [('declined', 'not this one')]
    assert books.get_wishlist(pid) == []
    # a member's badge counts the answer until they look
    assert _as(pid).get('/api/requests/music/counts').get_json()['updates'] >= 1


# ── #2188: an episode ask ────────────────────────────────────────────────

def test_an_ask_first_episode_is_a_request_and_approval_starts_it(made):
    pid = made(**{'podcasts.get': 'ask'})
    with patch('api.podcasts.queue_podcast_download') as queue:
        r = _as(pid).post('/api/podcasts/download', json=dict(EPISODE, extra='dropped'))
        assert r.status_code == 200 and r.get_json()['requested'] is True
        queue.assert_not_called()
    req = _db().list_media_requests(profile_id=pid, status='pending')[0]
    assert (req['kind'], req['item_key'], req['subtitle']) == ('podcast', 'ep-12', 'Some Show')
    assert 'extra' not in req['payload']
    with patch('api.podcasts.queue_podcast_download', return_value={'success': True}) as queue:
        assert _as(1).post('/api/requests/media/%d/approve' % req['id']).status_code == 200
        queue.assert_called_once()
        assert queue.call_args[0][0]['enclosure_url'] == EPISODE['enclosure_url']
    assert _db().get_media_request(req['id'])['status'] == 'approved'


def test_an_episode_profile_that_downloads_still_downloads(made):
    pid = made()
    with patch('api.podcasts.queue_podcast_download', return_value={'success': True, 'download_id': 'x'}) as q:
        r = _as(pid).post('/api/podcasts/download', json=EPISODE)
        assert r.status_code == 200 and r.get_json().get('requested') is None
        q.assert_called_once()
    assert _db().list_media_requests(profile_id=pid) == []


def test_approve_all_takes_book_and_episode_asks_too(made, books):
    pid = made(**{'books.get': 'ask', 'podcasts.get': 'ask'})
    _as(pid).post('/api/audiobooks/wishlist', json={'asin': 'B1'})
    with patch('api.podcasts.queue_podcast_download'):
        _as(pid).post('/api/podcasts/download', json=EPISODE)
    assert _as(1).get('/api/requests/music/counts').get_json()['pending'] >= 2
    with patch('api.podcasts.queue_podcast_download', return_value={'success': True}):
        out = _as(1).post('/api/requests/music/approve-all', json={}).get_json()
    assert out['approved'] >= 2
    assert {r['status'] for r in _db().list_media_requests(profile_id=pid)} == {'approved'}


# ── #2189: the policy's value is the one that counts ─────────────────────

def test_a_policy_at_ask_first_holds_a_download_template_everywhere(made, books):
    """Adult says download; the policy says Ask first. the map, the
    explanation the editor draws from, and the routes all answer ask."""
    pid = made(**{'movies.access': True})           # adult: download everything
    db = _db()
    PS.change_policy(db, {'set': {'movies.get': 'ask', 'books.get': 'ask', 'music.get': 'ask'}})
    ctx = PS.context_for(db, pid)
    for row in ('movies.get', 'books.get', 'music.get'):
        assert ctx['perms'][row] == 'ask'
        assert P.can(ctx, row, 'ask') and not P.can(ctx, row, 'download')
    rows = {r['id']: r for r in PS.profile_payload(db, db.get_profile(pid))['rows']}
    assert rows['movies.get']['template_value'] == 'download'
    assert rows['movies.get']['value'] == 'ask' and rows['movies.get']['capped'] is True
    # the routes: a book wish files a request, a music download is refused
    r = _as(pid).post('/api/audiobooks/wishlist', json={'asin': 'B1'})
    assert r.status_code == 200 and r.get_json()['requested'] is True
    assert books.get_wishlist(pid) == []
    r = _as(pid).post('/api/download', json={'username': 'u', 'filename': 'f.flac'})
    assert r.status_code == 403


# ── #2190: a change that matches its template isn't one ──────────────────

def test_a_change_equal_to_its_template_is_not_reported(made):
    db = _db()
    pid = made('teen', **{'movies.get': 'download'})
    assert PS.profile_payload(db, db.get_profile(pid))['overrides']['movies.get'] == 'download'
    # the template catches up with the change: it's stored, but says nothing
    teen = next(t for t in db.list_permission_templates() if t['slug'] == 'teen')
    PS.change_template(db, teen['id'], {'perms': {'movies.get': 'download'}})
    try:
        out = PS.profile_payload(db, db.get_profile(pid))
        assert 'movies.get' not in out['overrides']
        assert not next(r for r in out['rows'] if r['id'] == 'movies.get')['override']
        assert out['effective']['movies.get'] == 'download'
    finally:
        PS.change_template(db, teen['id'], {'perms': {'movies.get': 'ask'}})


# ── #2191: chat's push reaches only profiles with chat ───────────────────

def test_only_profiles_with_chat_join_its_socket_room(made):
    assert web_server._ws_profile_may_chat(made()) is True
    assert web_server._ws_profile_may_chat(made(**{'chat.use': False})) is False
    db = _db()
    with_chat = made()
    PS.change_policy(db, {'set': {'chat.use': False}})
    assert web_server._ws_profile_may_chat(with_chat) is False, 'the policy turns it off for everyone'
    assert web_server._ws_profile_may_chat(999999) is False, 'a profile that is gone fails closed'


def test_chat_push_names_the_chat_room():
    import inspect
    src = inspect.getsource(web_server)
    for event in ('chat:room_message', 'chat:room_protocol', 'chat:unread'):
        at = src.index("socketio.emit('%s'" % event)
        call = src[at:src.index(')', src.index('}', at)) + 1]
        assert 'to=CHAT_SOCKET_ROOM' in call, event


# ── #2192: one set of words ──────────────────────────────────────────────

def test_the_rows_use_one_set_of_words_and_refusals_still_read():
    labels = {pid: P.CATALOGUE[pid].label for pid in P.STORED_IDS}
    assert labels['movies.get'] == 'Movie requests'
    assert labels['tv.get'] == 'TV show requests'
    assert labels['music.get'] == 'Music requests'
    assert labels['books.get'] == 'Audiobook requests'
    assert labels['podcasts.get'] == 'Podcast episode requests'
    assert labels['video.dashboard'] == 'Dashboard'
    assert labels['movies.max_rating'] == labels['tv.max_rating'] == 'Maximum content rating'
    assert {labels['%s.quality' % k] for k in ('movies', 'tv', 'music')} == {'Allowed quality profiles'}
    assert {labels['%s.quality_default' % k] for k in ('movies', 'tv', 'music')} == {'Default quality profile'}
    assert P.MODE_LABELS == {'download': 'Auto-approve', 'ask': 'Require approval', 'none': 'Requests off'}
    # a refusal still says what they can't do, in a sentence
    assert P.refusal_body('movies.get')['message'].startswith("Getting movies isn't part of your profile")
    assert P.refusal_body('books.access')['message'].startswith("Audiobooks isn't part of your profile")
