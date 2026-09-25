"""kids profiles on the music side: hide_explicit through the real web_server app.

the kid is a real profile row (hide_explicit=1, max_rating='PG') with a real
browser session. library rows are real rows in the test db.
"""

from __future__ import annotations

import json
import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-kidsmusic-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'kidsmusic.db'))
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


def _client_as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


@pytest.fixture
def kid():
    db = web_server.get_database()
    pid = db.create_profile(name=f'kid_{uuid4().hex[:8]}')
    db.update_profile(pid, hide_explicit=1, max_rating='PG')
    yield pid
    db.delete_profile(pid)


@pytest.fixture
def member():
    db = web_server.get_database()
    pid = db.create_profile(name=f'member_{uuid4().hex[:8]}')
    yield pid
    db.delete_profile(pid)


@pytest.fixture
def tracks():
    """three library tracks: explicit, clean, unknown, plus one clean track
    on an explicit album."""
    db = web_server.get_database()
    base = 900000 + (uuid4().int % 50000) * 10
    ar, al_clean, al_expl = base, base + 1, base + 2
    rows = {
        'explicit': (base + 3, al_clean, 1, f'/music/kids_{base}/A/01 explicit.flac'),
        'clean': (base + 4, al_clean, 0, f'/music/kids_{base}/A/02 clean.flac'),
        'unknown': (base + 5, al_clean, None, f'/music/kids_{base}/A/03 unknown.flac'),
        'on_explicit_album': (base + 6, al_expl, 0, f'/music/kids_{base}/B/01 clean.flac'),
    }
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (?, ?)", (ar, f'Artist {base}'))
        conn.execute("INSERT INTO albums (id, artist_id, title, explicit) VALUES (?, ?, 'A', 0)",
                     (al_clean, ar))
        conn.execute("INSERT INTO albums (id, artist_id, title, explicit) VALUES (?, ?, 'B', 1)",
                     (al_expl, ar))
        for tid, alb, expl, fp in rows.values():
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, explicit) "
                         "VALUES (?, ?, ?, 't', ?, ?)", (tid, alb, ar, fp, expl))
        conn.commit()
    yield {k: {'id': v[0], 'file_path': v[3]} for k, v in rows.items()}
    with db._get_connection() as conn:
        conn.execute("DELETE FROM tracks WHERE artist_id = ?", (ar,))
        conn.execute("DELETE FROM albums WHERE artist_id = ?", (ar,))
        conn.execute("DELETE FROM artists WHERE id = ?", (ar,))
        conn.commit()


@pytest.fixture(autouse=True)
def _no_server_stream(monkeypatch):
    # a missing file must fall to a plain 404, never a media-server call
    monkeypatch.setattr(web_server, '_build_library_stream_url', lambda *a, **k: None)


# ── hard block: play / stream ────────────────────────────────────────────────

def test_kid_cannot_play_or_stream_an_explicit_track(kid, tracks):
    c = _client_as(kid)
    for key in ('explicit', 'on_explicit_album'):
        t = tracks[key]
        r = c.post('/api/library/play', json={'file_path': t['file_path'], 'track_id': t['id']})
        assert r.status_code == 403, key
        assert r.get_json() == {'success': False, 'error': 'restricted', 'restricted': True}
        r = c.get('/stream/library-audio', query_string={'path': t['file_path']})
        assert r.status_code == 403, key


def test_a_rerooted_path_does_not_walk_around_the_block(kid, tracks):
    c = _client_as(kid)
    local = '/some/other/mount/A/01 explicit.flac'
    assert c.get('/stream/library-audio', query_string={'path': local}).status_code == 403


def test_kid_can_play_clean_and_unknown_tracks(kid, tracks):
    c = _client_as(kid)
    for key in ('clean', 'unknown'):
        t = tracks[key]
        # past the guard to the route's own "file not found" answer
        r = c.post('/api/library/play', json={'file_path': t['file_path'], 'track_id': t['id']})
        assert r.status_code == 404, key
        r = c.get('/stream/library-audio', query_string={'path': t['file_path']})
        assert r.status_code == 404, key


def test_unrestricted_profiles_play_explicit(member, tracks):
    t = tracks['explicit']
    for c in (_client_as(member), _client_as(1)):
        r = c.post('/api/library/play', json={'file_path': t['file_path'], 'track_id': t['id']})
        assert r.status_code == 404


# ── filtered lists ───────────────────────────────────────────────────────────

def test_album_tracklist_drops_explicit_for_the_kid(kid, member, monkeypatch):
    import core.metadata_service as ms
    payload = {'success': True, 'album': {'name': 'X', 'explicit': False},
               'tracks': [{'name': 'a', 'explicit': True}, {'name': 'b', 'explicit': False},
                          {'name': 'c'}]}
    monkeypatch.setattr(ms, 'get_artist_album_tracks', lambda *a, **k: json.loads(json.dumps(payload)))
    kid_names = [t['name'] for t in _client_as(kid).get('/api/album/x1/tracks').get_json()['tracks']]
    assert kid_names == ['b', 'c']
    mem_names = [t['name'] for t in _client_as(member).get('/api/album/x1/tracks').get_json()['tracks']]
    assert mem_names == ['a', 'b', 'c']
    # an explicit album shows the kid no tracks
    payload['album']['explicit'] = True
    assert _client_as(kid).get('/api/album/x1/tracks').get_json()['tracks'] == []


def test_enhanced_artist_view_drops_explicit_albums_and_tracks(kid, member, monkeypatch):
    db = web_server.get_database()
    detail = {'success': True, 'artist': {'id': 1, 'name': 'A'}, 'albums': [
        {'id': 1, 'title': 'Clean', 'explicit': 0,
         'tracks': [{'id': 1, 'explicit': 1}, {'id': 2, 'explicit': 0}, {'id': 3, 'explicit': None}]},
        {'id': 2, 'title': 'Dirty', 'explicit': 1, 'tracks': [{'id': 4, 'explicit': 0}]},
    ]}
    monkeypatch.setattr(type(db), 'get_artist_full_detail',
                        lambda self, aid: json.loads(json.dumps(detail)))
    d = _client_as(kid).get('/api/library/artist/1/enhanced').get_json()
    assert [a['title'] for a in d['albums']] == ['Clean']
    assert [t['id'] for t in d['albums'][0]['tracks']] == [2, 3]
    d = _client_as(member).get('/api/library/artist/1/enhanced').get_json()
    assert [a['title'] for a in d['albums']] == ['Clean', 'Dirty']
    assert len(d['albums'][0]['tracks']) == 3


def test_enhanced_search_drops_explicit_results(kid, member, monkeypatch):
    orch = web_server._search_orchestrator
    resp = {'db_artists': [], 'spotify_artists': [],
            'spotify_albums': [{'id': 'a1', 'explicit': True}, {'id': 'a2', 'explicit': None}],
            'spotify_tracks': [{'id': 't1', 'explicit': True}, {'id': 't2', 'explicit': False}],
            'spotify_playlists': [], 'primary_source': 'deezer'}
    monkeypatch.setattr(web_server, '_build_search_deps', lambda: None)
    monkeypatch.setattr(web_server, '_get_cached_enhanced_search_response', lambda k: None)
    monkeypatch.setattr(web_server, '_set_cached_enhanced_search_response', lambda k, v: None)
    monkeypatch.setattr(orch, 'run_enhanced_search', lambda q, s, d: json.loads(json.dumps(resp)))
    d = _client_as(kid).post('/api/enhanced-search', json={'query': 'song'}).get_json()
    assert [x['id'] for x in d['spotify_tracks']] == ['t2']
    assert [x['id'] for x in d['spotify_albums']] == ['a2']
    d = _client_as(member).post('/api/enhanced-search', json={'query': 'song'}).get_json()
    assert len(d['spotify_tracks']) == 2 and len(d['spotify_albums']) == 2


def test_source_search_stream_drops_explicit_rows(kid, member, monkeypatch):
    orch = web_server._search_orchestrator
    source = sorted(s for s in orch.VALID_STREAM_SOURCES if s not in ('youtube_videos', 'spotify'))[0]

    def _stream(name, q, client, prefer_free=False):
        yield json.dumps({'type': 'tracks', 'data': [{'id': 't1', 'explicit': True},
                                                     {'id': 't2', 'explicit': False}]}) + '\n'
        yield json.dumps({'type': 'albums', 'data': [{'id': 'a1', 'explicit': 1}]}) + '\n'
        yield json.dumps({'type': 'done'}) + '\n'
    monkeypatch.setattr(web_server, '_build_search_deps', lambda: None)
    monkeypatch.setattr(orch, 'resolve_client', lambda name, deps: (object(), True))
    monkeypatch.setattr(orch, 'stream_metadata_source', _stream)

    def _lines(c):
        r = c.post(f'/api/enhanced-search/source/{source}', json={'query': 'song'})
        return {o['type']: o.get('data') for o in
                (json.loads(x) for x in r.get_data(as_text=True).splitlines() if x.strip())}
    k = _lines(_client_as(kid))
    assert [x['id'] for x in k['tracks']] == ['t2'] and k['albums'] == [] and 'done' in k
    m = _lines(_client_as(member))
    assert len(m['tracks']) == 2 and len(m['albums']) == 1
