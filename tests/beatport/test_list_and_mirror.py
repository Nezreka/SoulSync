"""every beatport list opens as a playlist: fetched by ref, identified, mirrored.

the old flow sent charts straight to the download modal as a fake "Various
Artists" compilation named after the chart, so the chart name became the album
and nothing could ever refresh it. now /api/beatport/list hands back the
chart_data discovery takes, and discovery/start mirrors the list by its ref.
"""

import json

import pytest
from flask import Flask

import api.beatport as beatport_api
import api.beatport_charts as charts_api
from core.beatport import fetch as bp_fetch
from core.beatport.playlist import chart_track, display_name, mirror_chart, mirror_track
from database.music_database import MusicDatabase
from tests.beatport._pages import FakeScraper


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    bp_fetch.clear_page_cache()
    import core.metadata.cache as cache_mod

    class _C:
        def store_entity(self, *a):
            pass

        def get_entity(self, *a):
            return None
    monkeypatch.setattr(cache_mod, 'get_metadata_cache', lambda: _C())
    yield
    bp_fetch.clear_page_cache()


def _listed(name='genre'):
    scraper = FakeScraper({'https://www.beatport.com/genre/trance-main-floor/7': 'genre'})
    return bp_fetch.fetch_list('genre:trance-main-floor:7:hype-10', scraper, genre_name='Trance (Main Floor)')


def test_display_name():
    assert display_name({'title': 'Nirvana', 'mix_name': 'Extended Mix'}) == 'Nirvana (Extended Mix)'
    assert display_name({'title': '1997', 'mix_name': 'Original Mix'}) == '1997'
    assert display_name({'title': 'Free Your Mind (Extended Mix)', 'mix_name': 'Extended Mix'}) == 'Free Your Mind (Extended Mix)'
    assert display_name({'title': 'X'}) == 'X'


def test_chart_track_shape():
    t = chart_track(_listed()['tracks'][0])
    assert t['id'] == 'beatport_30438457'
    assert t['name'] == 'Sturm Der Nacht (Nur Für Dich) (Extended Mix)'
    assert t['artists'] == ['Talla 2XLC', 'Marc van Linden']
    assert t['isrc'] == 'DEA312600932'
    assert t['album'] == 'Sturm Der Nacht (Nur Für Dich)'
    assert t['duration_ms'] > 0


def test_mirror_track_carries_no_extra_data():
    """extra_data on a refreshed row would replace its stored discovery"""
    row = mirror_track(chart_track(_listed()['tracks'][0]))
    assert row == {
        'track_name': 'Sturm Der Nacht (Nur Für Dich) (Extended Mix)',
        'artist_name': 'Talla 2XLC, Marc van Linden',
        'album_name': 'Sturm Der Nacht (Nur Für Dich)',
        'duration_ms': row['duration_ms'],
        'source_track_id': 'beatport_30438457',
        'image_url': row['image_url'],
    }


# -- the routes ---------------------------------------------------------------

@pytest.fixture
def app(monkeypatch, tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / 'music.db'))
    scraper = FakeScraper({
        'https://www.beatport.com/genre/trance-main-floor/7': 'genre',
        'https://www.beatport.com/chart/weekend-picks-2026-week-42/905427': 'chart',
    })
    import beatport_unified_scraper as bus
    monkeypatch.setattr(bus, 'BeatportUnifiedScraper', lambda: scraper)
    submitted = []

    class _Executor:
        def submit(self, fn, *a):
            submitted.append(a)

            class _F:
                def done(self):
                    return False
            return _F()

    states = {}
    monkeypatch.setattr(charts_api, 'beatport_chart_states', states)
    monkeypatch.setattr(charts_api, 'beatport_discovery_executor', _Executor())
    monkeypatch.setattr(charts_api, 'get_database', lambda: db)
    monkeypatch.setattr(charts_api, 'get_current_profile_id', lambda: 1)
    monkeypatch.setattr(charts_api, 'add_activity_item', lambda *a, **k: None)

    flask_app = Flask(__name__)
    flask_app.register_blueprint(beatport_api.bp)
    flask_app.register_blueprint(charts_api.bp)
    flask_app.db = db
    flask_app.states = states
    flask_app.submitted = submitted
    flask_app.scraper = scraper
    return flask_app


def test_list_by_ref(app):
    res = app.test_client().get('/api/beatport/list?ref=genre:trance-main-floor:7:hype-10&genre_name=Trance%20(Main%20Floor)')
    data = res.get_json()
    assert res.status_code == 200 and data['success'] is True
    assert data['ref'] == data['hash'] == 'genre:trance-main-floor:7:hype-10'
    assert data['name'] == 'Trance (Main Floor) Hype Top 10'
    assert data['tracks'][0]['name'] == 'Sturm Der Nacht (Nur Für Dich) (Extended Mix)'
    assert data['refreshable'] is True


def test_list_by_url(app):
    res = app.test_client().get('/api/beatport/list?url=https://www.beatport.com/chart/weekend-picks-2026-week-42/905427')
    data = res.get_json()
    assert data['ref'] == 'chart:weekend-picks-2026-week-42:905427'
    assert data['name'] == 'Weekend Picks 2026: Week 42'


def test_list_bad_ref_and_dead_page(app):
    client = app.test_client()
    assert client.get('/api/beatport/list?ref=a1b2c3').status_code == 400
    res = client.get('/api/beatport/list?ref=top-100')
    assert res.status_code == 502
    assert 'FlareSolverr' in res.get_json()['error']


def _start(app, ref, chart):
    return app.test_client().post(f'/api/beatport/discovery/start/{ref}', data=json.dumps({'chart_data': chart}),
                                  content_type='application/json')


def test_discovery_start_mirrors_the_list_by_ref(app):
    chart = app.test_client().get('/api/beatport/list?ref=genre:trance-main-floor:7:hype-10&genre_name=Trance%20(Main%20Floor)').get_json()
    res = _start(app, chart['ref'], chart)
    assert res.get_json()['success'] is True
    mirror = app.db.get_mirrored_playlist_by_source('beatport', 'genre:trance-main-floor:7:hype-10', 1)
    assert mirror['name'] == 'Trance (Main Floor) Hype Top 10'
    assert mirror['description'] == 'https://www.beatport.com/genre/trance-main-floor/7'
    rows = app.db.get_mirrored_playlist_tracks(mirror['id'])
    assert [r['source_track_id'] for r in rows] == [t['id'] for t in chart['tracks']]
    assert rows[0]['album_name'] == 'Sturm Der Nacht (Nur Für Dich)'
    assert app.submitted == [(chart['ref'],)]


def test_an_old_random_hash_gets_no_mirror(app):
    chart = {'name': 'Old Card', 'tracks': [{'id': 'beatport_1', 'name': 'A', 'artists': ['B']}]}
    assert _start(app, 'a1b2c3d4', chart).get_json()['success'] is True
    assert app.db.get_mirrored_playlists(profile_id=1) == []


def test_rediscovery_uses_the_new_tracks_and_drops_old_results(app):
    chart = {'name': 'Hype Top 10', 'tracks': [{'id': 'beatport_1', 'name': 'Old', 'artists': ['A']}]}
    _start(app, 'hype-10', chart)
    state = app.states['hype-10']
    state['phase'] = 'discovered'
    state['discovery_results'] = [{'index': 0, 'status': 'found'}]
    new = {'name': 'Hype Top 10', 'tracks': [{'id': 'beatport_2', 'name': 'New', 'artists': ['B']},
                                             {'id': 'beatport_3', 'name': 'Newer', 'artists': ['C']}]}
    _start(app, 'hype-10', new)
    assert state['chart'] is not chart and state['chart']['tracks'][0]['name'] == 'New'
    assert state['discovery_results'] == []
    assert state['spotify_total'] == 2


def test_mirror_chart_refuses_junk(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / 'music.db'))
    assert mirror_chart(db, 'not-a-ref', {'tracks': [{'id': 'x', 'name': 'x'}]}, 1) is None
    assert mirror_chart(db, 'top-100', {'tracks': []}, 1) is None
