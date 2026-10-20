"""reading beatport pages from their json (maV, discord oct 12 2026).

the html scraper glued title and mix ("NirvanaExtended Mix"), split the genre
top 10s by position, and its track-page enrichment returned "charts with this
track" as if they were the track. every one of those shows up downstream as a
chart-named album or an "Unknown Artist" row. these run the parser on real
captured pages.
"""

from core.beatport import next_data as nd
from tests.beatport._pages import next_data, page_html


def test_extracts_next_data_from_the_page_html():
    data = nd.extract_next_data(page_html('genre'))
    assert data == next_data('genre')


def test_no_next_data_is_none_not_a_crash():
    assert nd.extract_next_data('<html>cloudflare says no</html>') is None
    assert nd.extract_next_data('') is None
    assert nd.extract_next_data(None) is None
    assert nd.extract_next_data('<script id="__NEXT_DATA__">{broken</script>') is None


def test_genre_top_10_and_hype_10_come_from_their_own_queries():
    data = next_data('genre')
    top = nd.list_tracks(data, 'top-10')
    hype = nd.list_tracks(data, 'hype-10')
    assert top[0]['title'] == 'Nirvana'
    assert hype[0]['title'] == 'Sturm Der Nacht (Nur Für Dich)'
    assert {t['id'] for t in top}.isdisjoint({t['id'] for t in hype})


def test_lists_are_found_by_key_not_position():
    """put the hype list first: the old positional split would swap them"""
    data = next_data('genre')
    q = data['props']['pageProps']['dehydratedState']['queries']
    q.reverse()
    assert nd.list_tracks(data, 'top-10')[0]['title'] == 'Nirvana'
    assert nd.list_tracks(data, 'hype-10')[0]['title'] == 'Sturm Der Nacht (Nur Für Dich)'


def test_title_and_mix_stay_apart():
    track = nd.list_tracks(next_data('genre'), 'top-10')[0]
    assert track['title'] == 'Nirvana'
    assert track['mix_name'] == 'Extended Mix'


def test_every_track_carries_its_real_release():
    for name, kind in [('genre', 'top-10'), ('genre', 'hype-10'), ('home', 'top-10'),
                       ('top100', 'top-100'), ('hype100', 'hype-100'), ('chart', 'chart'),
                       ('gtop100', 'top-100'), ('ghype100', 'hype-100'), ('release', 'release')]:
        tracks = nd.list_tracks(next_data(name), kind, list_name='Some Chart')
        assert tracks, (name, kind)
        for t in tracks:
            assert t['release_name'], (name, t['title'])
            assert t['release_name'] != 'Some Chart'
            assert t['release_id'].isdigit()


def test_track_fields():
    t = nd.list_tracks(next_data('genre'), 'top-10')[0]
    assert t['id'] == 'beatport_30700667'
    assert t['beatport_id'] == 30700667
    assert t['url'] == 'https://www.beatport.com/track/nirvana/30700667'
    assert t['artist'] == 'Armin van Buuren, Funk Tribu'
    assert [a['name'] for a in t['artists']] == ['Armin van Buuren', 'Funk Tribu']
    assert t['isrc'] == 'NLF712606025'
    assert t['label'] == 'Armada Music'
    assert t['bpm'] == 145
    assert t['key'] == 'F Minor'
    assert t['genre'] == 'Trance (Main Floor)'
    assert t['duration'] == 311 and t['duration_ms'] == 311379
    assert t['catalog_number'] == 'ARMAS3885'
    assert t['release_image'].startswith('https://geo-media.beatport.com/')


def test_ids_never_repeat_across_lists():
    """beatport_chart_<position> gave track 1 of every chart the same id,
    and the sync match cache then handed chart B chart A's matches"""
    ids = []
    for name, kind in [('home', 'top-10'), ('home', 'hype-10'), ('genre', 'top-10'), ('genre', 'hype-10')]:
        ids += [t['id'] for t in nd.list_tracks(next_data(name), kind)]
    assert all(i.startswith('beatport_') and i[len('beatport_'):].isdigit() for i in ids)
    positional = [i for i in ids if i.startswith('beatport_chart_')]
    assert not positional


def test_missing_list_is_none_empty_list_is_empty():
    assert nd.list_tracks(next_data('top100'), 'hype-100') is None
    data = next_data('top100')
    data['props']['pageProps']['dehydratedState']['queries'][0]['state']['data']['results'] = []
    assert nd.list_tracks(data, 'top-100') == []


def test_limit():
    assert len(nd.list_tracks(next_data('top100'), 'top-100', limit=5)) == 5


def test_track_page_returns_the_track_not_its_charts():
    """the old enrichment read queries[1] (charts with this track), id-matched
    nothing, then title-matched a chart literally called "Nirvana" and turned
    it into an Unknown Artist track with no release"""
    data = next_data('track')
    charts = [k for k, _ in nd.queries(data) if k.endswith('-charts')]
    assert charts, 'fixture should still hold the charts query that fooled the old code'
    t = nd.track_details(data)
    assert t['beatport_id'] == 30700667
    assert t['title'] == 'Nirvana'
    assert t['mix_name'] == 'Extended Mix'
    assert t['artist'] == 'Armin van Buuren, Funk Tribu'
    assert t['release_name'] == 'Nirvana'
    assert t['label'] == 'Armada Music'
    assert t['isrc'] == 'NLF712606025'
    assert t['release_image'].endswith('.jpg') and '{w}' not in t['release_image']


def test_track_page_without_details_is_none():
    assert nd.track_details(next_data('genre')) is None


def test_chart_and_release_meta():
    chart = nd.chart_meta(next_data('chart'))
    assert chart['name'] == 'Weekend Picks 2026: Week 42'
    assert chart['creator'] == 'Beatport'
    assert chart['url'] == 'https://www.beatport.com/chart/weekend-picks-2026-week-42/905427'
    release = nd.release_meta(next_data('release'))
    assert release['name'] == 'The Jump EP'
    assert [a['name'] for a in release['artists']] == ['Andrew Azara']
    assert release['label'] == 'SiO'
    assert release['catalog_number'] == 'SiO013'
    assert nd.chart_meta(next_data('genre')) is None
    assert nd.release_meta(next_data('chart')) is None


def test_junk_tracks_are_skipped():
    assert nd.normalize_track({'name': 'x'}, 1) is None
    assert nd.normalize_track({'id': 5}, 1) is None
    assert nd.normalize_track('nope', 1) is None
    tracks = nd.normalize_tracks([{'id': 1, 'name': 'A'}, None, {'id': 2, 'name': 'B'}])
    assert [t['position'] for t in tracks] == [1, 2]
    assert tracks[0]['artist'] == 'Unknown Artist'
    assert tracks[0]['release_name'] == ''


def test_beatport_id_from_url():
    assert nd.beatport_id_from_url('https://www.beatport.com/track/nirvana/30700667') == '30700667'
    assert nd.beatport_id_from_url('https://www.beatport.com/chart/x/5') == ''
    assert nd.track_key(30700667) == 'beatport_30700667'
    assert nd.track_key('') == ''
