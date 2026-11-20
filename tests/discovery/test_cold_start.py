"""a new profile gets a filled discover: chart picks with their reason while it
has no taste, and "pick artists you love" seeds it right away through the
same similar-artist path the watchlist feeds."""
from __future__ import annotations

import json

import pytest
from flask import Flask

import api.discover_routes as routes
from core.discovery import cold_start
from core.discovery.explain import explanation
from database.music_database import MusicDatabase


class _Cfg:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _artist(i, name, fans=1000):
    return {'id': i, 'name': name, 'picture_big': f'https://img/{i}.jpg', 'nb_fan': fans}


class _Deezer:
    """deezer's public api, by endpoint."""

    def __init__(self):
        self.calls = []
        self.charts = {
            0: [_artist(1, 'Chart Star'), _artist(2, 'Owned Already')],
            132: [_artist(10, 'Pop One'), _artist(11, 'Pop Two')],
            116: [_artist(20, 'Rap One')],
        }
        self.related = {'500': [_artist(501, 'Like Picked A'), _artist(502, 'Like Picked B')]}

    def _api_get(self, endpoint, params=None, use_token=True):
        self.calls.append((endpoint, use_token))
        if endpoint.startswith('chart/'):
            return {'data': self.charts.get(int(endpoint.split('/')[1]), [])}
        if endpoint == 'search/artist':
            q = params['q'].lower()
            pool = [a for rows in self.charts.values() for a in rows] + [_artist(500, 'Picked Artist')]
            return {'data': [a for a in pool if q in a['name'].lower()]}
        if endpoint.endswith('/related'):
            return {'data': self.related.get(endpoint.split('/')[1], [])}
        return {}


def _play(db, artist, n, profile_id=1):
    conn = db._get_connection()
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO listening_history (track_id, title, artist, album, played_at, duration_ms,"
                " server_source, profile_id) VALUES (?,?,?,?,?,?,?,?)",
                (f't-{artist}-{i}', f'song {i}', artist, 'album', '2026-11-01 12:00:00', 180000,
                 'soulsync_web', profile_id))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean():
    cold_start._reset_for_tests()
    yield
    cold_start._reset_for_tests()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    deezer = _Deezer()
    who = {"id": 1}
    monkeypatch.setattr(routes, "get_database", lambda: db)
    monkeypatch.setattr(routes, "config_manager", _Cfg())
    monkeypatch.setattr(routes, "get_current_profile_id", lambda: who["id"])
    monkeypatch.setattr(routes, "_get_active_discovery_source", lambda: 'deezer')
    monkeypatch.setattr(routes, "_get_deezer_client", lambda: deezer)
    monkeypatch.setattr(routes, "_beatport_cached_home", lambda: None)
    monkeypatch.setattr(routes, "_profile_dial", lambda: 0.0)
    routes.invalidate_discover_shelf_cache()
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return db, deezer, who, app.test_client()


# ── seeds ────────────────────────────────────────────────────────────────────

def test_picks_are_cleaned_deduped_and_newest_first():
    merged = cold_start.merge_picks(
        [{'name': 'Old', 'deezer_id': '1'}],
        [{'name': 'New', 'deezer_id': '2', 'image_url': 'javascript:x'},
         {'name': 'new', 'deezer_id': '2'}, {'name': ''}, {'name': 'Old', 'deezer_id': '1'}])
    assert [p['name'] for p in merged] == ['New', 'Old']
    assert merged[0]['image_url'] is None              # only http(s) or same-origin art
    assert cold_start.clean_pick({'name': 'X', 'id': 'not-a-number'})['deezer_id'] is None


def test_a_profile_is_new_until_it_has_five_seeds(env):
    db, _deezer, _who, _http = env
    assert cold_start.is_new_profile(db, 1)
    _play(db, 'Once Only', 1)                          # one play is a click, not a taste
    for name in ('A', 'B', 'C'):
        _play(db, name, 2)
    assert len(cold_start.taste_seed_names(db, 1)) == 3
    assert cold_start.is_new_profile(db, 1)
    cold_start.save_picks(db, 1, [{'name': 'D', 'deezer_id': '4'}, {'name': 'E', 'deezer_id': '5'}])
    assert not cold_start.is_new_profile(db, 1)


def test_watchlist_artists_count_as_seeds(env):
    db, _deezer, _who, _http = env
    for i in range(5):
        db.add_artist_to_watchlist(str(100 + i), f'Watched {i}', profile_id=1, source='deezer')
    assert not cold_start.is_new_profile(db, 1)


def test_picks_are_per_profile(env):
    db, _deezer, _who, _http = env
    other = db.create_profile("Other")
    cold_start.save_picks(db, other, [{'name': 'Mine', 'deezer_id': '9'}])
    assert cold_start.load_picks(db, 1) == []
    assert [p['name'] for p in cold_start.load_picks(db, other)] == ['Mine']


# ── picked artists feed the normal seed path ─────────────────────────────────

def _edges(db, source_id, names, profile_id=1):
    db.replace_similar_artists(source_id, [
        {'similar_artist_name': n, 'similar_artist_deezer_id': str(900 + i), 'similarity_rank': i + 1}
        for i, n in enumerate(names)], profile_id=profile_id, source_provider='deezer')


def test_a_picked_artist_is_a_chosen_source_for_recommendations(env):
    db, _deezer, _who, _http = env
    _edges(db, '500', ['Like Picked A', 'Like Picked B'])
    # before the pick the edges trace back to nothing chosen: a browse, not a taste
    assert db.get_top_similar_artists(limit=10, profile_id=1) == []
    cold_start.save_picks(db, 1, [{'name': 'Picked Artist', 'deezer_id': '500'}])
    names = {a.similar_artist_name for a in db.get_top_similar_artists(limit=10, profile_id=1)}
    assert names == {'Like Picked A', 'Like Picked B'}


def test_another_profiles_pick_does_not_seed_mine(env):
    db, _deezer, _who, _http = env
    other = db.create_profile("Other")
    _edges(db, '500', ['Like Picked A'])
    cold_start.save_picks(db, other, [{'name': 'Picked Artist', 'deezer_id': '500'}])
    assert db.get_top_similar_artists(limit=10, profile_id=1) == []


def test_the_build_stores_each_picks_similar_artists_and_reports_progress(env):
    db, deezer, _who, _http = env
    seen = []
    picks = [{'name': 'Picked Artist', 'deezer_id': '500'}, {'name': 'No Id', 'deezer_id': None}]
    cold_start.save_picks(db, 1, picks)
    state = cold_start.build_for_picks(
        db, 1, cold_start.load_picks(db, 1),
        lambda pick: cold_start.related_artists(deezer, pick['deezer_id']),
        on_progress=lambda: seen.append(cold_start.build_status(1)['done']))
    assert state['state'] == 'done' and state['total'] == 1 and state['stored'] == 2
    assert seen == [1]
    assert ('artist/500/related', False) in deezer.calls     # public endpoint, no token


def test_one_failing_pick_does_not_stop_the_rest(env):
    db, _deezer, _who, _http = env

    def fetch(pick):
        if pick['name'] == 'Bad':
            raise RuntimeError('boom')
        return [{'similar_artist_name': 'Fine Similar', 'similarity_rank': 1}]

    picks = [{'name': 'Bad', 'deezer_id': '1'}, {'name': 'Good', 'deezer_id': '2'}]
    state = cold_start.build_for_picks(db, 1, picks, fetch)
    assert state['done'] == 2 and state['stored'] == 1


# ── the starter shelf ────────────────────────────────────────────────────────

def test_a_new_profiles_empty_shelf_fills_from_the_charts_with_its_reason(env, monkeypatch):
    _db, _deezer, _who, http = env
    monkeypatch.setattr(cold_start, 'owned_names', lambda _db, names: {'owned already'})
    body = http.get('/api/discover/similar-artists').get_json()
    assert body['success'] and body['starter'] is True and body['source'] == 'deezer'
    names = [a['artist_name'] for a in body['artists']]
    # the genres take turns, and what the library already has is left out
    assert names[:3] == ['Chart Star', 'Pop One', 'Rap One']
    assert 'Owned Already' not in names
    pop = next(a for a in body['artists'] if a['artist_name'] == 'Pop One')
    assert pop['explanation']['kind'] == 'trending'
    assert pop['explanation']['seeds'][0]['name'] == "Deezer's Pop chart"
    assert pop['genres'] == ['Pop'] and pop['artist_id'] == '10'


def test_a_profile_with_taste_never_gets_chart_filler(env):
    db, _deezer, _who, http = env
    for name in ('A', 'B', 'C', 'D', 'E'):
        _play(db, name, 3)
    body = http.get('/api/discover/similar-artists').get_json()
    assert body['artists'] == [] and 'starter' not in body


def test_the_charts_are_cached_between_shelves(env):
    _db, deezer, _who, _http = env
    cold_start.chart_artists(deezer, 132)
    cold_start.chart_artists(deezer, 132)
    assert [c for c in deezer.calls if c[0] == 'chart/132/artists'] == [('chart/132/artists', False)]


def test_network_charts_and_cached_beatport_join_by_name(env):
    db, deezer, _who, _http = env
    cold_start.sin_chart_artists(db)  # no table yet: nothing, no error
    named = cold_start.beatport_artists({'tracks': [{'artist': 'Pop Two, Someone', 'title': 'x'},
                                                    {'artist': 'Various Artists', 'title': 'y'}]})
    assert named == [('Pop Two', 'Beatport')]
    shelf = cold_start.starter_shelf(db, deezer, beatport_cache={'t': [{'artist': 'Rap One', 'title': 'z'}]})
    first = shelf[0]
    assert first['artist_name'] == 'Rap One'
    assert first['explanation']['seeds'][0]['name'] == 'Beatport'


def test_picked_recommendations_say_because_you_picked(env):
    db, _deezer, _who, http = env
    cold_start.save_picks(db, 1, [{'name': 'Picked Artist', 'deezer_id': '500'}])
    _edges(db, '500', ['Like Picked A'])
    body = http.get('/api/discover/similar-artists').get_json()
    rec = next(a for a in body['artists'] if a['artist_name'] == 'Like Picked A')
    assert rec['explanation']['kind'] == 'picked'
    assert rec['explanation']['seeds'][0]['name'] == 'Picked Artist'


# ── the banner's routes ──────────────────────────────────────────────────────

def test_status_offers_the_banner_until_dismissed(env):
    _db, _deezer, _who, http = env
    body = http.get('/api/discover/starter').get_json()
    assert body['show_banner'] and body['needs_seeds'] and body['threshold'] == 5
    assert body['connections'] == {'listenbrainz': False, 'lastfm': False}
    # no import route registered in this app: the banner doesn't offer one
    assert body['spotify_import'] == {'available': False, 'href': None}
    assert http.post('/api/discover/starter/dismiss').get_json()['success']
    assert http.get('/api/discover/starter').get_json()['show_banner'] is False


def test_dismissing_is_per_profile(env):
    db, _deezer, who, http = env
    other = db.create_profile("Other")
    http.post('/api/discover/starter/dismiss')
    who["id"] = other
    assert http.get('/api/discover/starter').get_json()['show_banner'] is True


def test_the_banner_never_shows_once_a_profile_has_history(env):
    db, _deezer, _who, http = env
    for name in ('A', 'B', 'C', 'D', 'E'):
        _play(db, name, 2)
    assert http.get('/api/discover/starter').get_json()['show_banner'] is False


def test_the_spotify_import_is_offered_once_its_route_exists(env):
    _db, _deezer, _who, _http = env
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    app.add_url_rule('/api/listening/import/spotify-history', 'x', lambda: 'ok', methods=['POST'])
    with app.test_request_context():
        assert routes._spotify_import_offer() == {'available': True, 'href': routes.SPOTIFY_IMPORT_HREF}


def test_admin_connections_come_from_settings(env):
    db, _deezer, _who, _http = env
    cfg = _Cfg(**{'listenbrainz.token': 'tok'})
    assert cold_start.connections(db, 1, cfg.get) == {'listenbrainz': True, 'lastfm': False}


def test_the_picker_lists_a_genres_chart_or_a_search(env):
    _db, _deezer, _who, http = env
    body = http.get('/api/discover/starter/artists?genre=132').get_json()
    assert [a['name'] for a in body['artists']] == ['Pop One', 'Pop Two']
    assert {'id': 132, 'name': 'Pop'} in body['genres']
    found = http.get('/api/discover/starter/artists?q=picked').get_json()
    assert found['artists'][0] == {'name': 'Picked Artist', 'deezer_id': '500',
                                   'image_url': 'https://img/500.jpg',
                                   'popularity': cold_start.popularity_from_fans(1000)}
    # an unknown genre reads as the main chart, never an arbitrary deezer path
    assert http.get('/api/discover/starter/artists?genre=99999').get_json()['artists'][0]['name'] == 'Chart Star'


def test_saving_picks_builds_the_first_recommendations_right_away(env, monkeypatch):
    db, deezer, _who, http = env
    monkeypatch.setattr(routes, '_starter_fetch_similar',
                        lambda pick: cold_start.related_artists(deezer, pick['deezer_id']))
    # run the background build inline so the test can read what it stored
    monkeypatch.setattr(cold_start, 'start_build',
                        lambda database, pid, picks, fetch, on_progress=None:
                        cold_start.build_for_picks(database, pid, picks, fetch, on_progress))
    body = http.post('/api/discover/starter/picks',
                     json={'artists': [{'name': 'Picked Artist', 'deezer_id': '500'}]}).get_json()
    assert body['success'] and body['build']['state'] == 'done' and body['build']['stored'] == 2
    shelf = http.get('/api/discover/similar-artists').get_json()
    assert {a['artist_name'] for a in shelf['artists']} == {'Like Picked A', 'Like Picked B'}
    assert 'starter' not in shelf


def test_saving_nothing_is_refused(env):
    _db, _deezer, _who, http = env
    resp = http.post('/api/discover/starter/picks', json={'artists': [{'name': ''}]})
    assert resp.status_code == 400


def test_picked_is_a_known_explanation_kind():
    assert explanation('picked', ['A'])['kind'] == 'picked'
    assert json.dumps(explanation('trending', ["Deezer's chart"]))
