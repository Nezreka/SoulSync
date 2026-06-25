"""Engine ``search_with_fallback`` is the PRIORITY-mode search path and is
deliberately quality-agnostic: the first source in the chain that returns any
tracks wins (source order is king), byte-for-byte like the pre-quality-system
hybrid loop (#896 review #3). It skips unconfigured/unavailable sources,
swallows per-source exceptions, and returns RAW tracks (match-filtering +
final quality ranking happen later). Cross-source quality pooling lives in
best_quality mode (``search_all_sources``), not here.
"""

import asyncio

import pytest

from core.download_engine.engine import DownloadEngine
from core.quality.model import AudioQuality


class _Cand:
    def __init__(self, aq, name):
        self.audio_quality = aq
        self.name = name


class _FakePlugin:
    def __init__(self, tracks, configured=True, raises=False):
        self._tracks = tracks
        self._configured = configured
        self._raises = raises
        self.searched = False

    def is_configured(self):
        return self._configured

    async def search(self, query, timeout=None, progress_callback=None):
        self.searched = True
        if self._raises:
            raise RuntimeError("boom")
        return (self._tracks, [])


FLAC = AudioQuality('flac', sample_rate=44100, bit_depth=16)
MP3 = AudioQuality('mp3', bitrate=320)
MP3_NO_BITRATE = AudioQuality('mp3')  # slskd often omits bitrate


def _engine_with(plugins):
    eng = object.__new__(DownloadEngine)
    eng._plugins = plugins
    return eng


def test_returns_first_source_with_tracks_source_priority_king():
    first = _FakePlugin([_Cand(MP3, 'a-mp3')])
    second = _FakePlugin([_Cand(FLAC, 'b-flac')])
    eng = _engine_with({'first': first, 'second': second})

    tracks, _ = asyncio.run(eng.search_with_fallback('q', ['first', 'second']))

    assert [t.name for t in tracks] == ['a-mp3']  # first source wins regardless of quality
    assert second.searched is False               # never queried — priority is king


def test_metadata_less_mp3_still_wins_in_priority_mode():
    """An mp3 whose bitrate slskd omitted must NOT be deprioritised in priority
    mode — the whole point of #896 review #3."""
    first = _FakePlugin([_Cand(MP3_NO_BITRATE, 'a-mp3')])
    second = _FakePlugin([_Cand(FLAC, 'b-flac')])
    eng = _engine_with({'first': first, 'second': second})

    tracks, _ = asyncio.run(eng.search_with_fallback('q', ['first', 'second']))

    assert [t.name for t in tracks] == ['a-mp3']
    assert second.searched is False


def test_skips_to_next_source_when_first_empty():
    first = _FakePlugin([])
    second = _FakePlugin([_Cand(FLAC, 'b-flac')])
    eng = _engine_with({'first': first, 'second': second})

    tracks, _ = asyncio.run(eng.search_with_fallback('q', ['first', 'second']))

    assert [t.name for t in tracks] == ['b-flac']
    assert second.searched is True


def test_returns_raw_tracks_not_pruned():
    # All of the winning source's tracks come back so the orchestrator's match
    # filter can pick the correct one.
    first = _FakePlugin([_Cand(MP3, 'one'), _Cand(FLAC, 'two')])
    eng = _engine_with({'first': first})

    tracks, _ = asyncio.run(eng.search_with_fallback('q', ['first']))

    assert {t.name for t in tracks} == {'one', 'two'}


def test_skips_unconfigured_source():
    first = _FakePlugin([_Cand(FLAC, 'a')], configured=False)
    second = _FakePlugin([_Cand(MP3, 'b')])
    eng = _engine_with({'first': first, 'second': second})

    tracks, _ = asyncio.run(eng.search_with_fallback('q', ['first', 'second']))

    assert [t.name for t in tracks] == ['b']
    assert first.searched is False


def test_swallows_per_source_exception_and_continues():
    first = _FakePlugin([], raises=True)
    second = _FakePlugin([_Cand(MP3, 'b')])
    eng = _engine_with({'first': first, 'second': second})

    tracks, _ = asyncio.run(eng.search_with_fallback('q', ['first', 'second']))

    assert [t.name for t in tracks] == ['b']


def test_returns_empty_when_all_sources_empty():
    first = _FakePlugin([])
    second = _FakePlugin([])
    eng = _engine_with({'first': first, 'second': second})

    tracks, albums = asyncio.run(eng.search_with_fallback('q', ['first', 'second']))

    assert tracks == []
    assert albums == []
