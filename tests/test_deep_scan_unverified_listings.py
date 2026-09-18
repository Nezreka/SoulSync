"""the deep scan deletes what it does not see, so it has to know the
difference between "the server listed nothing here" and "the server did not
answer".

it did not. navidrome and jellyfin fold a failed getAlbum / Items request into
an empty list, plexapi raises and the worker turned that into "artist updated,
no albums accessible" with zero seen tracks, and a Stop click just broke the
loop. every one of those left the unseen rows in the stale set, and the only
thing between them and DELETE FROM tracks was the 50% guard. one timed-out
getAlbum on the weekly scan took that album's rows, enrichment and all.

these run the real clients (fake transport), the real worker and a real db:
the seams are _make_request and the plexapi-shaped wrapper objects.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.database_update_worker as duw
from core.database_update_worker import DatabaseUpdateWorker
from core.jellyfin_client import JellyfinClient
from core.navidrome_client import NavidromeClient
from database.music_database import MusicDatabase


# ── one library, three servers ─────────────────────────────────────────────
# 2 artists, 5 albums each, 10 tracks each = 100 tracks, all already in the db

ARTISTS = ('ar1', 'ar2')
ALBUMS_PER_ARTIST = 5
TRACKS_PER_ALBUM = 10


def _albums_of(artist):
    return [f"{artist}-al{a}" for a in range(ALBUMS_PER_ARTIST)]


def _tracks_of(album):
    return [f"{album}-t{t}" for t in range(TRACKS_PER_ALBUM)]


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    """a db that already holds the whole library, bound to the worker."""
    path = str(tmp_path / 'music.db')
    db = MusicDatabase(path)
    monkeypatch.setattr('core.database_update_worker.get_database', lambda path=None: db)

    def seed(server):
        with db._get_connection() as conn:
            for ar in ARTISTS:
                conn.execute("INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
                             (ar, f"Artist {ar}", server))
                for al in _albums_of(ar):
                    conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES (?, ?, ?, ?)",
                                 (al, f"Album {al}", ar, server))
                    for n, t in enumerate(_tracks_of(al)):
                        conn.execute(
                            "INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, "
                            "file_path, server_source) VALUES (?, ?, ?, ?, ?, 100, ?, ?)",
                            (t, al, ar, f"Track {n}", n + 1, f"/m/{t}.flac", server))
            conn.commit()
        return db

    return SimpleNamespace(path=path, db=db, seed=seed)


def _remaining(db, server):
    with db._get_connection() as conn:
        rows = conn.execute("SELECT id FROM tracks WHERE server_source = ?", (server,)).fetchall()
    return {r[0] for r in rows}


def _all_track_ids():
    return {t for ar in ARTISTS for al in _albums_of(ar) for t in _tracks_of(al)}


class Server:
    """what the media server has right now, per artist -> album -> tracks.
    edit it to model a deletion on the server side."""

    def __init__(self):
        self.artists = {ar: {al: list(_tracks_of(al)) for al in _albums_of(ar)} for ar in ARTISTS}
        self.failing = set()          # (kind, id): requests that time out
        self.connection_up = True
        self.calls = []


# ── navidrome ──────────────────────────────────────────────────────────────

def _navidrome(server: Server):
    c = NavidromeClient()
    c.base_url = 'http://navidrome'
    c.username = 'u'
    c.password = 'p'
    c._connection_attempted = True
    c.ensure_connection = lambda: server.connection_up

    def fake_request(endpoint, params=None, **kw):
        params = params or {}
        server.calls.append((endpoint, dict(params)))
        if endpoint == 'getArtists':
            c.last_api_error = None
            return {'artists': {'index': [{'artist': [
                {'id': ar, 'name': f"Artist {ar}", 'albumCount': len(albums)}
                for ar, albums in server.artists.items()]}]}}
        if endpoint == 'getArtist':
            ar = params['id']
            if ('artist', ar) in server.failing:
                c.last_api_error = 'request failed: ReadTimeout'
                return None
            return {'artist': {'id': ar, 'name': f"Artist {ar}", 'album': [
                {'id': al, 'name': f"Album {al}", 'artistId': ar} for al in server.artists[ar]]}}
        if endpoint == 'getAlbum':
            al = params['id']
            if ('album', al) in server.failing:
                c.last_api_error = 'request failed: ReadTimeout'
                return None
            ar = al.split('-')[0]
            return {'album': {'id': al, 'name': f"Album {al}", 'song': [
                {'id': t, 'title': f"Track {n}", 'track': n + 1, 'duration': 100, 'path': f"/m/{t}.flac",
                 'suffix': 'flac', 'albumId': al, 'artistId': ar}
                for n, t in enumerate(server.artists[ar][al])]}}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    c._make_request = fake_request
    return c


def _run_navidrome(seeded, server, *, stop_after_artist=None):
    db = seeded.seed('navidrome')
    client = _navidrome(server)
    w = DatabaseUpdateWorker(media_client=client, database_path=seeded.path,
                             server_type='navidrome', force_sequential=True)
    if stop_after_artist is not None:
        real = w._process_artist_with_content

        def stop_after(artist, **kw):
            result = real(artist, **kw)
            if artist.ratingKey == stop_after_artist:
                w.should_stop = True     # the Stop button, mid-run
            return result
        w._process_artist_with_content = stop_after
    w.run_deep_scan()
    return db, w


def test_navidrome_healthy_scan_keeps_everything(seeded):
    db, _ = _run_navidrome(seeded, Server())
    assert _remaining(db, 'navidrome') == _all_track_ids()


def test_navidrome_one_timed_out_getalbum_keeps_that_albums_rows(seeded):
    server = Server()
    server.failing.add(('album', 'ar1-al3'))
    db, w = _run_navidrome(seeded, server)
    assert _remaining(db, 'navidrome') == _all_track_ids(), "a timed out getAlbum is not an empty album"
    assert w.removed_tracks == 0


def test_navidrome_one_timed_out_getartist_keeps_that_artists_rows(seeded):
    server = Server()
    server.failing.add(('artist', 'ar2'))
    db, w = _run_navidrome(seeded, server)
    assert _remaining(db, 'navidrome') == _all_track_ids(), "a timed out getArtist is not an artist with no albums"
    assert w.failed_operations == 1     # and the scan reports it as a failure, not a success


def test_navidrome_connection_dropping_mid_scan_keeps_the_rest(seeded):
    server = Server()
    client_calls = server.calls

    class Flaky(Server):
        pass
    db_seeded = seeded.seed('navidrome')
    client = _navidrome(server)
    # the connection dies after artist ar1 was listed: every later call is refused
    real_request = client._make_request

    def dying(endpoint, params=None, **kw):
        if endpoint == 'getArtist' and params.get('id') == 'ar2':
            server.connection_up = False
        return real_request(endpoint, params, **kw)
    client._make_request = dying
    w = DatabaseUpdateWorker(media_client=client, database_path=seeded.path,
                             server_type='navidrome', force_sequential=True)
    w.run_deep_scan()
    assert _remaining(db_seeded, 'navidrome') == _all_track_ids()
    assert client_calls  # the scan did run


def test_navidrome_stop_mid_scan_removes_nothing(seeded):
    db, w = _run_navidrome(seeded, Server(), stop_after_artist='ar1')
    assert _remaining(db, 'navidrome') == _all_track_ids(), "unscanned is not gone"
    assert w.removed_tracks == 0


# the guard must not turn stale removal off. these are real deletions on the
# server and the scan has to keep catching them.

def test_navidrome_album_deleted_on_server_is_still_removed(seeded):
    server = Server()
    del server.artists['ar1']['ar1-al3']
    db, w = _run_navidrome(seeded, server)
    assert _remaining(db, 'navidrome') == _all_track_ids() - set(_tracks_of('ar1-al3'))
    assert w.removed_tracks == TRACKS_PER_ALBUM


def test_navidrome_track_deleted_on_server_is_still_removed(seeded):
    server = Server()
    server.artists['ar1']['ar1-al3'].remove('ar1-al3-t9')
    db, w = _run_navidrome(seeded, server)
    assert _remaining(db, 'navidrome') == _all_track_ids() - {'ar1-al3-t9'}


def test_navidrome_artist_deleted_on_server_is_still_removed(seeded):
    server = Server()
    del server.artists['ar2']
    db, w = _run_navidrome(seeded, server)
    assert _remaining(db, 'navidrome') == {t for al in _albums_of('ar1') for t in _tracks_of(al)}


def test_navidrome_a_failure_elsewhere_does_not_shield_a_real_deletion(seeded):
    """the fence is per scope: ar2-al0 timing out keeps ar2-al0, and ar1-al3
    deleted on the server still goes."""
    server = Server()
    server.failing.add(('album', 'ar2-al0'))
    del server.artists['ar1']['ar1-al3']
    db, _ = _run_navidrome(seeded, server)
    assert _remaining(db, 'navidrome') == _all_track_ids() - set(_tracks_of('ar1-al3'))


# ── jellyfin ───────────────────────────────────────────────────────────────

def _jellyfin(server: Server, *, bulk_fail_after_pages=None, bulk='ok'):
    """``bulk``: 'ok' serves the bulk track + album fetches the deep scan
    starts with; 'tracks_fail' refuses every bulk track page so albums are
    fetched one at a time; 'all_fail' refuses both so artists are too. the
    deep scan clears the client cache before it starts, so the only way to
    reach the per-item fetchers is to make the bulk ones fail."""
    c = JellyfinClient()
    c.base_url = 'http://jellyfin'
    c.api_key = 'k'
    c.user_id = 'u'
    c.music_library_id = 'lib'
    c._connection_attempted = True
    c.ensure_connection = lambda: server.connection_up
    bulk_pages = {'tracks': 0}

    def _track_item(al, n, t):
        return {'Id': t, 'Name': f"Track {n}", 'IndexNumber': n + 1, 'RunTimeTicks': 100 * 10000,
                'AlbumId': al, 'ArtistItems': [{'Id': al.split('-')[0]}], 'Path': f"/m/{t}.flac",
                'MediaSources': [{'Bitrate': 900000, 'Size': 1000}]}

    def _album_item(ar, al):
        return {'Id': al, 'Name': f"Album {al}", 'AlbumArtists': [{'Id': ar, 'Name': f"Artist {ar}"}]}

    def _page(items, params):
        start = int(params.get('StartIndex', 0))
        limit = int(params.get('Limit', len(items) or 1))
        return {'Items': items[start:start + limit], 'TotalRecordCount': len(items)}

    def fake_request(endpoint, params=None, **kw):
        params = params or {}
        server.calls.append((endpoint, dict(params)))
        if endpoint == '/Artists/AlbumArtists':
            return {'Items': [{'Id': ar, 'Name': f"Artist {ar}"} for ar in server.artists]}
        if endpoint == '/Users/u/Items':
            kind = params.get('IncludeItemTypes')
            if params.get('ParentId') == 'lib':         # the bulk fetches
                if kind == 'Audio':
                    bulk_pages['tracks'] += 1
                    if bulk in ('tracks_fail', 'all_fail'):
                        return None
                    if bulk_fail_after_pages is not None and bulk_pages['tracks'] > bulk_fail_after_pages:
                        return None
                    items = [_track_item(al, n, t) for ar in server.artists for al in server.artists[ar]
                             for n, t in enumerate(server.artists[ar][al])]
                    return _page(items, params)
                if bulk == 'all_fail':
                    return None
                items = [_album_item(ar, al) for ar in server.artists for al in server.artists[ar]]
                return _page(items, params)
            if kind == 'MusicAlbum':                    # albums of one artist
                ar = params['ArtistIds']
                if ('artist', ar) in server.failing:
                    return None
                return _page([_album_item(ar, al) for al in server.artists[ar]], params)
            if kind == 'Audio':                         # tracks of one album
                al = params['ParentId']
                if ('album', al) in server.failing:
                    return None
                ar = al.split('-')[0]
                return _page([_track_item(al, n, t) for n, t in enumerate(server.artists[ar][al])], params)
        raise AssertionError(f"unexpected request {endpoint} {params}")

    c._make_request = fake_request
    return c


def _run_jellyfin(seeded, server, monkeypatch=None, **client_kw):
    db = seeded.seed('jellyfin')
    client = _jellyfin(server, **client_kw)
    if monkeypatch is not None:
        import core.jellyfin_client as jc
        monkeypatch.setattr(jc.time, 'sleep', lambda s: None)   # the bulk retry wait
    w = DatabaseUpdateWorker(media_client=client, database_path=seeded.path,
                             server_type='jellyfin', force_sequential=True)
    w.run_deep_scan()
    return db, w, client


def _per_item_calls(server, kind):
    """the per-album ('Audio') or per-artist ('MusicAlbum') Items requests"""
    return [c for c in server.calls if c[0] == '/Users/u/Items'
            and c[1].get('IncludeItemTypes') == kind and c[1].get('ParentId') != 'lib']


def test_jellyfin_healthy_scan_keeps_everything(seeded):
    db, _, _ = _run_jellyfin(seeded, Server())
    assert _remaining(db, 'jellyfin') == _all_track_ids()


def test_jellyfin_failed_per_album_request_keeps_that_albums_rows(seeded, monkeypatch):
    server = Server()
    server.failing.add(('album', 'ar1-al3'))
    db, w, _ = _run_jellyfin(seeded, server, monkeypatch, bulk='tracks_fail')
    assert _per_item_calls(server, 'Audio'), "the per-album fetcher was never reached"
    assert _remaining(db, 'jellyfin') == _all_track_ids()
    assert w.removed_tracks == 0


def test_jellyfin_failed_per_artist_request_keeps_that_artists_rows(seeded, monkeypatch):
    server = Server()
    server.failing.add(('artist', 'ar2'))
    db, w, _ = _run_jellyfin(seeded, server, monkeypatch, bulk='all_fail')
    assert _per_item_calls(server, 'MusicAlbum'), "the per-artist fetcher was never reached"
    assert _remaining(db, 'jellyfin') == _all_track_ids()


def test_jellyfin_per_item_deletions_are_still_removed(seeded, monkeypatch):
    """the per-item path still removes what is really gone"""
    server = Server()
    del server.artists['ar1']['ar1-al3']
    server.artists['ar2']['ar2-al0'].remove('ar2-al0-t0')
    db, w, _ = _run_jellyfin(seeded, server, monkeypatch, bulk='all_fail')
    assert _per_item_calls(server, 'MusicAlbum') and _per_item_calls(server, 'Audio')
    assert _remaining(db, 'jellyfin') == _all_track_ids() - set(_tracks_of('ar1-al3')) - {'ar2-al0-t0'}


def test_jellyfin_abandoned_bulk_fetch_is_not_used_as_a_cache(seeded, monkeypatch):
    """the bulk track fetch gives up at the page-size floor after two
    failures. the prefix it gathered would read as albums with half their
    tracks; it must be dropped and the albums fetched one by one."""
    import core.jellyfin_client as jc
    from core.library import bulk_paginate
    # make the bulk fetch page in tens so what it gathers really is a prefix
    real_paginate = bulk_paginate.paginate_all_items
    monkeypatch.setattr(jc, 'paginate_all_items',
                        lambda fetch, **kw: real_paginate(fetch, page_size=10, min_page_size=10, **kw))
    server = Server()
    db, w, client = _run_jellyfin(seeded, server, monkeypatch, bulk_fail_after_pages=3)
    bulk_track_pages = [c for c in server.calls if c[1].get('ParentId') == 'lib' and c[1].get('IncludeItemTypes') == 'Audio']
    assert len(bulk_track_pages) >= 4, "the bulk fetch never reached the failing page"
    assert _remaining(db, 'jellyfin') == _all_track_ids()
    per_album = {c[1]['ParentId'] for c in server.calls
                 if c[0] == '/Users/u/Items' and c[1].get('IncludeItemTypes') == 'Audio' and c[1].get('ParentId') != 'lib'}
    # every album, not just the ones past the failure: the prefix was dropped whole
    assert per_album == {al for ar in ARTISTS for al in _albums_of(ar)}, \
        "albums were not all fetched one at a time after the bulk fetch was abandoned"


def test_jellyfin_stop_mid_scan_removes_nothing(seeded):
    """navidrome's identity-repair step already refused to run on a stopped
    scan, so the stop guard is new protection for jellyfin and plex"""
    db = seeded.seed('jellyfin')
    w = DatabaseUpdateWorker(media_client=_jellyfin(Server()), database_path=seeded.path,
                             server_type='jellyfin', force_sequential=True)
    real = w._process_artist_with_content

    def stop_after(artist, **kw):
        result = real(artist, **kw)
        if artist.ratingKey == 'ar1':
            w.should_stop = True
        return result
    w._process_artist_with_content = stop_after
    w.run_deep_scan()
    assert _remaining(db, 'jellyfin') == _all_track_ids(), "unscanned is not gone"


def test_jellyfin_album_deleted_on_server_is_still_removed(seeded):
    server = Server()
    del server.artists['ar1']['ar1-al3']
    db, w, _ = _run_jellyfin(seeded, server)
    assert _remaining(db, 'jellyfin') == _all_track_ids() - set(_tracks_of('ar1-al3'))


def test_jellyfin_per_item_listings_page_past_the_old_cap(seeded, monkeypatch):
    """an album of 250 tracks used to come back as 100 (one request, Limit
    100) and the other 150 were deleted as stale. same for an artist with
    more than 200 albums. the per-item path is what the deep scan falls back
    to when the bulk fetch is refused, and what every other caller uses."""
    import core.jellyfin_client as jc
    monkeypatch.setattr(jc.time, 'sleep', lambda s: None)
    server = Server()
    big = 'ar1-al0'
    server.artists['ar1'][big] = [f"{big}-t{t}" for t in range(250)]
    for a in range(ALBUMS_PER_ARTIST, 230):          # ar2 has 230 albums
        server.artists['ar2'][f"ar2-al{a}"] = []
    db = seeded.seed('jellyfin')
    with db._get_connection() as conn:
        for n in range(TRACKS_PER_ALBUM, 250):
            t = f"{big}-t{n}"
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path, "
                         "server_source) VALUES (?, ?, 'ar1', ?, ?, 100, ?, 'jellyfin')",
                         (t, big, f"Track {n}", n + 1, f"/m/{t}.flac"))
        conn.commit()
    client = _jellyfin(server, bulk='all_fail')
    w = DatabaseUpdateWorker(media_client=client, database_path=seeded.path,
                             server_type='jellyfin', force_sequential=True)
    w.run_deep_scan()
    big_pages = [c for c in _per_item_calls(server, 'Audio') if c[1].get('ParentId') == big]
    assert len(big_pages) >= 2, "the 250-track album was fetched in one request"
    artist_pages = [c for c in _per_item_calls(server, 'MusicAlbum') if c[1].get('ArtistIds') == 'ar2']
    assert len(artist_pages) >= 2, "the 230-album artist was fetched in one request"
    assert len(_remaining(db, 'jellyfin')) == 100 - TRACKS_PER_ALBUM + 250
    assert w.removed_tracks == 0
    with db._get_connection() as conn:
        albums = conn.execute("SELECT COUNT(*) FROM albums WHERE artist_id = 'ar2'").fetchone()[0]
    assert albums == 230


# ── plex-shaped (plexapi raises) ───────────────────────────────────────────

class _PlexTrack:
    def __init__(self, k, n):
        self.ratingKey, self.title, self.trackNumber, self.duration = k, f"Track {n}", n + 1, 100000
        self.parentIndex = 1


class _PlexAlbum:
    def __init__(self, k, tracks, boom=False):
        self.ratingKey, self.title, self._t, self._boom = k, f"Album {k}", tracks, boom

    def tracks(self):
        if self._boom:
            raise ConnectionError("plex went away")
        return [_PlexTrack(t, n) for n, t in enumerate(self._t)]


class _PlexArtist:
    def __init__(self, k, albums, boom=False):
        self.ratingKey, self.title, self._a, self._boom = k, f"Artist {k}", albums, boom

    def albums(self):
        if self._boom:
            raise ConnectionError("plex went away")
        return self._a


class _PlexClient:
    def __init__(self, artists):
        self._artists = artists
        self.last_fetch_failed = False

    def ensure_connection(self):
        return True

    def get_all_artists(self):
        return list(self._artists)


def _plex_library(server: Server, *, artist_boom=(), album_boom=()):
    return [_PlexArtist(ar, [_PlexAlbum(al, tracks, boom=al in album_boom)
                              for al, tracks in albums.items()], boom=ar in artist_boom)
            for ar, albums in server.artists.items()]


def test_plex_artist_whose_albums_call_raised_keeps_its_rows(seeded):
    db = seeded.seed('plex')
    w = DatabaseUpdateWorker(media_client=_PlexClient(_plex_library(Server(), artist_boom={'ar2'})),
                             database_path=seeded.path, server_type='plex', force_sequential=True)
    w.run_deep_scan()
    assert _remaining(db, 'plex') == _all_track_ids()


def test_plex_album_whose_tracks_call_raised_keeps_its_rows(seeded):
    db = seeded.seed('plex')
    w = DatabaseUpdateWorker(media_client=_PlexClient(_plex_library(Server(), album_boom={'ar1-al3'})),
                             database_path=seeded.path, server_type='plex', force_sequential=True)
    w.run_deep_scan()
    assert _remaining(db, 'plex') == _all_track_ids()


def test_plex_stop_mid_scan_removes_nothing(seeded):
    db = seeded.seed('plex')
    w = DatabaseUpdateWorker(media_client=_PlexClient(_plex_library(Server())),
                             database_path=seeded.path, server_type='plex', force_sequential=True)
    real = w._process_artist_with_content

    def stop_after(artist, **kw):
        result = real(artist, **kw)
        if artist.ratingKey == 'ar1':
            w.should_stop = True
        return result
    w._process_artist_with_content = stop_after
    w.run_deep_scan()
    assert _remaining(db, 'plex') == _all_track_ids(), "unscanned is not gone"


def test_plex_deleted_album_is_still_removed(seeded):
    server = Server()
    del server.artists['ar1']['ar1-al3']
    db = seeded.seed('plex')
    w = DatabaseUpdateWorker(media_client=_PlexClient(_plex_library(server)),
                             database_path=seeded.path, server_type='plex', force_sequential=True)
    w.run_deep_scan()
    assert _remaining(db, 'plex') == _all_track_ids() - set(_tracks_of('ar1-al3'))


# ── the fence query itself ─────────────────────────────────────────────────

def test_a_failed_fence_query_skips_removal_instead_of_deleting(seeded, monkeypatch):
    """the fence answering None means it could not say which rows to keep. a
    partial fence is no fence."""
    server = Server()
    server.failing.add(('album', 'ar1-al3'))
    del server.artists['ar1']['ar1-al4']          # a real deletion that would normally go
    db = seeded.seed('navidrome')
    monkeypatch.setattr(MusicDatabase, 'get_track_ids_under_scopes', lambda self, *a, **k: None)
    w = DatabaseUpdateWorker(media_client=_navidrome(server), database_path=seeded.path,
                             server_type='navidrome', force_sequential=True)
    w.run_deep_scan()
    assert _remaining(db, 'navidrome') == _all_track_ids()


def test_get_track_ids_under_scopes(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        for ar in ('ar1', 'ar2'):
            conn.execute("INSERT INTO artists (id, name, server_source) VALUES (?, ?, 'navidrome')", (ar, ar))
        for al, ar in (('al1', 'ar1'), ('al2', 'ar1'), ('al3', 'ar2')):
            conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES (?, ?, ?, 'navidrome')", (al, al, ar))
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) VALUES "
                     "('t1', 'al1', 'ar1', 'a', '/a', 'navidrome')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) VALUES "
                     "('t2', 'al2', 'ar1', 'b', '/b', 'navidrome')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) VALUES "
                     "('t3', 'al3', 'ar2', 'c', '/c', 'navidrome')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) VALUES "
                     "('t4', 'al3', 'ar2', 'd', '/d', 'plex')")
        conn.commit()
    assert db.get_track_ids_under_scopes('navidrome', set(), set()) == set()
    assert db.get_track_ids_under_scopes('navidrome', {'ar1'}, set()) == {'t1', 't2'}
    assert db.get_track_ids_under_scopes('navidrome', set(), {'al3'}) == {'t3'}     # not plex's t4
    assert db.get_track_ids_under_scopes('navidrome', {'ar1'}, {'al3'}) == {'t1', 't2', 't3'}


# ── verified_listing ───────────────────────────────────────────────────────

def test_verified_listing_prefers_the_verified_answer():
    obj = SimpleNamespace(albums=lambda: ['stale'], albums_verified=lambda: (['a', 'b'], True))
    assert duw.verified_listing(obj, 'albums') == ['a', 'b']


def test_verified_listing_raises_on_an_unverified_answer():
    obj = SimpleNamespace(albums=lambda: [], albums_verified=lambda: ([], False))
    with pytest.raises(duw.ListingUnavailable):
        duw.verified_listing(obj, 'albums')


def test_verified_listing_treats_a_plain_empty_list_as_an_answer():
    obj = SimpleNamespace(albums=lambda: [])
    assert duw.verified_listing(obj, 'albums') == []


def test_verified_listing_wraps_a_raise():
    def boom():
        raise ConnectionError("gone")
    with pytest.raises(duw.ListingUnavailable):
        duw.verified_listing(SimpleNamespace(albums=boom), 'albums')
