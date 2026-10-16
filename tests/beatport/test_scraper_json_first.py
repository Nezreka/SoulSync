"""the scraper's list methods read the page json first (maV, oct 12 2026).

maV's trance hype top 10 came out with glued titles ("Fancy Footwork Extended
Mix"), chart-named albums and two "Best New Hype Trance (MF) / Unknown Artist"
rows. the json has none of those problems. these drive the real scraper with
its http swapped for captured pages.
"""

import pytest

import beatport_unified_scraper as bus
from core.beatport import fetch as bp_fetch
from tests.beatport._pages import FakeResponse, page_html

PAGES = {
    'https://www.beatport.com/': 'home',
    'https://www.beatport.com/top-100': 'top100',
    'https://www.beatport.com/hype-100': 'hype100',
    'https://www.beatport.com/genre/trance-main-floor/7': 'genre',
    'https://www.beatport.com/genre/trance-main-floor/7/top-100': 'gtop100',
    'https://www.beatport.com/genre/trance-main-floor/7/hype-100': 'ghype100',
    'https://www.beatport.com/chart/weekend-picks-2026-week-42/905427': 'chart',
    'https://www.beatport.com/release/the-jump-ep/7473727': 'release',
    'https://www.beatport.com/track/nirvana/30700667': 'track',
}
GENRE = {'name': 'Trance (Main Floor)', 'slug': 'trance-main-floor', 'id': '7'}


@pytest.fixture
def scraper(monkeypatch):
    bp_fetch.clear_page_cache()
    import core.metadata.cache as cache_mod

    class _Cache:
        def store_entity(self, *a):
            pass

        def get_entity(self, *a):
            return None
    monkeypatch.setattr(cache_mod, 'get_metadata_cache', lambda: _Cache())
    monkeypatch.setattr(bus.time, 'sleep', lambda *_: None)
    s = bus.BeatportUnifiedScraper()
    s.calls = []
    s.pages = dict(PAGES)

    def fake_get(url, timeout=None):
        s.calls.append(url)
        name = s.pages.get(url)
        if name is None:
            return FakeResponse('<html></html>', 404)
        if isinstance(name, tuple):
            return FakeResponse(name[1])
        return FakeResponse(page_html(name))
    monkeypatch.setattr(s, '_get', fake_get)
    yield s
    bp_fetch.clear_page_cache()


def _assert_clean(tracks, list_name=None):
    assert tracks
    for t in tracks:
        assert t['release_name'], t['title']
        assert t['artist'] != 'Unknown Artist', t['title']
        assert t['id'].startswith('beatport_') and t['id'][9:].isdigit()
        assert not t['title'].endswith('Mix'), t['title']
        if list_name:
            assert t['list_name'] == list_name


def test_genre_top10_lists(scraper):
    data = scraper.scrape_genre_top10_tracks('trance-main-floor', '7')
    assert data['beatport_top10'][0]['title'] == 'Nirvana'
    assert data['hype_top10'][0]['title'] == 'Sturm Der Nacht (Nur Für Dich)'
    assert data['has_hype_section'] is True
    assert data['total_tracks'] == 20
    _assert_clean(data['beatport_top10'], 'Beatport Top 10')
    _assert_clean(data['hype_top10'], 'Hype Top 10')
    assert scraper.calls == ['https://www.beatport.com/genre/trance-main-floor/7']


def test_homepage_top10_lists(scraper):
    data = scraper.scrape_homepage_top10_lists()
    _assert_clean(data['beatport_top10'], 'Beatport Top 10')
    _assert_clean(data['hype_top10'], 'Hype Top 10')
    assert data['beatport_top10'][0]['title'] == 'Fancy Footwork'
    assert data['beatport_top10'][0]['mix_name'] == 'Extended Mix'


def test_top_100s_need_no_enrichment(scraper):
    top = scraper.scrape_top_100(limit=100, enrich=True)
    hype = scraper.scrape_hype_top_100(limit=100, enrich=True)
    _assert_clean(top, 'Top 100')
    _assert_clean(hype, 'Hype Top 100')
    # one page each, no per-track page visits
    assert scraper.calls == ['https://www.beatport.com/top-100', 'https://www.beatport.com/hype-100']


def test_genre_charts(scraper):
    top100 = scraper.scrape_genre_charts(GENRE, limit=100)
    _assert_clean(top100, 'Trance (Main Floor) Top 100')
    assert scraper.calls == ['https://www.beatport.com/genre/trance-main-floor/7/top-100']
    hype10 = scraper.scrape_genre_hype_top_10(GENRE)
    assert hype10[0]['title'] == 'Sturm Der Nacht (Nur Für Dich)'
    hype100 = scraper.scrape_genre_hype_charts(GENRE, limit=100)
    assert scraper.calls[-1] == 'https://www.beatport.com/genre/trance-main-floor/7/hype-100'
    _assert_clean(hype100)
    top10 = scraper.scrape_genre_top_10(GENRE)
    assert top10[0]['title'] == 'Nirvana'


def test_chart_extract(scraper):
    tracks = scraper.extract_tracks_from_chart(
        'https://www.beatport.com/chart/weekend-picks-2026-week-42/905427', 'Weekend Picks', 100)
    _assert_clean(tracks, 'Weekend Picks')
    assert len(tracks) == 12


def test_release_metadata(scraper):
    data = scraper.get_release_metadata('https://www.beatport.com/release/the-jump-ep/7473727')
    assert data['success'] is True
    assert data['album']['name'] == 'The Jump EP'
    assert data['album']['label'] == 'SiO'
    assert data['album']['album_type'] == 'ep'
    assert data['artist']['name'] == 'Andrew Azara'
    assert [t['track_number'] for t in data['tracks']] == [1, 2, 3]
    assert all(t['id'].startswith('beatport_') and t['isrc'] for t in data['tracks'])
    assert all(t['album'] is data['album'] for t in data['tracks'])


def test_falls_back_to_html_when_the_page_has_no_json(scraper):
    raw = ('raw', '<html><body><a href="/track/x/1">Some Track</a></body></html>')
    scraper.pages['https://www.beatport.com/top-100'] = raw
    scraper.pages['https://beatport.com/top-100'] = raw
    tracks = scraper.scrape_top_100(limit=10, enrich=False)
    assert tracks and tracks[0]['url'].endswith('/track/x/1')


# -- enrichment ---------------------------------------------------------------

def _thin(url='https://www.beatport.com/track/nirvana/30700667', title='NirvanaExtended Mix'):
    return {'title': title, 'artist': 'Armin van Buuren', 'url': url, 'list_name': 'Hype Top 10', 'position': 4, 'rank': 4}


def test_enrich_reads_the_track_not_its_charts(scraper):
    """the track page also lists "charts with this track", one of them named
    Nirvana. the old title fallback picked it: Unknown Artist, no release"""
    out = scraper.enrich_chart_tracks([_thin()])
    t = out[0]
    assert t['title'] == 'Nirvana'
    assert t['mix_name'] == 'Extended Mix'
    assert t['artist'] == 'Armin van Buuren, Funk Tribu'
    assert t['release_name'] == 'Nirvana'
    assert t['isrc'] == 'NLF712606025'
    assert t['list_name'] == 'Hype Top 10'
    assert t['position'] == 4 and t['rank'] == 4


def test_enrich_never_guesses(scraper):
    """a track page with no track-details query leaves the track alone"""
    import json
    from tests.beatport._pages import next_data
    data = next_data('track')
    q = data['props']['pageProps']['dehydratedState']['queries']
    data['props']['pageProps']['dehydratedState']['queries'] = [x for x in q if not str(x['queryKey'][0]).startswith('track-details')]
    scraper.pages['https://www.beatport.com/track/nirvana/30700667'] = (
        'raw', f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>')
    original = _thin()
    out = scraper.enrich_chart_tracks([dict(original)])
    assert out == [original]


def test_enrich_skips_tracks_that_are_already_rich(scraper):
    rich = scraper.scrape_top_100(limit=3)
    scraper.calls.clear()
    progress = []
    out = scraper.enrich_chart_tracks(rich, progress_callback=lambda *a: progress.append(a))
    assert out == rich
    assert scraper.calls == []
    assert [p[0] for p in progress] == [1, 2, 3]


def test_enrich_leaves_non_track_urls(scraper):
    t = {'title': 'x', 'url': 'https://www.beatport.com/chart/a/1'}
    assert scraper.enrich_chart_tracks([dict(t)]) == [t]
    assert scraper.calls == []
