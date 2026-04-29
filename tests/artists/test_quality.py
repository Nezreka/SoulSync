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
    profile_id=1,
    quality_tier=('mp3_320', 4),
):
    deps = aq.ArtistQualityDeps(
        spotify_client=spotify,
        matching_engine=matching_engine or _FakeMatchingEngine(),
        get_database=lambda: _FakeDatabase(artist_detail=artist_detail),
        get_wishlist_service=lambda: wishlist or _FakeWishlist(),
        get_current_profile_id=lambda: profile_id,
        get_quality_tier_from_extension=lambda fp: quality_tier,
        get_metadata_fallback_client=lambda: fallback_client,
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
    """No Spotify match AND no fallback match → failed reason 'No Spotify or fallback match'."""
    spotify = _FakeSpotify(track_details=None, search_results=[])
    fallback = type('FB', (), {
        'search_tracks': lambda self, q, limit=5: [],
    })()
    deps = _build_deps(
        spotify=spotify,
        artist_detail=_artist_with_track(),
        fallback_client=fallback,
    )

    payload, _ = aq.enhance_artist_quality('artist-1', ['t1'], deps)

    assert payload['failed_count'] == 1
    assert 'No Spotify or fallback match' in payload['failed_tracks'][0]['reason']


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
