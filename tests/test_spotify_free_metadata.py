"""Tests for core/spotify_free_metadata.py — the no-creds Spotify fallback.

Pure-unit only: the live SpotipyFree calls hit the network and can't run in CI,
but the two things that actually need pinning are pure — the activation gate and
the artist normalizer (the one shape SpotipyFree returns raw). Fixtures below
are trimmed from real captured responses (2026-06).
"""

from __future__ import annotations

from core.spotify_free_metadata import (
    normalize_artist,
    should_offer_spotify_metadata,
    should_use_free_fallback,
    spotify_free_installed,
)


# ---------------------------------------------------------------------------
# should_use_free_fallback — the activation gate
# ---------------------------------------------------------------------------

def test_gate_open_when_no_auth():
    assert should_use_free_fallback(authenticated=False, rate_limited=False) is True


def test_gate_open_when_rate_limited_even_if_authed():
    assert should_use_free_fallback(authenticated=True, rate_limited=True) is True


def test_gate_closed_when_authed_and_healthy():
    # The critical guarantee: with working Spotify auth and no rate limit, the
    # free source must NEVER activate.
    assert should_use_free_fallback(authenticated=True, rate_limited=False) is False


def test_gate_open_when_no_auth_and_rate_limited():
    assert should_use_free_fallback(authenticated=False, rate_limited=True) is True


# ---------------------------------------------------------------------------
# should_offer_spotify_metadata — the availability gate the callers use
# ---------------------------------------------------------------------------

def test_offer_when_authed_even_if_no_free():
    assert should_offer_spotify_metadata(authenticated=True, free_available=False) is True


def test_offer_when_free_available_even_if_no_auth():
    # The whole point: no auth but free available → Spotify source stays usable.
    assert should_offer_spotify_metadata(authenticated=False, free_available=True) is True


def test_not_offered_when_neither():
    assert should_offer_spotify_metadata(authenticated=False, free_available=False) is False


def test_full_composition_authed_healthy_never_uses_free():
    # The critical no-regression guarantee end to end: authed + healthy →
    # offered (official), and the per-request gate stays CLOSED.
    assert should_offer_spotify_metadata(authenticated=True, free_available=True) is True
    assert should_use_free_fallback(authenticated=True, rate_limited=False) is False


def test_spotify_free_installed_returns_bool():
    # Whatever the env, it's a cached bool and doesn't raise / hit network.
    assert isinstance(spotify_free_installed(), bool)


# ---------------------------------------------------------------------------
# normalize_artist — raw web-player GraphQL → Spotify-compatible artist dict
# ---------------------------------------------------------------------------

# Trimmed from a real `SpotipyFree.artist()` response.
_RAW_ARTIST = {
    '__typename': 'Artist',
    'id': '4Z8W4fKeB5YxbusRsdQVPb',
    'uri': 'spotify:artist:4Z8W4fKeB5YxbusRsdQVPb',
    'profile': {'name': 'Radiohead', 'biography': {'text': '...'}},
    'stats': {'followers': 16239336, 'monthlyListeners': 45139421},
    'visuals': {
        'avatarImage': {
            'sources': [
                {'height': 640, 'width': 640, 'url': 'https://i.scdn.co/image/big'},
                {'height': 160, 'width': 160, 'url': 'https://i.scdn.co/image/small'},
            ]
        }
    },
}

# Trimmed from a real `artist_search` item's `data` (no usable image — only
# color swatches under visualIdentity; SoulSync lazy-loads art separately).
_RAW_SEARCH_ARTIST = {
    '__typename': 'Artist',
    'uri': 'spotify:artist:1dfeR4HaWDbWqFHLkxsg1d',
    'profile': {'name': 'Queen'},
    'visualIdentity': {'squareCoverImage': {'extractedColorSet': {}}},
}


def test_normalize_artist_full_shape():
    out = normalize_artist(_RAW_ARTIST)
    assert out['id'] == '4Z8W4fKeB5YxbusRsdQVPb'
    assert out['name'] == 'Radiohead'
    assert out['followers'] == {'total': 16239336}
    assert out['images'][0]['url'] == 'https://i.scdn.co/image/big'
    assert len(out['images']) == 2
    assert out['external_urls'] == {
        'spotify': 'https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb'
    }
    assert out['genres'] == []  # web player doesn't provide genres


def test_normalize_artist_id_from_uri_when_no_id_field():
    out = normalize_artist(_RAW_SEARCH_ARTIST)
    assert out['id'] == '1dfeR4HaWDbWqFHLkxsg1d'
    assert out['name'] == 'Queen'
    assert out['images'] == []  # search items carry no usable image
    assert out['external_urls']['spotify'].endswith('1dfeR4HaWDbWqFHLkxsg1d')


def test_normalize_artist_handles_empty_and_none():
    for bad in (None, {}, {'profile': None, 'stats': None, 'visuals': None}):
        out = normalize_artist(bad)
        assert out['name'] == ''
        assert out['images'] == []
        assert out['followers'] == {'total': 0}
        assert out['external_urls'] == {}


def test_normalize_artist_skips_imageless_sources():
    raw = {'uri': 'spotify:artist:x', 'profile': {'name': 'A'},
           'visuals': {'avatarImage': {'sources': [{'height': 1}, {'url': 'u'}]}}}
    out = normalize_artist(raw)
    assert out['images'] == [{'url': 'u', 'height': None, 'width': None}]


# ---------------------------------------------------------------------------
# SpotifyClient gate model — auto-bridge vs explicit opt-in vs no-surprise
# (constructs the client via __new__ so no network/config init runs)
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402
from core.spotify_client import SpotifyClient  # noqa: E402
import core.spotify_free_metadata as _sfm  # noqa: E402


def _gate(authed, has_creds, selected, installed, rate_limited):
    c = SpotifyClient.__new__(SpotifyClient)
    with patch.object(SpotifyClient, 'is_spotify_authenticated', return_value=authed), \
         patch('core.spotify_client.config_manager') as cm, \
         patch('core.spotify_client._is_globally_rate_limited', return_value=rate_limited), \
         patch.object(_sfm, 'spotify_free_installed', return_value=installed):
        cm.get_spotify_config.return_value = (
            {'client_id': 'x', 'client_secret': 'y'} if has_creds else {})
        cm.get.side_effect = lambda k, d=None: selected if k == 'metadata.spotify_free' else d
        return c.is_spotify_metadata_available(), c._free_active()


def test_connected_healthy_uses_official():
    avail, free = _gate(authed=True, has_creds=True, selected=False, installed=True, rate_limited=False)
    assert avail is True and free is False  # official; free never opens


def test_plain_spotify_ratelimited_does_NOT_bridge():
    # OPT-IN: a user on plain 'Spotify' (didn't pick Spotify Free) waits out a
    # rate-limit ban — free does NOT auto-bridge for them. No surprise scraping.
    avail, free = _gate(authed=False, has_creds=True, selected=False, installed=True, rate_limited=True)
    assert avail is False and free is False


def test_spotify_free_user_ratelimited_bridges():
    # A user who DID pick Spotify Free and also connected an account: official
    # when healthy, free bridges during a ban.
    avail, free = _gate(authed=False, has_creds=True, selected=True, installed=True, rate_limited=True)
    assert avail is True and free is True


def test_spotify_free_user_healthy_uses_official():
    avail, free = _gate(authed=True, has_creds=True, selected=True, installed=True, rate_limited=False)
    assert avail is True and free is False   # picked Spotify Free but authed -> official


def test_no_auth_picked_spotify_free_serves():
    avail, free = _gate(authed=False, has_creds=False, selected=True, installed=True, rate_limited=False)
    assert avail is True and free is True


def test_no_auth_not_opted_in_does_nothing():
    # The key no-surprise guarantee: a user who never chose Spotify gets no free.
    avail, free = _gate(authed=False, has_creds=False, selected=False, installed=True, rate_limited=False)
    assert avail is False and free is False


def test_selected_but_package_missing_is_graceful():
    avail, free = _gate(authed=False, has_creds=False, selected=True, installed=False, rate_limited=False)
    assert avail is False and free is False


def test_connected_ratelimited_but_no_package_no_bridge():
    avail, free = _gate(authed=False, has_creds=True, selected=False, installed=False, rate_limited=True)
    assert avail is False and free is False
