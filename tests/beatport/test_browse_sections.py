"""the beatport tab's browse sections, read from the page json.

checked against live beatport on oct 16 2026, the html scrape for these had
gone stale: new releases came back empty, featured and dj charts answered
"section not found", the genre hero had nothing, and hero titles were glued to
the artist ("Jinx (Club Mix)Imbermind", "Whole Room Dancing Riva"). the page
json has every one of them. these run the real routes on captured pages.
"""

import pytest
from flask import Flask

import api.beatport as beatport_api
from core.beatport import browse
from core.beatport import fetch as bp_fetch
from core.beatport.next_data import module_items, page_modules
from tests.beatport._pages import FakeScraper, next_data

HOME = 'https://www.beatport.com/'
GENRE = 'https://www.beatport.com/genre/trance-main-floor/7'


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    bp_fetch.clear_page_cache()
    for section in beatport_api.beatport_data_cache['homepage'].values():
        section['data'] = None
        section['timestamp'] = 0
    beatport_api.beatport_data_cache['genre'].clear()
    yield
    bp_fetch.clear_page_cache()
    for section in beatport_api.beatport_data_cache['homepage'].values():
        section['data'] = None
        section['timestamp'] = 0
    beatport_api.beatport_data_cache['genre'].clear()


def _scraper():
    return FakeScraper({HOME: 'home_browse', GENRE: 'genre_browse'})


def test_modules_are_found_by_type_not_id():
    data = next_data('home_browse')
    types = [t for t, _ in page_modules(data)]
    assert {'hypeFeature', 'releaseFeature', 'homeLargeSlideshowOld', 'chartFeature',
            'homeSmallSlideshow'} <= set(types)
    assert module_items(data, 'nopeFeature') is None


def test_every_homepage_section():
    s = _scraper()
    hero = browse.hero(s)
    assert hero[2]['title'] == 'Jinx (Club Mix)' and hero[2]['artist'] == 'Imbermind'
    assert hero[1]['title'] == 'Whole Room Dancing' and hero[1]['label'] == 'VOD'
    assert '920x642' in hero[0]['image_url']                  # the wide slideshow art
    for section in (browse.new_releases(s), browse.hype_picks(s), browse.top10_releases(s)):
        assert section
        for r in section:
            assert r['title'] and r['artist'] and r['label']
            assert r['url'].startswith('https://www.beatport.com/release/')
            assert '500x500' in r['image_url']
    assert [r['rank'] for r in browse.top10_releases(s)] == list(range(1, 7))
    featured = browse.featured_charts(s)
    assert featured[0]['name'] == 'Weekend Picks 2026: Week 42' and featured[0]['creator'] == 'Beatport'
    dj = browse.dj_charts(s)
    assert dj[0]['name'] == 'Artist of the Month | Bonobo' and dj[0]['creator'] == 'Bonobo'
    assert all(c['url'].startswith('https://www.beatport.com/chart/') and c['image'] for c in featured + dj)
    # one homepage fetch served all of them
    assert s.calls == [HOME]


def test_genre_sections():
    s = _scraper()
    hero = browse.genre_hero('trance-main-floor', '7', s)
    assert hero[0]['title'] == 'Velorum' and hero[0]['artists_string'] == 'Activa'
    top = browse.genre_top10_releases('trance-main-floor', '7', s)
    assert top[0]['title'] == 'Mashed' and top[0]['rank'] == 1


def test_a_page_without_json_is_none():
    s = FakeScraper({HOME: 403})
    assert browse.hero(s) is None and browse.featured_charts(s) is None


# -- the routes ---------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    import beatport_unified_scraper as bus
    scraper = _scraper()
    monkeypatch.setattr(bus, 'BeatportUnifiedScraper', lambda: scraper)
    # api.beatport imported the class by name, patch that one too or its
    # html fallback scrapes live beatport
    monkeypatch.setattr(beatport_api, 'BeatportUnifiedScraper', lambda: scraper)
    monkeypatch.setattr(browse, 'fetch_next_data',
                        lambda url, s=None, use_cache=True: bp_fetch.fetch_next_data(url, scraper, use_cache))
    beatport_api.configure(activity_item=lambda *a, **k: None,
                           cache_headers=lambda r, *a, **k: r, metadata_cache=lambda: None)
    app = Flask(__name__)
    app.register_blueprint(beatport_api.bp)
    return app.test_client()


@pytest.mark.parametrize('path,key', [
    ('/api/beatport/hero-tracks', 'tracks'),
    ('/api/beatport/new-releases', 'releases'),
    ('/api/beatport/hype-picks', 'releases'),
    ('/api/beatport/featured-charts', 'charts'),
    ('/api/beatport/dj-charts', 'charts'),
    ('/api/beatport/homepage/top-10-releases-cards', 'releases'),
    ('/api/beatport/genre/trance-main-floor/7/hero', 'releases'),
    ('/api/beatport/genre/trance-main-floor/7/top-10-releases', 'releases'),
])
def test_routes_answer_from_the_page_json(client, path, key):
    data = client.get(path).get_json()
    assert data['success'] is True
    assert len(data[key]) > 0
    # a second call is the route's own cache, same answer
    assert client.get(path).get_json()[key] == data[key]


def test_a_route_falls_back_to_html_without_json(client, monkeypatch):
    """no json: the old html scrape still runs (here it finds no page either)"""
    monkeypatch.setattr(browse, 'hype_picks', lambda *a, **k: None)
    import beatport_unified_scraper as bus
    monkeypatch.setattr(bus, 'BeatportUnifiedScraper', lambda: FakeScraper({}))
    monkeypatch.setattr(beatport_api, 'BeatportUnifiedScraper', lambda: FakeScraper({}))
    data = client.get('/api/beatport/hype-picks').get_json()
    assert data['success'] is False
    assert 'homepage' in data['error'].lower()
