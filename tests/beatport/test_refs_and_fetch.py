"""beatport list refs and the fetch that reads them.

a ref is what makes a beatport list mirrorable: the old cards used a random
hash nothing could turn back into a url, so beatport mirrors never refreshed.
"""

import pytest

from core.beatport import fetch as bp_fetch
from core.beatport.refs import parse_ref, ref_from_url
from tests.beatport._pages import FakeScraper


@pytest.fixture(autouse=True)
def _no_page_cache(monkeypatch):
    bp_fetch.clear_page_cache()
    stored = []

    class _Cache:
        def store_entity(self, source, entity_type, entity_id, data):
            stored.append((source, entity_type, entity_id, data))

        def get_entity(self, source, entity_type, entity_id):
            for s, t, i, d in reversed(stored):
                if (s, t, i) == (source, entity_type, entity_id):
                    return d
            return None

    import core.metadata.cache as cache_mod
    monkeypatch.setattr(cache_mod, 'get_metadata_cache', lambda: _Cache())
    yield stored
    bp_fetch.clear_page_cache()


@pytest.mark.parametrize('text,url', [
    ('top-10', 'https://www.beatport.com/'),
    ('hype-10', 'https://www.beatport.com/'),
    ('top-100', 'https://www.beatport.com/top-100'),
    ('hype-100', 'https://www.beatport.com/hype-100'),
    ('genre:trance-main-floor:7:top-10', 'https://www.beatport.com/genre/trance-main-floor/7'),
    ('genre:trance-main-floor:7:hype-100', 'https://www.beatport.com/genre/trance-main-floor/7/hype-100'),
    ('chart:weekend-picks-2026-week-42:905427', 'https://www.beatport.com/chart/weekend-picks-2026-week-42/905427'),
    ('release:the-jump-ep:7473727', 'https://www.beatport.com/release/the-jump-ep/7473727'),
    ('genre:140-deep-dubstep-grime:95:top-100', 'https://www.beatport.com/genre/140-deep-dubstep-grime/95/top-100'),
])
def test_ref_round_trip(text, url):
    ref = parse_ref(text)
    assert ref is not None
    assert str(ref) == text
    assert ref.url == url
    assert '/' not in str(ref)


@pytest.mark.parametrize('junk', [
    '', None, 'a1b2c3d4', 'genre:trance:7:top-50', 'chart:x', 'chart:Bad Slug:1',
    'release:x:abc', '../etc/passwd', 'genre:x:7:top-10:extra',
])
def test_junk_is_not_a_ref(junk):
    """an old random chart hash, or anything else, must not parse"""
    assert parse_ref(junk) is None


def test_refs_from_urls():
    assert str(ref_from_url('https://www.beatport.com/genre/trance-main-floor/7')) == 'genre:trance-main-floor:7:top-10'
    assert str(ref_from_url('https://beatport.com/genre/trance-main-floor/7/hype-100/')) == 'genre:trance-main-floor:7:hype-100'
    assert str(ref_from_url('https://www.beatport.com/chart/weekend-picks-2026-week-42/905427')) == 'chart:weekend-picks-2026-week-42:905427'
    assert ref_from_url('https://www.beatport.com/track/nirvana/30700667') is None
    assert ref_from_url('https://example.com/chart/x/1') is None


def test_names_and_refreshable():
    assert parse_ref('hype-10').default_name() == 'Hype Top 10'
    assert parse_ref('genre:trance-main-floor:7:hype-10').default_name('Trance (Main Floor)') == 'Trance (Main Floor) Hype Top 10'
    assert parse_ref('genre:trance-main-floor:7:top-100').default_name('Trance (Main Floor)') == 'Trance (Main Floor) Top 100'
    assert parse_ref('release:x:1').refreshable is False
    assert parse_ref('chart:x:1').refreshable is True


def test_fetch_genre_hype_10():
    scraper = FakeScraper({'https://www.beatport.com/genre/trance-main-floor/7': 'genre'})
    result = bp_fetch.fetch_list('genre:trance-main-floor:7:hype-10', scraper, genre_name='Trance (Main Floor)')
    assert result['name'] == 'Trance (Main Floor) Hype Top 10'
    assert result['ref'] == 'genre:trance-main-floor:7:hype-10'
    assert result['tracks'][0]['title'] == 'Sturm Der Nacht (Nur Für Dich)'
    assert result['tracks'][0]['list_name'] == 'Trance (Main Floor) Hype Top 10'
    assert result['image']


def test_one_page_serves_both_top_10s(_no_page_cache):
    scraper = FakeScraper({'https://www.beatport.com/': 'home'})
    top = bp_fetch.fetch_list('top-10', scraper)
    hype = bp_fetch.fetch_list('hype-10', scraper)
    assert top['tracks'][0]['title'] != hype['tracks'][0]['title']
    assert scraper.calls == ['https://www.beatport.com/']


def test_chart_takes_its_own_name_and_owner():
    url = 'https://www.beatport.com/chart/weekend-picks-2026-week-42/905427'
    result = bp_fetch.fetch_list('chart:weekend-picks-2026-week-42:905427', FakeScraper({url: 'chart'}))
    assert result['name'] == 'Weekend Picks 2026: Week 42'
    assert result['creator'] == 'Beatport'
    assert len(result['tracks']) == 12


def test_blocked_or_missing_page_is_none():
    assert bp_fetch.fetch_list('top-100', FakeScraper({'https://www.beatport.com/top-100': 403})) is None
    assert bp_fetch.fetch_list('top-100', FakeScraper({})) is None
    assert bp_fetch.fetch_list('not-a-ref', FakeScraper({})) is None


def test_page_without_the_list_is_none():
    # the top 100 page has no hype list
    scraper = FakeScraper({'https://www.beatport.com/hype-100': 'top100'})
    assert bp_fetch.fetch_list('hype-100', scraper) is None


def test_fetched_tracks_are_remembered_by_url_and_id(_no_page_cache):
    scraper = FakeScraper({'https://www.beatport.com/genre/trance-main-floor/7': 'genre'})
    bp_fetch.fetch_list('genre:trance-main-floor:7:top-10', scraper)
    keys = {(s, t, i) for s, t, i, _ in _no_page_cache}
    assert ('beatport', 'track', 'https://www.beatport.com/track/nirvana/30700667') in keys
    assert ('beatport', 'track', 'id:30700667') in keys
    assert bp_fetch.cached_track('beatport_30700667')['isrc'] == 'NLF712606025'
    assert bp_fetch.cached_track('30700667')['title'] == 'Nirvana'
    assert bp_fetch.cached_track('beatport_chart_0') is None


def test_fetch_track_page():
    url = 'https://www.beatport.com/track/nirvana/30700667'
    track = bp_fetch.fetch_track(url, FakeScraper({url: 'track'}))
    assert track['title'] == 'Nirvana'
    assert track['url'] == url
    assert track['isrc'] == 'NLF712606025'
