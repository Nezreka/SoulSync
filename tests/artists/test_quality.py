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
    deps = aq.ArtistQualityDeps(
        spotify_client=spotify,
        matching_engine=matching_engine or _FakeMatchingEngine(),
        get_database=lambda: _FakeDatabase(artist_detail=artist_detail),
        get_wishlist_service=lambda: wishlist or _FakeWishlist(),
        get_current_profile_id=lambda: profile_id,
        get_quality_tier_from_extension=lambda fp: quality_tier,
        get_metadata_search_sources=lambda: list(search_sources),
    )
    return deps


def _artist_with_track(*, track_id='t1', file_path='/file.mp3', spotify_tid=None):
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
    """Track details without raw_data → rebuild payload with album images via get_album."""
    enhanced = {'name': 'Track One', 'artists': ['Artist Name'],
                'album': {'id': 'alb-id', 'name': 'Album X'},
                'duration_ms': 180000, 'track_number': 1, 'disc_number': 1}
    full_album = {'images': [{'url': 'http://art'}]}
    spotify = _FakeSpotify(track_details=enhanced, album=full_album)
    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=spotify,
        artist_detail=_artist_with_track(spotify_tid='sp-stored'),
        wishlist=wishlist,
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert payload['enhanced_count'] == 1
    md = wishlist.added[0]['spotify_track_data']
    assert md['album']['images'] == [{'url': 'http://art'}]


# ---------------------------------------------------------------------------
# Spotify search fallback (priority 2)
# ---------------------------------------------------------------------------

def test_spotify_search_fallback_when_no_stored_id():
    """No spotify_track_id → search via matching_engine, pick best match."""
    track = _SpotifyTrack(name='Track One', artists=['Artist Name'])
    raw = {'id': 'sp-search', 'name': 'Track One', 'artists': [{'name': 'Artist Name'}],
           'album': {'name': 'Album X'}}
    spotify = _FakeSpotify(track_details={'raw_data': raw}, search_results=[track])
    wishlist = _FakeWishlist()
    deps = _build_deps(
        spotify=spotify,
        artist_detail=_artist_with_track(spotify_tid=None),
        wishlist=wishlist,
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert payload['enhanced_count'] == 1
    assert wishlist.added[0]['spotify_track_data'] == raw


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
