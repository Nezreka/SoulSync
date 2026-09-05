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


# ── Source-targeted search (new in basic-search redesign) ──────────────


class _FakeOrchestratorMulti:
    """Orchestrator stand-in with per-source clients.

    ``search()`` is the default orchestrator path (single-source mode or
    first hybrid source). ``client(name)`` returns the per-source client
    so a source-targeted basic search can call ``.search()`` directly on
    the specific source rather than going through the chain.
    """

    def __init__(self, default_results, per_source_results, fail_unknown=False):
        self._default = default_results
        self._sources = per_source_results
        self._fail_unknown = fail_unknown
        self.default_search_calls = 0
        self.per_source_calls = {}

    async def search(self, query, timeout=None, progress_callback=None, exclude_sources=None):
        self.default_search_calls += 1
        return self._default

    def client(self, name):
        if name not in self._sources:
            if self._fail_unknown:
                return None
            return None
        plugin = _FakeSoulseek(tracks=self._sources[name][0], albums=self._sources[name][1])
        # Record which sources got asked for.
        self.per_source_calls.setdefault(name, 0)
        self.per_source_calls[name] += 1
        return plugin


def test_source_param_routes_to_specific_client():
    """``source='tidal'`` calls the Tidal client directly, NOT the
    orchestrator's chain. Ensures the per-source basic search bypasses
    the hybrid-first selection so users can target any active source."""
    tidal_track = _SearchTrack('From Tidal', 0.9, username='tidal')
    soul_track = _SearchTrack('From Soulseek', 0.5, username='peer')

    orch = _FakeOrchestratorMulti(
        default_results=([soul_track], []),
        per_source_results={
            'tidal': ([tidal_track], []),
            'soulseek': ([soul_track], []),
        },
    )
    result = basic.run_basic_search('q', orch, _sync_run_async, source='tidal')

    # Tidal result returned, NOT soulseek result.
    assert len(result) == 1
    assert result[0]['name'] == 'From Tidal'
    # Orchestrator default chain NOT consulted.
    assert orch.default_search_calls == 0
    # Tidal client was called exactly once.
    assert orch.per_source_calls.get('tidal') == 1
    assert result[0]['source'] == 'tidal'


def test_scoped_prowlarr_result_keeps_release_title_and_uses_entity_artist():
    """Indexer release titles are not semantic artist/title columns.

    A Library-v2 search has server-validated entity metadata, so it should use
    that identity while preserving Prowlarr's raw value as the release title.
    """
    meta = {
        'protocol': 'usenet',
        'indexer': 'NZBGeek',
        'release_title': 'TEAM-WEB-Various.Artists-Compilation-FLAC-2026',
    }
    track = _real_track(
        username='usenet',
        artist='TEAM',
        title='WEB-Various.Artists-Compilation',
        _source_metadata=meta,
    )
    album = _real_album(
        username='usenet',
        artist='TEAM',
        album_title='WEB-Various.Artists-Compilation',
        tracks=[track],
    )
    client = _FakeSoulseek(tracks=[track], albums=[album])

    result = basic.run_basic_search(
        'Real Artist Real Album',
        client,
        _sync_run_async,
        source='usenet',
        entity_context={
            'album_id': 7,
            'artist_name': 'Real Artist',
            'album_name': 'Real Album',
        },
    )

    # Album-scoped Prowlarr searches expose one release row, not the legacy
    # track+album duplicate pair.
    assert len(result) == 1
    assert result[0]['result_type'] == 'album'
    assert result[0]['artist'] == 'Real Artist'
    assert result[0]['matched_album_title'] == 'Real Album'
    assert result[0]['release_title'] == meta['release_title']
    assert result[0]['source'] == 'usenet'


def test_no_source_param_falls_through_to_orchestrator_default():
    """When ``source`` is omitted, behaviour is identical to pre-redesign:
    orchestrator.search() is called and routes to the configured source
    (single-source mode) or first hybrid source. Preserves the existing
    contract for callers that don't opt in to per-source targeting."""
    track = _SearchTrack('Default', 0.7)
    orch = _FakeOrchestratorMulti(
        default_results=([track], []),
        per_source_results={'tidal': ([], [])},
    )
    result = basic.run_basic_search('q', orch, _sync_run_async)

    assert result[0]['name'] == 'Default'
    assert orch.default_search_calls == 1
    assert orch.per_source_calls == {}


def test_unknown_source_falls_back_to_orchestrator():
    """Bogus source name (e.g. user-edited config with a typo) falls
    through to the orchestrator default rather than returning an empty
    list silently. Logged as a warning so misconfiguration is visible."""
    track = _SearchTrack('Default', 0.7)
    orch = _FakeOrchestratorMulti(
        default_results=([track], []),
        per_source_results={'tidal': ([], [])},
    )
    result = basic.run_basic_search('q', orch, _sync_run_async, source='nonexistent')

    assert result[0]['name'] == 'Default'
    assert orch.default_search_calls == 1


def test_backwards_compat_alias_still_works():
    """``run_basic_soulseek_search`` is the legacy name; any external
    caller that hasn't been updated must keep working. Aliased to
    ``run_basic_search`` so both call the same code path."""
    track = _SearchTrack('Compat', 0.5)
    client = _FakeSoulseek(tracks=[track])
    result = basic.run_basic_soulseek_search('q', client, _sync_run_async)
    assert result[0]['name'] == 'Compat'
    assert basic.run_basic_soulseek_search is basic.run_basic_search


# ── The real dataclasses ───────────────────────────────────────────────
#
# Every fake above stores ``quality_score`` in ``__dict__``. The real
# ``SearchResult``/``AlbumResult`` declare it as a ``@property``, which
# ``__dict__`` does not contain — so the fakes tested a shape that never
# occurs in production, and the serialiser silently dropped the score for
# years. These tests use the production classes so that cannot recur.


def _real_track(**over):
    from core.download_plugins.types import TrackResult
    kwargs = dict(
        username='peer', filename='a.flac', size=10_000_000, bitrate=1000,
        duration=200_000, quality='flac', free_upload_slots=1,
        upload_speed=1_000_000, queue_length=0, artist='A', title='T',
        album='Al', track_number=1,
    )
    kwargs.update(over)
    return TrackResult(**kwargs)


def _real_album(**over):
    from core.download_plugins.types import AlbumResult
    kwargs = dict(
        username='peer', album_path='/p', album_title='Al', artist='A',
        track_count=10, total_size=100_000_000, tracks=[_real_track()],
        dominant_quality='flac',
    )
    kwargs.update(over)
    return AlbumResult(**kwargs)


def test_real_track_result_carries_its_quality_score_to_the_payload():
    """The score is a property; without an explicit copy it never ships."""
    track = _real_track(quality='flac', bitrate=1000)
    client = _FakeSoulseek(tracks=[track])

    result = basic.run_basic_search('q', client, _sync_run_async)

    assert 'quality_score' not in track.__dict__, 'precondition: it is a property'
    assert result[0]['quality_score'] == track.quality_score
    assert result[0]['quality_score'] > 0


def test_real_album_result_carries_its_quality_score_to_the_payload():
    album = _real_album()
    client = _FakeSoulseek(albums=[album])

    result = basic.run_basic_search('q', client, _sync_run_async)

    assert result[0]['quality_score'] == album.quality_score
    assert result[0]['quality_score'] > 0


def test_album_tracks_carry_their_own_quality_score():
    """The per-track rows inside an album are rendered as their own list in
    the UI and sortable there, so they need the score too."""
    album = _real_album(tracks=[_real_track(quality='mp3', bitrate=128)])
    client = _FakeSoulseek(albums=[album])

    result = basic.run_basic_search('q', client, _sync_run_async)

    assert result[0]['tracks'][0]['quality_score'] == album.tracks[0].quality_score


def test_album_track_payload_does_not_alias_the_live_object():
    """The serialised dict must be a copy — handing out ``track.__dict__``
    means a caller mutating the response mutates the search result."""
    album = _real_album()
    client = _FakeSoulseek(albums=[album])

    result = basic.run_basic_search('q', client, _sync_run_async)
    result[0]['tracks'][0]['title'] = 'MUTATED'

    assert album.tracks[0].title == 'T'


def test_real_results_are_actually_ordered_by_quality():
    """The end-to-end claim in the module docstring, on real objects: a FLAC
    outranks a 128kbps MP3."""
    flac = _real_track(title='flac', quality='flac', bitrate=1000)
    mp3 = _real_track(title='mp3', quality='mp3', bitrate=128)
    client = _FakeSoulseek(tracks=[mp3, flac])

    result = basic.run_basic_search('q', client, _sync_run_async)

    assert [r['title'] for r in result] == ['flac', 'mp3']


def test_a_result_whose_score_raises_does_not_kill_the_search():
    """``quality_score`` calls ``.lower()`` on the quality string; a source
    that leaves it None must cost that one row its score, not the search."""
    broken = _real_track(title='broken', quality=None)
    fine = _real_track(title='fine', quality='flac')
    client = _FakeSoulseek(tracks=[broken, fine])

    result = basic.run_basic_search('q', client, _sync_run_async)

    assert {r['title'] for r in result} == {'broken', 'fine'}
    assert next(r for r in result if r['title'] == 'broken')['quality_score'] == 0.0


def test_source_targeted_search_serialises_albums_with_tracks():
    """Source-targeted path goes through the same normaliser as the
    default path, so albums returned via a specific source still get
    their tracks serialised + ``result_type='album'`` tagged."""
    inner = _SearchTrack('inner', 0.5, filename='in.mp3')
    album = _SearchAlbum('TidalAlbum', 0.9, tracks=[inner], username='tidal')
    orch = _FakeOrchestratorMulti(
        default_results=([], []),
        per_source_results={'tidal': ([], [album])},
    )

    result = basic.run_basic_search('q', orch, _sync_run_async, source='tidal')

    assert result[0]['result_type'] == 'album'
    assert result[0]['tracks'][0]['name'] == 'inner'
