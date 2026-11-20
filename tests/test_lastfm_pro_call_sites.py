"""the live last.fm call sites, with a key last.fm refuses (pro only from
1 december 2026): each one falls back, or says plainly why it cannot."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask, g

from core import lastfm_client as lfc
from core import lastfm_fallback as fb
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _refuse(key='k'):
    lfc.note_key_rejected(key, 10, 'Invalid API key')


# ── artist page: most played by name ──

def test_artist_top_tracks_route_names_the_source(monkeypatch):
    web_server = pytest.importorskip('web_server')
    web_server.app.config['TESTING'] = True
    seen = {}

    def fake_top(name, limit=10, *, lastfm=None, **_):
        seen['lastfm'] = lastfm
        return [{'name': 'Lullaby', 'playcount': 9, 'listeners': 1, 'url': ''}], 'listenbrainz'

    monkeypatch.setattr(fb, 'artist_top_tracks', fake_top)
    monkeypatch.setattr(web_server, 'lastfm_worker', None)
    body = web_server.app.test_client().get('/api/artist/0/lastfm-top-tracks?name=Low&limit=5').get_json()
    assert body['success'] is True
    assert body['source'] == 'listenbrainz'
    assert body['tracks'][0]['name'] == 'Lullaby'


def test_artist_top_tracks_route_asks_the_worker_client_first(monkeypatch):
    web_server = pytest.importorskip('web_server')
    worker_client = SimpleNamespace(api_key='k')
    seen = {}

    def fake_top(name, limit=10, *, lastfm=None, **_):
        seen['lastfm'] = lastfm
        return [], ''

    monkeypatch.setattr(fb, 'artist_top_tracks', fake_top)
    monkeypatch.setattr(web_server, 'lastfm_worker', SimpleNamespace(client=worker_client))
    body = web_server.app.test_client().get('/api/artist/0/lastfm-top-tracks?name=Low').get_json()
    assert seen['lastfm'] is worker_client
    assert body == {'success': True, 'tracks': [], 'source': ''}


# ── profile last.fm username check ──

def test_profile_lastfm_check_says_pro_and_points_at_listenbrainz(monkeypatch):
    from api import user_profiles
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, 'get',
                        lambda key, default=None: 'k' if key == 'lastfm.api_key' else default)
    _refuse('k')
    ok, message = user_profiles._check_lastfm_user('someone')
    assert ok is False
    assert message == lfc.LASTFM_IMPORT_REJECTED_MESSAGE
    assert 'ListenBrainz' in message


# ── history import ──

class _ImportConfig:
    def get(self, key, default=None):
        return {'lastfm.api_key': 'k', 'lastfm.username': 'tester'}.get(key, default)

    def set(self, key, value):
        pass


def test_import_refuses_up_front_when_the_key_is_refused(db):
    from core.listening_import.lastfm import LastFMListeningImportWorker
    _refuse('k')
    state = LastFMListeningImportWorker(db, _ImportConfig()).start_import()
    assert state['status'] == 'error'
    assert state['error'] == lfc.LASTFM_IMPORT_REJECTED_MESSAGE


def test_import_does_not_retry_a_refused_key(db, monkeypatch):
    from core.listening_import import lastfm as imp
    worker = imp.LastFMListeningImportWorker(db, _ImportConfig())
    calls = []

    class _Client:
        def get_user_recent_tracks(self, *a, **k):
            calls.append(1)
            raise lfc.LastFMKeyRejected()

    monkeypatch.setattr(worker, '_sleep_retry', lambda s: pytest.fail('retried a refused key'))
    with pytest.raises(lfc.LastFMKeyRejected):
        worker._get_recent_tracks_page(_Client(), 'tester', 1, None)
    assert calls == [1]
    assert imp._safe_error_message(lfc.LastFMKeyRejected()) == lfc.LASTFM_IMPORT_REJECTED_MESSAGE


# ── scrobbling ──

def _scrobble_worker(db):
    from core.listening_stats_worker import ListeningStatsWorker
    cfg = {'lastfm.api_key': 'k', 'lastfm.api_secret': 's', 'lastfm.scrobble_enabled': True,
           'lastfm.session_key': 'sess'}
    return ListeningStatsWorker(db, SimpleNamespace(get=lambda key, default=None: cfg.get(key, default)), None)


def _play(db, title):
    from core.listening_scope import SHARED_OWNER
    conn = db._get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO listening_history (track_id, title, artist, album, played_at, duration_ms,"
            " server_source, profile_id, scrobble_eligible) VALUES (?,?,?,?,?,?,?,?,1)",
            ('t-' + title, title, 'artist', 'album', '2026-11-30 12:00:00', 180000, 'soulsync_web',
             SHARED_OWNER))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _scrobbled(db, row_id):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT scrobbled_lastfm FROM listening_history WHERE id = ?",
                            (row_id,)).fetchone()[0]
    finally:
        conn.close()


def test_a_refused_key_keeps_plays_queued_and_posts_nothing(db, monkeypatch):
    built = []

    class _LFM:
        def __init__(self, *a, **k):
            built.append(1)

        def scrobble_tracks(self, tracks):
            return True

    monkeypatch.setattr(lfc, 'LastFMClient', _LFM)
    row = _play(db, 'song')
    _refuse('k')
    worker = _scrobble_worker(db)
    worker._scrobble_new_events()
    assert built == []
    assert _scrobbled(db, row) == 0
    stats = worker.get_stats()['stats']
    assert stats['lastfm_scrobble_status'] == 'key_rejected'
    assert stats['lastfm_scrobble_message'] == lfc.LASTFM_PRO_MESSAGE

    # the key works again: the queued play goes out
    lfc.reset_key_state()
    worker._scrobble_new_events()
    assert _scrobbled(db, row) == 1
    assert worker.get_stats()['stats']['lastfm_scrobble_status'] == 'ok'


def test_a_refusal_mid_run_stops_the_batches(db, monkeypatch):
    posted = []

    class _LFM:
        def __init__(self, *a, **k):
            pass

        def scrobble_tracks(self, tracks):
            posted.append(len(tracks))
            _refuse('k')
            return False

    monkeypatch.setattr(lfc, 'LastFMClient', _LFM)
    rows = [_play(db, f'song {i}') for i in range(60)]
    _scrobble_worker(db)._scrobble_new_events()
    assert posted == [50]
    assert all(_scrobbled(db, r) == 0 for r in rows)


# ── discover: your artists ──

def test_your_artists_fallback_reads_listenbrainz_then_your_plays(db, monkeypatch):
    from api import discover_routes
    from core import listenbrainz_public
    monkeypatch.setattr(db, 'get_profile_listenbrainz',
                        lambda pid: {'token': 'tok', 'base_url': '', 'username': 'me'})
    monkeypatch.setattr(listenbrainz_public, 'user_top_artists',
                        lambda user, limit=200: [{'name': 'Low', 'playcount': 9, 'mbid': ''}]
                        if user == 'me' else [])
    assert discover_routes._your_top_artists_fallback(db, 2) == ([{'name': 'Low'}], 'listenbrainz')

    # no listenbrainz account: what was played here, three plays or more
    monkeypatch.setattr(db, 'get_profile_listenbrainz',
                        lambda pid: {'token': None, 'base_url': None, 'username': None})
    monkeypatch.setattr(db, 'get_top_artists', lambda **k: [
        {'name': 'Often', 'play_count': 5}, {'name': 'Once', 'play_count': 1}])
    assert discover_routes._your_top_artists_fallback(db, 2) == ([{'name': 'Often'}], 'listening_history')


def test_your_artists_fallback_skips_a_self_hosted_listenbrainz(db, monkeypatch):
    from api import discover_routes
    from core import listenbrainz_public
    monkeypatch.setattr(db, 'get_profile_listenbrainz',
                        lambda pid: {'token': 'tok', 'base_url': 'http://maloja:42010', 'username': 'me'})
    monkeypatch.setattr(listenbrainz_public, 'user_top_artists',
                        lambda *a, **k: pytest.fail('asked the official server for a maloja user'))
    monkeypatch.setattr(db, 'get_top_artists', lambda **k: [])
    assert discover_routes._your_top_artists_fallback(db, 2) == ([], 'listening_history')


# ── seasonal vibes ──

def test_seasonal_tag_chain_runs_without_lastfm(db, monkeypatch):
    from core import seasonal_vibes as sv
    asked = []

    def tag(tag, limit=12, *, lastfm=None):
        asked.append(('tag', lastfm))
        return [{'name': 'Fresh Face'}], 'deezer'

    def top(name, limit=2, *, lastfm=None, free_sources=()):
        asked.append(('top', free_sources))
        return [{'name': 'Summer Song'}], 'deezer'

    monkeypatch.setattr(fb, 'tag_top_artists', tag)
    monkeypatch.setattr(fb, 'artist_top_tracks', top)
    monkeypatch.setattr(sv, '_pool_art_for_artist', lambda database, name: 'https://f.jpg')
    rows = sv.lastfm_tag_tracks(db, None, 'summer', limit=1)
    assert [(r['artist_name'], r['track_name']) for r in rows] == [('Fresh Face', 'Summer Song')]
    # deezer's tag artists take deezer's top tracks, no musicbrainz lookups
    assert ('top', ('deezer',)) in asked


# ── chat radio ──

@pytest.fixture()
def radio(db, monkeypatch):
    import api.chat as chat_api
    monkeypatch.setattr(chat_api, '_db', lambda: db)
    chat_api.configure(client_getter=lambda: None, run_async=lambda v: v,
                       config_get=lambda k, d=None: d)
    app = Flask(__name__)

    @app.before_request
    def _admin():
        g.is_admin = True

    app.register_blueprint(chat_api.create_blueprint())
    yield app.test_client()
    chat_api.configure(client_getter=lambda: None, run_async=lambda v: v,
                       config_get=lambda k, d=None: d)


def test_chat_radio_without_lastfm_plays_deezers_neighbour(radio, monkeypatch):
    monkeypatch.setattr(fb, 'similar_tracks', lambda a, t, limit=30, lastfm=None: (
        [{'name': 'Glory Box', 'artist': 'Portishead', 'match': 1.0}], 'deezer'))
    body = radio.post('/api/chat/jukebox/radio', json={'title': 'Massive Attack - Teardrop'}).get_json()
    assert body == {'query': 'Portishead Glory Box', 'why': 'similar to Teardrop'}


def test_chat_radio_vibe_without_lastfm_uses_deezer_tag_artists(radio, monkeypatch):
    monkeypatch.setattr(fb, 'tag_top_artists', lambda tag, limit=40, lastfm=None: (
        [{'name': 'Slowdive'}], 'deezer'))
    body = radio.post('/api/chat/jukebox/radio', json={'title': 'shoegaze mix'}).get_json()
    assert body['query'] == 'Slowdive'


# ── last.fm radio (discover) ──

def test_radio_generate_falls_back_to_deezer_and_says_so(monkeypatch):
    web_server = pytest.importorskip('web_server')
    from api import listenbrainz_routes
    saved = {}

    class _Manager:
        def save_lastfm_radio_playlist(self, track, artist, similar, source='lastfm'):
            saved['source'] = source
            return 'lastfm_radio_abc'

    monkeypatch.setattr(web_server, 'lastfm_worker', SimpleNamespace(client=SimpleNamespace(api_key='k')))
    monkeypatch.setattr(listenbrainz_routes, '_get_profile_lb_manager', lambda: (_Manager(), 'u', 'lb'))
    monkeypatch.setattr(fb, 'similar_tracks', lambda a, t, limit=25, lastfm=None: (
        [{'name': 'Glory Box', 'artist': 'Portishead', 'match': 1.0, 'mbid': ''}], 'deezer'))
    body = web_server.app.test_client().post(
        '/api/lastfm/radio/generate', json={'track_name': 'Teardrop', 'artist_name': 'Massive Attack'}).get_json()
    assert body['success'] is True
    assert body['title'] == 'Deezer Radio: Teardrop by Massive Attack'
    assert saved['source'] == 'deezer'
    states = listenbrainz_routes.listenbrainz_playlist_states
    for key in [k for k in states if str(k).endswith(':lastfm_radio_abc')]:
        states.pop(key, None)


def test_radio_playlist_title_names_the_source():
    from core.listenbrainz_manager import radio_playlist_title
    assert radio_playlist_title('T', 'A') == 'Last.fm Radio: T by A'
    assert radio_playlist_title('T', 'A', 'deezer') == 'Deezer Radio: T by A'


def test_lastfm_configured_reports_a_refused_key(monkeypatch):
    web_server = pytest.importorskip('web_server')
    monkeypatch.setattr(web_server, 'lastfm_worker', SimpleNamespace(client=SimpleNamespace(api_key='k')))
    _refuse('k')
    body = web_server.app.test_client().get('/api/lastfm/configured').get_json()
    assert body['configured'] is True
    assert body['usable'] is False
    assert body['key_rejected'] is True
    assert body['message'] == lfc.LASTFM_PRO_MESSAGE
