"""Tests for core/artists/quality.py — artist quality enhancement helper."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.artists import quality as aq


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _SpotifyTrack:
    id: str = 'sp-1'
    name: str = 'Found'
    artists: list = None
    album: str = 'Album'
    duration_ms: int = 200000
    image_url: str = ''
    popularity: int = 50
    preview_url: str = ''
    external_urls: dict = None
    album_type: str = 'album'
    release_date: str = '2024-01-01'

    def __post_init__(self):
        if self.artists is None:
            self.artists = ['Artist Name']
        if self.external_urls is None:
            self.external_urls = {}


class _FakeSpotify:
    def __init__(self, track_details=None, search_results=None, album=None):
        self._track_details = track_details
        self._search_results = search_results or []
        self._album = album
        self.search_calls = []

    def get_track_details(self, track_id):
        return self._track_details

    def get_album(self, album_id):
        return self._album

    def search_tracks(self, query, limit=5):
        self.search_calls.append((query, limit))
        return self._search_results


class _FakeMatchingEngine:
    def generate_download_queries(self, track):
        return [f"{track.artists[0]} {track.name}"]

    def normalize_string(self, s):
        return (s or '').lower().strip()

    def similarity_score(self, a, b):
        if a == b:
            return 1.0
        if not a or not b:
            return 0.0
        return 0.95 if a in b or b in a else 0.0


class _FakeWishlist:
    def __init__(self):
        self.added = []

    def add_spotify_track_to_wishlist(self, **kwargs):
        self.added.append(kwargs)
        return True


class _FakeDatabase:
    def __init__(self, artist_detail=None):
        self._artist_detail = artist_detail or {'success': False}

    def get_artist_full_detail(self, artist_id):
        return self._artist_detail


def _build_deps(
    *,
    spotify=None,
    matching_engine=None,
    artist_detail=None,
    wishlist=None,
    fallback_client=None,
    fallback_source=None,
    search_sources=None,
    profile_id=1,
    quality_tier=('mp3_320', 4),
):
    """Build deps for tests.

    ``search_sources`` is the new contract — list of ``(name, client)``
    pairs the multi-source search dispatches across. For convenience,
    ``fallback_client`` + ``fallback_source`` are still supported and
    auto-translate to a single-source list when ``search_sources``
    isn't passed (preserves the older test ergonomics).
    """
    if search_sources is None:
        # Default: build a single-source list from fallback_client +
        # fallback_source, mirroring the legacy single-source contract.
        if fallback_source is None:
            fallback_source = 'spotify' if spotify is not None else 'itunes'
        if fallback_client is None and fallback_source == 'spotify':
            fallback_client = spotify
        search_sources = (
            [(fallback_source, fallback_client)] if fallback_client else []
        )
        # Mirror web_server._resolve_search_sources: Spotify is always
        # added to the source list when a Spotify client is configured,
        # alongside whatever else the user has connected. Tests passing
        # ``spotify=`` get Spotify in the source list automatically so
        # the direct-lookup fast path can find it.
        if spotify is not None and not any(name == 'spotify' for name, _ in search_sources):
            search_sources = [('spotify', spotify)] + list(search_sources)
    deps = aq.ArtistQualityDeps(
        matching_engine=matching_engine or _FakeMatchingEngine(),
        get_database=lambda: _FakeDatabase(artist_detail=artist_detail),
        get_wishlist_service=lambda: wishlist or _FakeWishlist(),
        get_current_profile_id=lambda: profile_id,
        get_quality_tier_from_extension=lambda fp: quality_tier,
        get_metadata_search_sources=lambda: list(search_sources),
    )
    return deps


def _artist_with_track(*, track_id='t1', file_path='/file.mp3',
                       spotify_tid=None, deezer_tid=None, itunes_tid=None,
                       soul_id=None):
    return {
        'success': True,
        'artist': {'name': 'Artist Name'},
        'albums': [{
            'id': 'a1',
            'title': 'Album X',
            'tracks': [{
                'id': track_id,
                'title': 'Track One',
                'file_path': file_path,
                'spotify_track_id': spotify_tid,
                'deezer_id': deezer_tid,
                'itunes_track_id': itunes_tid,
                'soul_id': soul_id,
                'track_number': 1,
                'duration': 180000,
                'bitrate': 320,
            }],
        }],
    }


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_no_track_ids_returns_400():
    deps = _build_deps()
    payload, status = aq.enhance_artist_quality('artist-1', [], deps)
    assert status == 400
    assert payload == {"success": False, "error": "No track IDs provided"}


def test_artist_not_found_returns_404():
    deps = _build_deps(artist_detail={'success': False})
    payload, status = aq.enhance_artist_quality('artist-x', ['t1'], deps)
    assert status == 404
    assert payload == {"success": False, "error": "Artist not found"}


# ---------------------------------------------------------------------------
# Spotify direct lookup (priority 1)
# ---------------------------------------------------------------------------

def test_spotify_direct_lookup_via_track_id_uses_raw_data():
    """Track has spotify_track_id → get_track_details, raw_data fed to wishlist."""
    raw = {'id': 'sp-stored', 'name': 'Track One', 'artists': [{'name': 'Artist Name'}],
           'album': {'name': 'Album X', 'images': [{'url': 'http://i'}]}, 'duration_ms': 180000}
    spotify = _FakeSpotify(track_details={'raw_data': raw})
    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=spotify,
        artist_detail=_artist_with_track(spotify_tid='sp-stored'),
        wishlist=wishlist,
    )

    payload, status = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert status == 200
    assert payload['enhanced_count'] == 1
    assert wishlist.added[0]['spotify_track_data'] == raw


def test_spotify_direct_lookup_enhanced_format_rebuilds_payload():
    """Track details without raw_data → rebuild wishlist-shape payload
    from the enhanced top-level fields. Spotify enhanced shape returns
    ``artists`` as a list of strings; the converter normalizes to
    Spotify's wishlist shape (``[{'name': ...}]``). Album images stay
    empty when the source doesn't surface them on the enhanced dict —
    the wishlist re-download fetches art at download time."""
    enhanced = {'id': 'sp-stored', 'name': 'Track One',
                'artists': ['Artist Name'],
                'album': {'id': 'alb-id', 'name': 'Album X'},
                'duration_ms': 180000, 'track_number': 1, 'disc_number': 1}
    spotify = _FakeSpotify(track_details=enhanced)
    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=spotify,
        artist_detail=_artist_with_track(spotify_tid='sp-stored'),
        wishlist=wishlist,
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert payload['enhanced_count'] == 1
    md = wishlist.added[0]['spotify_track_data']
    assert md['id'] == 'sp-stored'
    assert md['name'] == 'Track One'
    # Enhanced format: artists normalized from [str] → [{'name': str}].
    assert md['artists'] == [{'name': 'Artist Name'}]
    assert md['album']['name'] == 'Album X'
    assert md['album']['artists'] == [{'name': 'Artist Name'}]


# ---------------------------------------------------------------------------
# Spotify search fallback (priority 2)
# ---------------------------------------------------------------------------

def test_spotify_search_fallback_when_no_stored_id():
    """No spotify_track_id → multi-source text search runs, builds
    wishlist payload from the source-native Track object via
    ``_build_payload_from_track`` (not ``get_track_details`` — search
    results already carry enough Track-shape fields, no extra API call
    needed)."""
    track = _SpotifyTrack(id='sp-search', name='Track One',
                           artists=['Artist Name'])
    track.album = 'Album X'
    spotify = _FakeSpotify(search_results=[track])
    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=spotify,
        artist_detail=_artist_with_track(spotify_tid=None),
        wishlist=wishlist,
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert payload['enhanced_count'] == 1
    md = wishlist.added[0]['spotify_track_data']
    assert md['id'] == 'sp-search'
    assert md['name'] == 'Track One'
    assert md['artists'] == [{'name': 'Artist Name'}]
    assert md['album']['name'] == 'Album X'


# ---------------------------------------------------------------------------
# Fallback source (iTunes/Deezer)
# ---------------------------------------------------------------------------

def test_fallback_source_when_spotify_none():
    """Spotify client None → iTunes/fallback search runs."""
    fallback_track = _SpotifyTrack(id='it-1', name='Track One', artists=['Artist Name'],
                                    image_url='http://it')
    fallback_track.track_number = 1
    fallback_track.disc_number = 1
    fallback = type('FB', (), {
        'search_tracks': lambda self, q, limit=5: [fallback_track],
    })()
    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(),
        wishlist=wishlist,
        fallback_client=fallback,
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert payload['enhanced_count'] == 1
    md = wishlist.added[0]['spotify_track_data']
    assert md['id'] == 'it-1'
    assert md['album']['images'] == [{'url': 'http://it', 'height': 600, 'width': 600}]


def test_dispatches_through_primary_source_not_spotify_specific():
    """Architectural pin: when the user's primary metadata source is
    Discogs (or any non-Spotify source), the enhance flow searches
    THAT source's client, not Spotify. Pre-fix the flow had a
    hardcoded Spotify-direct → Spotify-search → iTunes-fallback chain
    that ignored the user's actual configured primary source.
    """
    discogs_track = _SpotifyTrack(id='dc-1', name='Track One',
                                   artists=['Artist Name'])
    discogs_track.track_number = 1
    discogs_track.disc_number = 1
    discogs_track.album = 'Album X'
    discogs_calls = []

    class _DiscogsStub:
        def search_tracks(self, q, limit=5):
            discogs_calls.append(q)
            return [discogs_track]

    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(),
        wishlist=wishlist,
        fallback_client=_DiscogsStub(),
        fallback_source='discogs',
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    # Discogs (the configured primary) was the one searched; match queued.
    assert discogs_calls, "Primary source (Discogs) was not searched"
    assert payload['enhanced_count'] == 1
    assert wishlist.added[0]['spotify_track_data']['id'] == 'dc-1'


def test_spotify_direct_lookup_runs_as_fast_path_then_falls_back():
    """Architectural pin: Spotify direct-lookup is a fast-path
    optimization, NOT a primary-source gate. When the user has Spotify
    configured AND a stored spotify_track_id, direct lookup runs first
    regardless of which other sources are wired. If it returns nothing,
    the multi-source parallel search runs and the cross-source best
    match wins (in this test, Discogs).

    This pins the post-refactor behavior — pre-refactor direct lookup
    was gated on Spotify being the active primary source, which broke
    enhance for users without Spotify primary even when they had a
    stored Spotify ID.
    """
    spotify_calls = []

    class _SpotifyStub:
        def get_track_details(self, tid):
            spotify_calls.append(('details', tid))
            return None
        def search_tracks(self, q, limit=10):
            spotify_calls.append(('search', q))
            return []

    discogs_track = _SpotifyTrack(id='dc-1', name='Track One',
                                   artists=['Artist Name'])
    discogs_track.track_number = 1
    discogs_track.disc_number = 1
    discogs_track.album = 'Album X'

    class _DiscogsStub:
        def search_tracks(self, q, limit=10):
            return [discogs_track]

    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=_SpotifyStub(),
        artist_detail=_artist_with_track(spotify_tid='sp-stored'),
        wishlist=wishlist,
        fallback_client=_DiscogsStub(),
        fallback_source='discogs',
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    # Fast path was attempted (Spotify configured + stored ID present).
    assert ('details', 'sp-stored') in spotify_calls
    # Fast path returned None → multi-source search ran → Discogs won.
    assert payload['enhanced_count'] == 1
    assert wishlist.added[0]['spotify_track_data']['id'] == 'dc-1'


# ---------------------------------------------------------------------------
# Direct-lookup-by-stored-ID (priority 1) — applies to every source
# with a stored ID column, not just Spotify
# ---------------------------------------------------------------------------

def test_direct_lookup_via_deezer_id_skips_text_search():
    """Library track has stored deezer_id + Deezer is configured →
    enhance fast-paths via deezer_client.get_track_details(id) and skips
    fuzzy text search entirely. Mirrors what Download Discography does
    (stable IDs, no fuzzy matching). Pre-fix Deezer-primary users went
    through text search even when the deezer_id was already on the row.
    """
    deezer_calls = []
    enhanced_dict = {
        'id': '12345', 'name': 'Track One',
        'artists': ['Artist Name'],
        'album': {'id': 'alb-1', 'name': 'Album X'},
        'duration_ms': 180000, 'track_number': 1, 'disc_number': 1,
    }

    class _DeezerStub:
        def get_track_details(self, tid):
            deezer_calls.append(('details', tid))
            return enhanced_dict
        def search_tracks(self, q, limit=10):
            deezer_calls.append(('search', q))
            return []

    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(deezer_tid='12345'),
        wishlist=wishlist,
        fallback_client=_DeezerStub(),
        fallback_source='deezer',
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert payload['enhanced_count'] == 1
    # Direct-lookup ran, text search did NOT (fast path skipped it).
    assert ('details', '12345') in deezer_calls
    assert not any(call[0] == 'search' for call in deezer_calls)
    md = wishlist.added[0]['spotify_track_data']
    assert md['id'] == '12345'
    assert md['artists'] == [{'name': 'Artist Name'}]


def test_direct_lookup_via_itunes_id_skips_text_search():
    """Stored itunes_track_id triggers iTunes direct lookup. Same
    contract as Spotify / Deezer — get_track_details called, search
    not."""
    itunes_calls = []
    enhanced_dict = {
        'id': 'it-9001', 'name': 'Track One',
        'artists': ['Artist Name'],
        'album': {'id': 'alb-it', 'name': 'Album X'},
        'duration_ms': 180000,
    }

    class _ItunesStub:
        def get_track_details(self, tid):
            itunes_calls.append(('details', tid))
            return enhanced_dict
        def search_tracks(self, q, limit=10):
            itunes_calls.append(('search', q))
            return []

    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(itunes_tid='it-9001'),
        wishlist=wishlist,
        fallback_client=_ItunesStub(),
        fallback_source='itunes',
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert payload['enhanced_count'] == 1
    assert ('details', 'it-9001') in itunes_calls
    assert not any(call[0] == 'search' for call in itunes_calls)


def test_direct_lookup_prefers_user_primary_source():
    """Track has stored IDs on multiple sources; user's configured
    primary source is tried first. Pin: a Deezer-primary user with
    both spotify_track_id and deezer_id stored gets the Deezer payload
    (correct cover art / album shape for their setup), not whichever
    source happened to come first in registry order.
    """
    spotify_calls = []
    deezer_calls = []

    spotify_enhanced = {
        'id': 'sp-1', 'name': 'Track One',
        'artists': ['Artist Name'],
        'album': {'id': 'sp-alb', 'name': 'Album X'},
    }
    deezer_enhanced = {
        'id': 'dz-1', 'name': 'Track One',
        'artists': ['Artist Name'],
        'album': {'id': 'dz-alb', 'name': 'Album X'},
    }

    class _SpotifyStub:
        def get_track_details(self, tid):
            spotify_calls.append(tid)
            return spotify_enhanced

    class _DeezerStub:
        def get_track_details(self, tid):
            deezer_calls.append(tid)
            return deezer_enhanced

    # Patch get_primary_source to return 'deezer' so we don't depend
    # on the test-runner's actual config.
    import core.metadata.registry as registry_mod
    original = registry_mod.get_primary_source
    registry_mod.get_primary_source = lambda **kw: 'deezer'
    try:
        wishlist = _FakeWishlist()
        deps = _build_deps(
            spotify=_SpotifyStub(),
            artist_detail=_artist_with_track(
                spotify_tid='sp-stored', deezer_tid='dz-stored',
            ),
            wishlist=wishlist,
            fallback_client=_DeezerStub(),
            fallback_source='deezer',
        )
        payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)
    finally:
        registry_mod.get_primary_source = original

    assert payload['enhanced_count'] == 1
    # Deezer (primary) tried first and won — Spotify never queried.
    assert deezer_calls == ['dz-stored']
    assert spotify_calls == []
    md = wishlist.added[0]['spotify_track_data']
    assert md['id'] == 'dz-1'


def test_direct_lookup_falls_through_to_text_search_when_no_stored_ids():
    """Track has ZERO stored source IDs → direct lookup yields nothing
    → falls through to multi-source text search. Pin the contract:
    direct-lookup is best-effort, not required."""
    text_search_track = _SpotifyTrack(id='dz-search', name='Track One',
                                       artists=['Artist Name'])
    text_search_track.album = 'Album X'

    class _DeezerStub:
        def get_track_details(self, tid):
            raise AssertionError("should not be called — no stored ID")
        def search_tracks(self, q, limit=10):
            return [text_search_track]

    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(),  # no stored IDs
        wishlist=wishlist,
        fallback_client=_DeezerStub(),
        fallback_source='deezer',
    )
    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)
    assert payload['enhanced_count'] == 1
    assert wishlist.added[0]['spotify_track_data']['id'] == 'dz-search'


def test_direct_lookup_failure_falls_through_to_text_search():
    """Stored ID exists but direct lookup returns None (network blip,
    catalog removal, etc.) → flow falls through to text search rather
    than hard-failing. Pin: direct-lookup miss is non-fatal."""
    text_search_track = _SpotifyTrack(id='dz-search', name='Track One',
                                       artists=['Artist Name'])
    text_search_track.album = 'Album X'

    class _DeezerStub:
        def get_track_details(self, tid):
            return None  # direct lookup miss
        def search_tracks(self, q, limit=10):
            return [text_search_track]

    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(deezer_tid='9999'),
        wishlist=wishlist,
        fallback_client=_DeezerStub(),
        fallback_source='deezer',
    )
    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)
    assert payload['enhanced_count'] == 1
    # Text-search winner used.
    assert wishlist.added[0]['spotify_track_data']['id'] == 'dz-search'


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

def test_track_not_in_artist_detail_marked_failed():
    """Track ID provided but missing from artist's albums → failed_tracks entry."""
    deps = _build_deps(artist_detail=_artist_with_track(track_id='t1'))
    payload, _ = aq.enhance_artist_quality('artist-1', ['t99'], deps)

    assert payload['enhanced_count'] == 0
    assert payload['failed_count'] == 1
    assert payload['failed_tracks'][0]['reason'] == 'Track not found'


def test_track_with_no_file_path_marked_failed():
    """Track has no file_path → failed reason 'No file path'."""
    detail = _artist_with_track()
    detail['albums'][0]['tracks'][0]['file_path'] = None
    deps = _build_deps(artist_detail=detail)
    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert payload['failed_count'] == 1
    assert payload['failed_tracks'][0]['reason'] == 'No file path'


def test_no_match_anywhere_marked_failed():
    """No source returns a usable match → failed reason lists the
    sources that were searched so user knows what was tried."""
    spotify = _FakeSpotify(track_details=None, search_results=[])
    deps = _build_deps(
        spotify=spotify,
        artist_detail=_artist_with_track(),
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert payload['failed_count'] == 1
    reason = payload['failed_tracks'][0]['reason']
    assert 'No usable match across' in reason
    assert 'spotify' in reason
    assert 'try connecting an additional metadata source' in reason


def test_no_match_without_spotify_lists_searched_sources():
    """When Spotify isn't connected and the configured sources find
    nothing, the failure reason lists every searched source. Discord
    case: user with no Spotify / Deezer saw enhance silently produce
    'unknown artist - unknown album - unknown track' wishlist entries
    instead of a clear failure."""
    fallback = type('FB', (), {
        'search_tracks': lambda self, q, limit=5: [],
    })()
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(),
        fallback_client=fallback,
        fallback_source='itunes',
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)
    assert payload['failed_count'] == 1
    reason = payload['failed_tracks'][0]['reason']
    assert 'No usable match across' in reason
    assert 'itunes' in reason


def test_no_match_with_no_sources_configured_prompts_setup():
    """User with literally zero metadata sources configured gets a
    clear prompt to connect one — instead of a generic 'no match'."""
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(),
        search_sources=[],
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)
    assert payload['failed_count'] == 1
    reason = payload['failed_tracks'][0]['reason']
    assert 'No metadata source configured' in reason


def test_fallback_match_with_empty_artist_rejected():
    """Per the user-reported bug: an iTunes match that clears the 0.7
    confidence threshold but has empty/missing artists is rejected
    instead of producing a wishlist entry with empty artist field
    (which the wishlist payload normalizer happily accepts and the
    UI then displays as 'unknown artist')."""
    fallback_track = _SpotifyTrack(id='it-empty', name='Track One',
                                    artists=[],  # empty artists list
                                    image_url='')
    fallback_track.track_number = 1
    fallback_track.disc_number = 1
    fallback_track.album = 'Album X'
    fallback = type('FB', (), {
        'search_tracks': lambda self, q, limit=5: [fallback_track],
    })()
    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(),
        wishlist=wishlist,
        fallback_client=fallback,
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    # No wishlist entry with empty fields — match was rejected.
    assert payload['enhanced_count'] == 0
    assert payload['failed_count'] == 1
    assert wishlist.added == []


def test_fallback_match_with_empty_album_rejected():
    """Empty album field on iTunes match → reject (was producing
    'unknown album' wishlist entries)."""
    fallback_track = _SpotifyTrack(id='it-no-album', name='Track One',
                                    artists=['Artist Name'])
    fallback_track.track_number = 1
    fallback_track.disc_number = 1
    fallback_track.album = ''  # empty
    fallback = type('FB', (), {
        'search_tracks': lambda self, q, limit=5: [fallback_track],
    })()
    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(),
        wishlist=wishlist,
        fallback_client=fallback,
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)
    assert payload['enhanced_count'] == 0
    assert payload['failed_count'] == 1
    assert wishlist.added == []


def test_fallback_match_with_empty_name_rejected():
    """Empty title on iTunes match → reject."""
    fallback_track = _SpotifyTrack(id='it-no-name', name='',
                                    artists=['Artist Name'])
    fallback_track.track_number = 1
    fallback_track.disc_number = 1
    fallback_track.album = 'Album X'
    fallback = type('FB', (), {
        'search_tracks': lambda self, q, limit=5: [fallback_track],
    })()
    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=None,
        artist_detail=_artist_with_track(),
        wishlist=wishlist,
        fallback_client=fallback,
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)
    assert payload['enhanced_count'] == 0
    assert payload['failed_count'] == 1
    assert wishlist.added == []


# ---------------------------------------------------------------------------
# Wishlist source_context payload
# ---------------------------------------------------------------------------

def test_wishlist_source_context_carries_quality_metadata():
    """source_context includes original_file_path, format tier, bitrate, artist_name."""
    raw = {'id': 'sp-1', 'name': 'Track One', 'artists': [{'name': 'Artist Name'}],
           'album': {'name': 'Album X'}}
    spotify = _FakeSpotify(track_details={'raw_data': raw})
    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=spotify,
        artist_detail=_artist_with_track(spotify_tid='sp-1'),
        wishlist=wishlist,
        quality_tier=('mp3_192', 4),
    )

    aq.enhance_artist_quality('artist-1', ['t1'], deps)

    ctx = wishlist.added[0]['source_context']
    assert ctx['enhance'] is True
    assert ctx['original_file_path'] == '/file.mp3'
    assert ctx['original_format'] == 'mp3_192'
    assert ctx['original_bitrate'] == 320
    assert ctx['original_tier'] == 4
    assert ctx['artist_name'] == 'Artist Name'
    assert wishlist.added[0]['source_type'] == 'enhance'
