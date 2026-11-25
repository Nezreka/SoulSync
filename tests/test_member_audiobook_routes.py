"""who may do what on the audiobook routes (#2198 #2201).

a member on Require approval for books saw Search now, per-book Search and
Find releases on their wishlist, and the library page showed them file paths,
download provenance, catalogue matching, Delete from disk and the folder scan.
hiding the buttons is not enough: every route declares its gate here, a member
is refused the acquisition and admin routes, and their library read comes back
without the plumbing. driven through web_server.app with a member's session,
the way test_member_enrichment_workers does.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-abook-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'abook.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')

from core import audiobook_database as ADB  # noqa: E402
from core import permission_store as PS  # noqa: E402
from core.permissions import declared_access  # noqa: E402

APP = web_server.app

ACCESS = ('perm', 'books.access')
GET = ('perm', 'books.get')
ADMIN = ('admin', None)

# every /api/audiobooks route and the gate it declares. a route added later
# fails test_no_audiobook_route_is_left_out until someone decides its gate.
ROUTES = {
    ('GET', '/api/audiobooks/search'): ACCESS,
    ('GET', '/api/audiobooks/book/<asin>'): ACCESS,
    ('GET', '/api/audiobooks/similar/<asin>'): ACCESS,
    ('GET', '/api/audiobooks/series'): ACCESS,
    ('GET', '/api/audiobooks/author'): ACCESS,
    ('GET', '/api/audiobooks/narrator'): ACCESS,
    ('GET', '/api/audiobooks/person'): ACCESS,
    ('GET', '/api/audiobooks/browse'): ACCESS,
    ('GET', '/api/audiobooks/bestsellers'): ACCESS,
    ('GET', '/api/audiobooks/new-releases'): ACCESS,
    ('GET', '/api/audiobooks/categories'): ACCESS,
    ('GET', '/api/audiobooks/home'): ACCESS,
    ('GET', '/api/audiobooks/sample-proxy'): ACCESS,
    # their own wishlist and follows (adding one asks or downloads in the body)
    ('GET', '/api/audiobooks/wishlist'): ACCESS,
    ('POST', '/api/audiobooks/wishlist'): ACCESS,
    ('DELETE', '/api/audiobooks/wishlist'): ACCESS,
    ('PATCH', '/api/audiobooks/wishlist/<asin>'): ACCESS,
    ('DELETE', '/api/audiobooks/wishlist/<asin>'): ACCESS,
    ('GET', '/api/audiobooks/watchlist'): ACCESS,
    ('POST', '/api/audiobooks/watchlist'): ACCESS,
    ('PATCH', '/api/audiobooks/watchlist/<path:name>'): ACCESS,
    ('DELETE', '/api/audiobooks/watchlist/<path:name>'): ACCESS,
    ('GET', '/api/audiobooks/blocklist'): ACCESS,
    ('DELETE', '/api/audiobooks/blocklist'): ACCESS,
    ('DELETE', '/api/audiobooks/blocklist/<path:key>'): ACCESS,
    # the shelf (trimmed for a member) and its covers
    ('GET', '/api/audiobooks/library'): ACCESS,
    ('GET', '/api/audiobooks/library/<asin>/cover'): ACCESS,
    # searching indexers, grabbing, the download list: getting books right away
    ('POST', '/api/audiobooks/wishlist/search'): GET,
    ('POST', '/api/audiobooks/wishlist/<asin>/search'): GET,
    ('POST', '/api/audiobooks/watchlist/scan'): GET,
    ('GET', '/api/audiobooks/releases/<asin>'): GET,
    ('POST', '/api/audiobooks/releases/<asin>/start'): GET,
    ('GET', '/api/audiobooks/releases/poll'): GET,
    ('DELETE', '/api/audiobooks/releases/poll'): GET,
    ('POST', '/api/audiobooks/releases/contents'): GET,
    ('POST', '/api/audiobooks/blocklist'): GET,
    ('POST', '/api/audiobooks/grab'): GET,
    ('POST', '/api/audiobooks/adopt'): GET,
    ('GET', '/api/audiobooks/downloads'): GET,
    # the files on shared storage
    ('GET', '/api/audiobooks/library/<asin>/matches'): ADMIN,
    ('PATCH', '/api/audiobooks/library/<asin>/match'): ADMIN,
    ('DELETE', '/api/audiobooks/library/<asin>'): ADMIN,
    ('GET', '/api/audiobooks/library/recycle'): ADMIN,
    ('DELETE', '/api/audiobooks/library/recycle'): ADMIN,
    ('POST', '/api/audiobooks/library/recycle/<path:name>'): ADMIN,
    ('DELETE', '/api/audiobooks/library/recycle/<path:name>'): ADMIN,
}


def _db():
    return web_server.get_database()


def _make_member(**perms):
    db = _db()
    pid = db.create_profile(name='ab_%s' % uuid4().hex[:8])
    tid = next(t['id'] for t in db.list_permission_templates() if t['slug'] == 'adult')
    PS.change_profile(db, db.get_profile(pid), {'template_id': tid, 'set': perms})
    return pid


@pytest.fixture
def asker():
    """the reporter's member: books on Require approval"""
    pid = _make_member(**{'books.get': 'ask'})
    yield pid
    try:
        _db().delete_profile(pid)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
def getter():
    """a member who gets books right away: still not an admin"""
    pid = _make_member(**{'books.get': 'download'})
    yield pid
    try:
        _db().delete_profile(pid)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    """a private audiobook database with one downloaded book on it"""
    adb = ADB.AudiobookDatabase(str(tmp_path / 'audiobooks.db'))
    monkeypatch.setattr(ADB, '_default_db', adb)
    adb.add_to_library(
        {'asin': 'B08G9PRS1K', 'title': 'Project Hail Mary', 'author_names': ['Andy Weir'],
         'narrator_names': ['Ray Porter'], 'series': [{'title': 'Standalone', 'sequence': '1'}],
         'runtime_minutes': 970, 'cover_url': 'https://example.invalid/c.jpg'},
        '/app/audiobooks/Andy Weir/[2021] Project Hail Mary',
        file_count=1, size_bytes=883_000_000, audio_format='m4b', download_id='dl-1',
        file_paths=['/app/audiobooks/Andy Weir/[2021] Project Hail Mary/book.m4b'],
        grouping='Grouped by book title', metadata_json={'asin': 'B08G9PRS1K'},
    )
    yield adb
    adb.close()


def _as(pid):
    c = APP.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


def _call(c, method, url):
    return getattr(c, method.lower())(url, json={})


# ── the declarations ─────────────────────────────────────────────────────

def _routes():
    for r in APP.url_map.iter_rules():
        if r.rule.startswith('/api/audiobooks/'):
            for m in (r.methods or ()) - {'HEAD', 'OPTIONS'}:
                yield m, r.rule, APP.view_functions[r.endpoint]


def test_no_audiobook_route_is_left_out():
    """a route added later has to say who it's for"""
    seen = set()
    for m, rule, _view in _routes():
        assert (m, rule) in ROUTES, 'undeclared audiobook route %s %s: add it to ROUTES' % (m, rule)
        seen.add((m, rule))
    assert seen == set(ROUTES), 'listed but gone: %s' % sorted(set(ROUTES) - seen)


def test_every_audiobook_route_declares_its_gate():
    for m, rule, view in _routes():
        assert declared_access(view) == ROUTES[(m, rule)], '%s %s' % (m, rule)


# ── a member on Require approval (#2198) ─────────────────────────────────

ACQUIRE = (
    ('POST', '/api/audiobooks/wishlist/search'),
    ('POST', '/api/audiobooks/wishlist/B08G9PRS1K/search'),
    ('POST', '/api/audiobooks/watchlist/scan'),
    ('GET', '/api/audiobooks/releases/B08G9PRS1K'),
    ('POST', '/api/audiobooks/releases/B08G9PRS1K/start'),
    ('GET', '/api/audiobooks/releases/poll?id=x'),
    ('DELETE', '/api/audiobooks/releases/poll?id=x'),
    ('POST', '/api/audiobooks/releases/contents'),
    ('POST', '/api/audiobooks/blocklist'),
    ('POST', '/api/audiobooks/grab'),
    ('POST', '/api/audiobooks/adopt'),
    ('GET', '/api/audiobooks/downloads'),
)
ADMIN_CALLS = (
    ('GET', '/api/audiobooks/library/B08G9PRS1K/matches'),
    ('PATCH', '/api/audiobooks/library/B08G9PRS1K/match'),
    ('DELETE', '/api/audiobooks/library/B08G9PRS1K'),
    ('GET', '/api/audiobooks/library/recycle'),
    ('DELETE', '/api/audiobooks/library/recycle'),
    ('POST', '/api/audiobooks/library/recycle/x'),
    ('DELETE', '/api/audiobooks/library/recycle/x'),
)


@pytest.mark.parametrize('method,url', ACQUIRE)
def test_an_asking_member_is_refused_every_search_and_grab(asker, shelf, method, url):
    assert _call(_as(asker), method, url).status_code == 403


@pytest.mark.parametrize('method,url', ADMIN_CALLS)
def test_a_member_is_refused_the_library_files(asker, shelf, method, url):
    assert _call(_as(asker), method, url).status_code == 403


@pytest.mark.parametrize('method,url', ADMIN_CALLS)
def test_a_downloading_member_is_still_refused_the_library_files(getter, shelf, method, url):
    assert _call(_as(getter), method, url).status_code == 403
    # and the book is still there
    assert shelf.get_library_entry('B08G9PRS1K') is not None


def test_a_downloading_member_passes_the_acquisition_gate(getter, shelf):
    # an unknown search id is a 404 past the gate, not a refusal
    assert _as(getter).get('/api/audiobooks/releases/poll?id=nope').status_code == 404
    assert _as(getter).get('/api/audiobooks/downloads').status_code == 200


def test_an_asking_member_keeps_their_wishlist(asker, shelf):
    r = _as(asker).get('/api/audiobooks/wishlist')
    assert r.status_code == 200
    assert 'items' in r.get_json()


# ── the library read (#2201) ─────────────────────────────────────────────

def test_a_members_library_is_the_shelf_not_the_plumbing(asker, shelf):
    body = _as(asker).get('/api/audiobooks/library').get_json()
    assert body['success'] is True
    assert set(body) == {'success', 'books'}            # no root, scan or size
    (book,) = body['books']
    assert set(book) == {'asin', 'title', 'author', 'narrator', 'series_title', 'series_sequence',
                         'runtime_minutes', 'imported_at', 'catalog_asin', 'cover_url'}
    assert book['title'] == 'Project Hail Mary' and book['narrator'] == 'Ray Porter'
    assert book['catalog_asin'] == 'B08G9PRS1K'          # the link to its page
    flat = repr(body)
    for leaked in ('/app/audiobooks', 'dl-1', 'm4b', 'Grouped by', '883000000'):
        assert leaked not in flat, leaked


def test_an_unsettled_match_gives_a_member_no_catalogue_id(asker, shelf):
    shelf.add_to_library({'asin': 'local:abc', 'title': 'Home Rip'}, '/app/audiobooks/rip',
                         source='scan')
    books = {b['asin']: b for b in _as(asker).get('/api/audiobooks/library').get_json()['books']}
    assert books['local:abc']['catalog_asin'] == ''
    assert books['local:abc']['cover_url'].endswith('/api/audiobooks/library/local:abc/cover')


def test_the_admin_library_is_unchanged(shelf):
    body = _as(1).get('/api/audiobooks/library').get_json()
    assert {'root', 'scan', 'total_bytes'} <= set(body)
    (book,) = body['books']
    assert book['path'].startswith('/app/audiobooks/')
    assert 'download' in book and book['match_status'] == 'identifier'
    assert book['file_paths'] and book['grouping']


def test_the_admin_still_reaches_the_library_files(shelf):
    c = _as(1)
    assert c.get('/api/audiobooks/library/recycle').status_code == 200
    assert c.get('/api/audiobooks/releases/poll?id=nope').status_code == 404


# ── podcasts: paths and the shared queue ─────────────────────────────────

def test_podcast_cancel_queued_is_the_admins():
    for r in APP.url_map.iter_rules():
        if r.rule == '/api/podcasts/downloads/cancel-queued':
            assert declared_access(APP.view_functions[r.endpoint]) == ADMIN
            break
    else:
        raise AssertionError('no cancel-queued route')


def test_a_member_is_refused_cancelling_everyones_podcasts(asker):
    assert _as(asker).post('/api/podcasts/downloads/cancel-queued', json={}).status_code == 403


def test_a_member_reads_podcast_downloads_without_paths(asker, monkeypatch):
    import api.podcasts as POD
    monkeypatch.setitem(POD._downloads, 'x1', {'id': 'x1', 'title': 'Ep', 'status': 'completed',
                                              'file_path': '/data/podcasts/show/ep.mp3',
                                              'started_at': 1})
    mine = _as(asker).get('/api/podcasts/downloads').get_json()['downloads']
    row = next(d for d in mine if d['id'] == 'x1')
    assert 'file_path' not in row and row['status'] == 'completed'
    admin = _as(1).get('/api/podcasts/downloads').get_json()['downloads']
    assert next(d for d in admin if d['id'] == 'x1')['file_path'] == '/data/podcasts/show/ep.mp3'


def test_a_members_show_page_says_downloaded_without_the_path(asker, monkeypatch):
    import api.podcasts as POD

    class _Db:
        def get_downloaded_podcast_episodes(self, **_k):
            return [{'enclosure_url': 'https://e.invalid/1.mp3', 'file_path': '/data/p/1.mp3'}]

    monkeypatch.setattr(POD, '_db', lambda: _Db())
    show = {'episodes': [{'enclosure_url': 'https://e.invalid/1.mp3'}]}
    with APP.test_request_context('/api/podcasts/show'):
        from flask import session
        session['profile_id'] = asker
        ep = POD._with_downloaded_flags(show, 'https://e.invalid/feed')['episodes'][0]
    assert ep['downloaded'] is True and 'file_path' not in ep
    with APP.test_request_context('/api/podcasts/show'):
        ep = POD._with_downloaded_flags(show, 'https://e.invalid/feed')['episodes'][0]
    assert ep['file_path'] == '/data/p/1.mp3'
