"""Tests for core/search/basic.py — basic Soulseek file search."""

from __future__ import annotations

from core.search import basic


class _SearchTrack:
    def __init__(self, name, quality_score, **extra):
        self.__dict__['name'] = name
        self.__dict__['quality_score'] = quality_score
        self.__dict__.update(extra)


class _SearchAlbum:
    def __init__(self, name, quality_score, tracks=None, **extra):
        self.__dict__['name'] = name
        self.__dict__['quality_score'] = quality_score
        self.__dict__['tracks'] = tracks or []
        self.__dict__.update(extra)


class _FakeSoulseek:
    def __init__(self, tracks=None, albums=None):
        self._tracks = tracks or []
        self._albums = albums or []

    async def search(self, query):
        return self._tracks, self._albums


def _run_async(coro):
    """Test-friendly run_async — drains a coroutine synchronously."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.get_event_loop().is_running() else None


def _sync_run_async(coro):
    """Threadless awaitable runner using a fresh loop."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_returns_empty_for_no_results():
    client = _FakeSoulseek(tracks=[], albums=[])
    result = basic.run_basic_soulseek_search('q', client, _sync_run_async)
    assert result == []


def test_tracks_are_tagged_with_result_type():
    track = _SearchTrack('T1', 0.5, username='u', filename='f.mp3', size=1, bitrate=320)
    client = _FakeSoulseek(tracks=[track])
    result = basic.run_basic_soulseek_search('q', client, _sync_run_async)
    assert result[0]['result_type'] == 'track'
    assert result[0]['name'] == 'T1'


def test_albums_get_tracks_serialized_and_tagged():
    inner_track = _SearchTrack('inner', 0.5, filename='in.mp3')
    album = _SearchAlbum('A1', 0.9, tracks=[inner_track], username='u')
    client = _FakeSoulseek(albums=[album])
    result = basic.run_basic_soulseek_search('q', client, _sync_run_async)
    assert result[0]['result_type'] == 'album'
    assert result[0]['name'] == 'A1'
    assert isinstance(result[0]['tracks'], list)
    assert result[0]['tracks'][0]['name'] == 'inner'


def test_results_sorted_by_quality_score_desc():
    low = _SearchTrack('low', 0.1)
    high = _SearchTrack('high', 0.9)
    mid = _SearchTrack('mid', 0.5)
    client = _FakeSoulseek(tracks=[low, mid, high])
    result = basic.run_basic_soulseek_search('q', client, _sync_run_async)
    assert [r['name'] for r in result] == ['high', 'mid', 'low']


def test_albums_and_tracks_intermingled_by_quality():
    track = _SearchTrack('mid_t', 0.5)
    album = _SearchAlbum('top_a', 0.9, tracks=[])
    client = _FakeSoulseek(tracks=[track], albums=[album])
    result = basic.run_basic_soulseek_search('q', client, _sync_run_async)
    assert result[0]['name'] == 'top_a'
    assert result[1]['name'] == 'mid_t'


def test_missing_quality_score_treated_as_zero():
    no_score = _SearchTrack('n', None)
    no_score.__dict__.pop('quality_score', None)
    has_score = _SearchTrack('h', 0.5)
    client = _FakeSoulseek(tracks=[no_score, has_score])
    result = basic.run_basic_soulseek_search('q', client, _sync_run_async)
    # has_score (0.5) ranks above no_score (treated as 0)
    assert result[0]['name'] == 'h'
