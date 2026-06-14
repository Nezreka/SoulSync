"""Engine search_with_fallback is quality-aware: it falls through to the next
source when the current source can deliver no target-satisfying quality, but
returns RAW tracks (match-filtering happens later in the orchestrator).
"""

import asyncio

import pytest

from core.download_engine import engine as engine_mod
from core.download_engine.engine import DownloadEngine
from core.quality.model import AudioQuality, QualityTarget


class _Cand:
    def __init__(self, aq, name):
        self.audio_quality = aq
        self.name = name


class _FakePlugin:
    def __init__(self, tracks):
        self._tracks = tracks
        self.searched = False

    def is_configured(self):
        return True

    async def search(self, query, timeout=None, progress_callback=None):
        self.searched = True
        return (self._tracks, [])


FLAC = AudioQuality('flac', sample_rate=44100, bit_depth=16)
MP3 = AudioQuality('mp3', bitrate=320)
WANT_FLAC_ONLY = [QualityTarget(label='FLAC 16', format='flac', bit_depth=16)]


def _engine_with(plugins):
    eng = object.__new__(DownloadEngine)
    eng._plugins = plugins
    return eng


def _patch_profile(monkeypatch, targets, fallback_enabled):
    monkeypatch.setattr(
        engine_mod, 'load_profile_targets',
        lambda: (targets, fallback_enabled),
    )


def test_escalates_to_next_source_when_first_cannot_meet_target(monkeypatch):
    _patch_profile(monkeypatch, WANT_FLAC_ONLY, True)
    first = _FakePlugin([_Cand(MP3, 'a-mp3')])
    second = _FakePlugin([_Cand(FLAC, 'b-flac')])
    eng = _engine_with({'first': first, 'second': second})

    tracks, _ = asyncio.run(eng.search_with_fallback('q', ['first', 'second']))

    assert [t.name for t in tracks] == ['b-flac']  # escalated to FLAC source
    assert second.searched is True


def test_stops_on_first_satisfying_source(monkeypatch):
    _patch_profile(monkeypatch, WANT_FLAC_ONLY, True)
    first = _FakePlugin([_Cand(FLAC, 'a-flac')])
    second = _FakePlugin([_Cand(FLAC, 'b-flac')])
    eng = _engine_with({'first': first, 'second': second})

    tracks, _ = asyncio.run(eng.search_with_fallback('q', ['first', 'second']))

    assert [t.name for t in tracks] == ['a-flac']
    assert second.searched is False  # never queried — source priority king


def test_returns_raw_tracks_not_pruned(monkeypatch):
    # A source satisfied by one candidate must still return ALL its tracks so
    # the orchestrator's match filter can pick the correct one.
    _patch_profile(monkeypatch, WANT_FLAC_ONLY, True)
    first = _FakePlugin([_Cand(MP3, 'wrong-but-present'), _Cand(FLAC, 'flac')])
    eng = _engine_with({'first': first})

    tracks, _ = asyncio.run(eng.search_with_fallback('q', ['first']))

    names = {t.name for t in tracks}
    assert names == {'wrong-but-present', 'flac'}  # nothing pruned


def test_no_source_satisfies_fallback_on_returns_first_source(monkeypatch):
    _patch_profile(monkeypatch, WANT_FLAC_ONLY, True)
    first = _FakePlugin([_Cand(MP3, 'a-mp3')])
    second = _FakePlugin([_Cand(MP3, 'b-mp3')])
    eng = _engine_with({'first': first, 'second': second})

    tracks, _ = asyncio.run(eng.search_with_fallback('q', ['first', 'second']))

    assert [t.name for t in tracks] == ['a-mp3']  # source priority for fallback


def test_no_source_satisfies_fallback_off_returns_empty(monkeypatch):
    _patch_profile(monkeypatch, WANT_FLAC_ONLY, False)
    first = _FakePlugin([_Cand(MP3, 'a-mp3')])
    eng = _engine_with({'first': first})

    tracks, albums = asyncio.run(eng.search_with_fallback('q', ['first']))

    assert tracks == []
    assert albums == []
