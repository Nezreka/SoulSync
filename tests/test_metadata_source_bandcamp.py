"""Tests for the Bandcamp enrichment hook in core.metadata.source.

Mirrors the existing per-source hooks (_process_lastfm_source,
_process_genius_source): gated by a config flag, pulls
`runtime.bandcamp_worker.client`, populates `pp["bandcamp_*"]` fields which
`_write_embedded_metadata` later turns into ID3/Vorbis/MP4 tags.

Reuses the fake audio-file/symbols/config helpers from
tests/metadata/test_metadata_enrichment.py rather than redefining them.
"""

from __future__ import annotations

import types

from core.metadata import enrichment as me
from core.metadata import source as ms
from tests.metadata.test_metadata_enrichment import (
    _Config,
    _fake_symbols,
    _FakeAudio,
)


class _FakeBandcampClient:
    def __init__(self, track_result=None, album_result=None):
        self._track_result = track_result
        self._album_result = album_result
        self.track_calls = []
        self.album_calls = []

    def search_track(self, artist, title):
        self.track_calls.append((artist, title))
        return self._track_result

    def search_album(self, artist, album):
        self.album_calls.append((artist, album))
        return self._album_result


def _runtime(client):
    return types.SimpleNamespace(bandcamp_worker=types.SimpleNamespace(client=client))


# ---------------------------------------------------------------------------
# _process_bandcamp_source — unit level, no audio file involved.
# ---------------------------------------------------------------------------


class TestProcessBandcampSource:
    def test_confident_track_match_populates_pp(self):
        client = _FakeBandcampClient(track_result={
            'id': '123', 'url': 'https://x.bandcamp.com/track/y', 'title': 'Song One',
            'tags': ['idm', 'ambient'], 'label': 'Test Label',
        })
        pp = ms._blank_post_process_state()
        cfg = _Config({'bandcamp.embed_tags': True})

        ms._process_bandcamp_source(pp, {'album': 'Album One'}, cfg, _runtime(client), 'Song One', 'Artist One')

        assert pp['bandcamp_url'] == 'https://x.bandcamp.com/track/y'
        assert pp['bandcamp_tags'] == ['idm', 'ambient']
        assert pp['bandcamp_label'] == 'Test Label'
        assert client.track_calls == [('Artist One', 'Song One')]
        assert client.album_calls == []  # track search succeeded — no album fallback

    def test_falls_back_to_album_search_when_track_search_fails(self):
        client = _FakeBandcampClient(
            track_result=None,
            album_result={'url': 'https://x.bandcamp.com/album/z', 'tags': ['rock'], 'label': 'Album Label'},
        )
        pp = ms._blank_post_process_state()
        cfg = _Config({'bandcamp.embed_tags': True})

        ms._process_bandcamp_source(pp, {'album': 'Album One'}, cfg, _runtime(client), 'Song One', 'Artist One')

        assert pp['bandcamp_url'] == 'https://x.bandcamp.com/album/z'
        assert pp['bandcamp_label'] == 'Album Label'
        assert client.album_calls == [('Artist One', 'Album One')]

    def test_no_album_fallback_when_metadata_has_no_album(self):
        client = _FakeBandcampClient(track_result=None)
        pp = ms._blank_post_process_state()
        cfg = _Config({'bandcamp.embed_tags': True})

        ms._process_bandcamp_source(pp, {}, cfg, _runtime(client), 'Song One', 'Artist One')

        assert client.album_calls == []
        assert pp['bandcamp_url'] is None

    def test_no_match_leaves_pp_untouched(self):
        client = _FakeBandcampClient(track_result=None, album_result=None)
        pp = ms._blank_post_process_state()
        cfg = _Config({'bandcamp.embed_tags': True})

        ms._process_bandcamp_source(pp, {'album': 'Album One'}, cfg, _runtime(client), 'Song One', 'Artist One')

        assert pp['bandcamp_url'] is None
        assert pp['bandcamp_tags'] == []
        assert pp['bandcamp_label'] is None

    def test_embed_tags_disabled_skips_entirely(self):
        client = _FakeBandcampClient(track_result={'url': 'https://x.bandcamp.com/track/y', 'tags': ['x'], 'label': 'L'})
        pp = ms._blank_post_process_state()
        cfg = _Config({'bandcamp.embed_tags': False})

        ms._process_bandcamp_source(pp, {'album': 'Album One'}, cfg, _runtime(client), 'Song One', 'Artist One')

        assert client.track_calls == []
        assert pp['bandcamp_url'] is None

    def test_missing_title_or_artist_skips(self):
        client = _FakeBandcampClient(track_result={'url': 'x', 'tags': [], 'label': None})
        pp = ms._blank_post_process_state()
        cfg = _Config({'bandcamp.embed_tags': True})

        ms._process_bandcamp_source(pp, {}, cfg, _runtime(client), '', 'Artist One')
        ms._process_bandcamp_source(pp, {}, cfg, _runtime(client), 'Song One', '')

        assert client.track_calls == []

    def test_no_bandcamp_worker_on_runtime_is_a_noop(self):
        pp = ms._blank_post_process_state()
        cfg = _Config({'bandcamp.embed_tags': True})
        runtime = types.SimpleNamespace()  # no bandcamp_worker attribute at all

        ms._process_bandcamp_source(pp, {'album': 'Album One'}, cfg, runtime, 'Song One', 'Artist One')

        assert pp['bandcamp_url'] is None


# ---------------------------------------------------------------------------
# End-to-end: embed_source_ids writes bandcamp_url/tags/label as real tags.
# ---------------------------------------------------------------------------


def test_embed_source_ids_writes_bandcamp_tags(monkeypatch):
    client = _FakeBandcampClient(track_result={
        'id': '123', 'url': 'https://x.bandcamp.com/track/y', 'title': 'Song One',
        'tags': ['idm', 'ambient'], 'label': 'Test Label',
    })
    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    runtime = _runtime(client)

    monkeypatch.setattr(ms, 'get_config_manager', lambda: _Config({
        'metadata_enhancement.enabled': True,
        'metadata_enhancement.tags.genre_merge': True,
        'bandcamp.embed_tags': True,
        'bandcamp.tags.url': True,
        'bandcamp.tags.label': True,
        'bandcamp.tags.genre_merge': True,
    }))
    monkeypatch.setattr(ms, 'get_mutagen_symbols', lambda: symbols)
    monkeypatch.setattr(ms, 'get_database', lambda: None)

    metadata = {
        'source': 'bandcamp', 'title': 'Song One', 'artist': 'Artist One',
        'album_artist': 'Artist One', 'album': 'Album One',
    }

    me.embed_source_ids(audio, metadata, context={'track_info': {}, 'original_search_result': {}}, runtime=runtime)

    url_tags = [f for f in audio.tags.added if f.kind == 'TXXX' and f.kwargs.get('desc') == 'BANDCAMP_URL']
    assert len(url_tags) == 1
    assert url_tags[0].kwargs['text'] == ['https://x.bandcamp.com/track/y']

    label_tags = [f for f in audio.tags.added if f.kind == 'TPUB']
    assert len(label_tags) == 1
    assert label_tags[0].kwargs['text'] == ['Test Label']

    genre_tags = [f for f in audio.tags.added if f.kind == 'TCON']
    assert len(genre_tags) == 1
    assert 'Idm' in genre_tags[0].kwargs['text'][0]


def test_embed_source_ids_respects_url_and_label_toggles_independently(monkeypatch):
    client = _FakeBandcampClient(track_result={
        'url': 'https://x.bandcamp.com/track/y', 'tags': [], 'label': 'Test Label',
    })
    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    runtime = _runtime(client)

    monkeypatch.setattr(ms, 'get_config_manager', lambda: _Config({
        'metadata_enhancement.enabled': True,
        'bandcamp.embed_tags': True,
        'bandcamp.tags.url': False,
        'bandcamp.tags.label': True,
    }))
    monkeypatch.setattr(ms, 'get_mutagen_symbols', lambda: symbols)
    monkeypatch.setattr(ms, 'get_database', lambda: None)

    metadata = {
        'source': 'bandcamp', 'title': 'Song One', 'artist': 'Artist One',
        'album_artist': 'Artist One', 'album': 'Album One',
    }

    me.embed_source_ids(audio, metadata, context={'track_info': {}, 'original_search_result': {}}, runtime=runtime)

    assert not [f for f in audio.tags.added if f.kind == 'TXXX' and f.kwargs.get('desc') == 'BANDCAMP_URL']
    assert [f for f in audio.tags.added if f.kind == 'TPUB']
