"""beatport as an enrichment source at tag time.

for an electronic track beatport has the label's own bpm and key, the
catalog number and the beatport genre. a lookup is by isrc and only takes the
exact recording: from the metadata cache when the track came in through a
beatport list (free), else from beatport's search, picked by isrc. these use a
real metadata cache table, the captured search page and real audio files.
"""

import struct
from pathlib import Path

import pytest
from mutagen.flac import FLAC
from mutagen.id3 import ID3

from core.beatport import enrich
from core.beatport import fetch as bp_fetch
from core.metadata import source as ms
from core.metadata.cache import MetadataCache
from core.metadata.common import get_mutagen_symbols
from database.music_database import MusicDatabase
from tests.beatport._pages import FakeScraper
from tests.metadata.test_metadata_enrichment import _Config

SEARCH = 'https://www.beatport.com/search/tracks?q=Armin%20van%20Buuren%20Nirvana'
ISRC = 'NLF712606025'


@pytest.fixture
def cache(tmp_path, monkeypatch):
    db = MusicDatabase(database_path=str(tmp_path / 'music.db'))
    real = MetadataCache()
    real._get_db = lambda: db
    import core.metadata.cache as cache_mod
    monkeypatch.setattr(cache_mod, 'get_metadata_cache', lambda: real)
    monkeypatch.setattr(enrich, 'SEARCH_MIN_INTERVAL', 0)
    enrich.reset()
    bp_fetch.clear_page_cache()
    yield real
    enrich.reset()
    bp_fetch.clear_page_cache()


def test_standard_key():
    assert enrich.standard_key('F Minor') == 'Fm'
    assert enrich.standard_key('Eb Major') == 'Eb'
    assert enrich.standard_key('C♯ Minor') == 'C#m'
    assert enrich.standard_key('B♭ minor') == 'Bbm'
    assert enrich.standard_key('G') == 'G'
    assert enrich.standard_key('') == ''
    assert enrich.standard_key('not a key') == ''


def test_looks_electronic():
    assert enrich.looks_electronic(['Trance (Main Floor)'])
    assert enrich.looks_electronic(['Pop', 'Melodic House & Techno'])
    assert enrich.looks_electronic(['Drum & Bass'])
    assert not enrich.looks_electronic(['Rock', 'Indie', None])
    assert not enrich.looks_electronic([])


def test_search_result_shape():
    from tests.beatport._pages import next_data
    results = enrich.search_results(next_data('search'))
    nirvana = [r for r in results if r['isrc'] == ISRC][0]
    assert nirvana['title'] == 'Nirvana' and nirvana['mix_name'] == 'Extended Mix'
    assert nirvana['artist'] == 'Armin van Buuren, Funk Tribu'
    assert nirvana['bpm'] == 145 and nirvana['key'] == 'F Minor'
    assert nirvana['label'] == 'Armada Music'
    assert nirvana['catalog_number'] == 'ARMAS3885'
    assert nirvana['genre'] == 'Trance (Main Floor)'
    assert nirvana['id'] == 'beatport_30700667'
    assert nirvana['url'].endswith('/30700667')


def test_search_picks_by_isrc_and_remembers_everything(cache):
    scraper = FakeScraper({SEARCH: 'search'})
    track = enrich.lookup(ISRC, 'Armin van Buuren, Funk Tribu', 'Nirvana (Extended Mix)', scraper=scraper)
    assert track['beatport_id'] == 30700667
    assert scraper.calls == [SEARCH]
    # the next lookup for it (or any other result) is a cache hit
    again = enrich.lookup(ISRC, 'x', 'y', scraper=FakeScraper({}))
    assert again['beatport_id'] == 30700667


def test_a_title_match_with_another_isrc_is_not_taken(cache):
    """the search has the right title, but not this recording: no tags"""
    scraper = FakeScraper({SEARCH: 'search'})
    assert enrich.lookup('GBAAA9900001', 'Armin van Buuren', 'Nirvana', scraper=scraper) is None
    # and the miss is remembered, beatport isn't asked again
    assert enrich.lookup('GBAAA9900001', 'Armin van Buuren', 'Nirvana', scraper=scraper) is None
    assert scraper.calls == [SEARCH]


def test_no_search_when_not_allowed_or_no_isrc(cache):
    scraper = FakeScraper({SEARCH: 'search'})
    assert enrich.lookup(ISRC, 'Armin van Buuren', 'Nirvana', allow_search=False, scraper=scraper) is None
    assert enrich.lookup('', 'Armin van Buuren', 'Nirvana', scraper=scraper) is None
    assert scraper.calls == []


def test_a_track_from_a_beatport_list_needs_no_request(cache):
    from tests.beatport._pages import next_data
    from core.beatport.next_data import list_tracks
    bp_fetch.remember_tracks(list_tracks(next_data('genre'), 'top-10'))
    track = enrich.lookup(ISRC, 'Armin van Buuren', 'Nirvana', scraper=FakeScraper({}))
    assert track['title'] == 'Nirvana' and track['bpm'] == 145


def test_blocked_search_is_none(cache):
    assert enrich.lookup(ISRC, 'Armin van Buuren', 'Nirvana', scraper=FakeScraper({SEARCH: 403})) is None


# -- the tagging pipeline -----------------------------------------------------

def _pp_with_isrc(isrc=ISRC):
    pp = ms._blank_post_process_state()
    pp['deezer_isrc'] = isrc
    return pp


def test_process_step_searches_for_electronic_tracks(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(enrich, 'search_by_isrc', lambda *a, **k: calls.append(a) or {'bpm': 145})
    pp = _pp_with_isrc()
    pp['mb_genres'] = ['trance']
    ms._process_beatport_source(pp, {}, _Config({}), None, 'Nirvana', 'Armin van Buuren')
    assert pp['beatport'] == {'bpm': 145}
    assert calls == [(ISRC, 'Armin van Buuren', 'Nirvana', None)]


def test_process_step_does_not_search_for_everything(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(enrich, 'search_by_isrc', lambda *a, **k: calls.append(a))
    pp = _pp_with_isrc()
    pp['mb_genres'] = ['rock']
    ms._process_beatport_source(pp, {'genre': 'Indie'}, _Config({}), None, 'Song', 'Band')
    assert calls == [] and pp['beatport'] is None
    # unless asked to
    ms._process_beatport_source(pp, {}, _Config({'beatport.search_all_tracks': True}), None, 'Song', 'Band')
    assert len(calls) == 1


def test_process_step_off_and_without_isrc(cache, monkeypatch):
    monkeypatch.setattr(enrich, 'search_by_isrc', lambda *a, **k: pytest.fail('no lookup expected'))
    pp = _pp_with_isrc()
    pp['mb_genres'] = ['techno']
    ms._process_beatport_source(pp, {}, _Config({'beatport.embed_tags': False}), None, 'x', 'y')
    ms._process_beatport_source(ms._blank_post_process_state(), {'genre': 'techno'}, _Config({}), None, 'x', 'y')


def test_the_step_runs_last_even_for_an_older_saved_order():
    assert ms.DEFAULT_SOURCE_ORDER[-1] == 'beatport'


def _flac(path: Path) -> FLAC:
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    path.write_bytes(b'fLaC' + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(streaminfo))
    return FLAC(str(path))


BEATPORT = {
    'beatport_id': 30700667, 'bpm': 145, 'key': 'F Minor', 'label': 'Armada Music',
    'catalog_number': 'ARMAS3885', 'genre': 'Trance (Main Floor)', 'isrc': ISRC,
}


def _write(audio, cfg=None, pp_extra=None):
    pp = ms._blank_post_process_state()
    pp['beatport'] = dict(BEATPORT)
    pp.update(pp_extra or {})
    ms._write_embedded_metadata(audio, {'genre': ''}, pp, _Config(cfg or {}), get_mutagen_symbols())
    audio.save()


def test_writes_beatport_tags_to_a_flac(tmp_path):
    audio = _flac(tmp_path / 'a.flac')
    _write(audio)
    tags = FLAC(str(tmp_path / 'a.flac'))
    assert tags['BPM'] == ['145']
    assert tags['INITIALKEY'] == ['Fm']
    assert tags['LABEL'] == ['Armada Music']
    assert tags['CATALOGNUMBER'] == ['ARMAS3885']
    assert tags['BEATPORT_TRACK_ID'] == ['30700667']
    assert 'Trance (Main Floor)' in tags['GENRE'][0]


def test_beatport_bpm_wins_over_deezer(tmp_path):
    audio = _flac(tmp_path / 'a.flac')
    _write(audio, pp_extra={'deezer_bpm': 72})
    assert FLAC(str(tmp_path / 'a.flac'))['BPM'] == ['145']


def test_each_tag_can_be_turned_off(tmp_path):
    audio = _flac(tmp_path / 'a.flac')
    _write(audio, cfg={'beatport.tags.bpm': False, 'beatport.tags.key': False, 'beatport.tags.label': False,
                       'beatport.tags.catalog_number': False, 'beatport.tags.track_id': False,
                       'beatport.tags.genre_merge': False})
    tags = FLAC(str(tmp_path / 'a.flac'))
    for key in ('BPM', 'INITIALKEY', 'LABEL', 'CATALOGNUMBER', 'BEATPORT_TRACK_ID', 'GENRE'):
        assert key not in tags, key


class _Id3File:
    """an mp3's tag side: real mutagen ID3 frames, saved to a real file"""

    def __init__(self, path):
        self.path = path
        self.tags = ID3()

    def save(self):
        self.tags.save(str(self.path))


def test_writes_tkey_to_an_mp3(tmp_path):
    path = tmp_path / 'a.mp3'
    audio = _Id3File(path)
    _write(audio)
    tags = ID3(str(path))
    assert tags['TKEY'].text == ['Fm']
    assert tags['TBPM'].text == ['145']
    assert tags['TPUB'].text == ['Armada Music']
    assert tags['TXXX:CATALOGNUMBER'].text == ['ARMAS3885']
    assert tags['TXXX:BEATPORT_TRACK_ID'].text == ['30700667']


def test_no_beatport_result_writes_nothing_new(tmp_path):
    audio = _flac(tmp_path / 'a.flac')
    pp = ms._blank_post_process_state()
    ms._write_embedded_metadata(audio, {'genre': ''}, pp, _Config({}), get_mutagen_symbols())
    audio.save()
    tags = FLAC(str(tmp_path / 'a.flac'))
    assert 'INITIALKEY' not in tags and 'BEATPORT_TRACK_ID' not in tags
