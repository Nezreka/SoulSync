"""PROTOTYPE — no-credentials Spotify metadata via SpotipyFree / spotapi.

Goal: stand in as a READ-ONLY Spotify metadata source when the user has no
Spotify auth (or is rate-limited), mapping SpotipyFree's outputs onto the same
Spotify-compatible shapes the rest of SoulSync already consumes
(see core/spotify_client.py + core/search/sources.py).

Unofficial / web-player scraping — best-effort, fragile, and NOT a substitute
for the user-account features (those need a real login).

Capabilities (verified live, 2026-06):
  search_tracks          ✅ SpotipyFree.search (already official-shaped)
  search_artists         ✅ spotapi.Public().artist_search (normalized here)
  search_albums          ❌ no album-name search exists upstream → returns []
  get_album              ✅ SpotipyFree.album (already official-shaped)
  get_artist_albums_list ✅ SpotipyFree.artist_albums (already official-shaped)
  get_track_details      ✅ SpotipyFree.track (already official-shaped)
  get_artist             ✅ SpotipyFree.artist (RAW GraphQL → normalized here)
  get_artist_top_tracks  ❌ unavailable
  audio_features         ❌ unavailable (Spotify deprecated it anyway)

This module is import-safe even when SpotipyFree isn't installed — it
soft-imports inside the client factory.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_installed_cache: Optional[bool] = None


def spotify_free_installed() -> bool:
    """Cheap, cached check: is the optional SpotipyFree package importable?

    Used by the availability gates — never constructs a client or hits the
    network. Absence just means we degrade to the iTunes/Deezer fallback.
    """
    global _installed_cache
    if _installed_cache is None:
        _installed_cache = importlib.util.find_spec('SpotipyFree') is not None
    return _installed_cache


def should_use_free_fallback(authenticated: bool, rate_limited: bool) -> bool:
    """The per-request gate: the no-creds SpotipyFree source may serve a request
    ONLY when official Spotify can't — i.e. the user has no Spotify auth, or
    we're currently rate-limited. When authed AND healthy the official path
    returns before any fallback, so this never opens.
    """
    return (not authenticated) or rate_limited


def should_offer_spotify_metadata(authenticated: bool, free_available: bool) -> bool:
    """The availability gate: SoulSync can serve *some* Spotify metadata when
    either real auth is present, or the no-creds fallback is available. The
    upstream gates (search resolve, enrichment worker, watchlist) use this so
    the fallback is actually reachable — without changing what
    ``is_spotify_authenticated()`` means anywhere.
    """
    return authenticated or free_available


def should_block_rate_limited_resume(rate_limited: bool, metadata_available: bool) -> bool:
    """Whether to refuse resuming the Spotify enrichment worker.

    The worker's own loop bridges to the no-creds free source during a ban
    (its rate-limit guard checks ``is_spotify_metadata_available()``). The
    resume button must mirror that: block ONLY when rate-limited AND nothing
    can serve (plain auth, no free) — otherwise resuming just sleeps. When the
    free fallback is available, ``metadata_available`` is True during a ban
    (``is_spotify_authenticated()`` returns False while banned), so resume is
    allowed and the worker bridges via free.
    """
    return rate_limited and not metadata_available


# --------------------------------------------------------------------------
# Normalizers (pure — unit-testable against captured fixtures)
# --------------------------------------------------------------------------

def normalize_artist(raw: dict) -> dict:
    """Map a raw SpotipyFree/spotapi artist object (from ``artist()`` or an
    ``artist_search`` item's ``data``) onto the Spotify-compatible artist dict
    SoulSync expects: ``{id, name, images, genres, followers, external_urls}``.

    Artist-search items carry no usable image (only color swatches), so
    ``images`` may be empty there — SoulSync lazy-loads artist art separately.
    Genres aren't provided by the web player at all.
    """
    raw = raw or {}
    profile = raw.get('profile') or {}
    uri = raw.get('uri') or ''
    artist_id = raw.get('id') or (uri.split(':')[-1] if uri else '')
    name = profile.get('name') or raw.get('name') or ''

    images = []
    avatar = (raw.get('visuals') or {}).get('avatarImage') or {}
    for src in (avatar.get('sources') or []):
        if src.get('url'):
            images.append({
                'url': src['url'],
                'height': src.get('height'),
                'width': src.get('width'),
            })

    followers = (raw.get('stats') or {}).get('followers')

    return {
        'id': str(artist_id),
        'name': name,
        'images': images,
        'genres': [],  # web player doesn't expose genres
        'followers': {'total': followers or 0},
        'external_urls': (
            {'spotify': f'https://open.spotify.com/artist/{artist_id}'}
            if artist_id else {}
        ),
    }


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class SpotifyFreeMetadataClient:
    """Read-only Spotify metadata via SpotipyFree, normalized to SoulSync's
    Spotify-compatible shapes. Methods mirror the metadata-source interface."""

    def __init__(self):
        self._sf = None       # SpotipyFree.Spotify instance
        self._public = None   # spotapi.Public() for artist_search

    # -- lazy clients (soft import; absence is not fatal) ------------------
    def _sf_client(self):
        if self._sf is None:
            from SpotipyFree import Spotify  # optional, user-installed
            self._sf = Spotify()
        return self._sf

    def _public_client(self):
        if self._public is None:
            import spotapi
            self._public = spotapi.Public()
        return self._public

    def is_available(self) -> bool:
        try:
            self._sf_client()
            return True
        except Exception as e:
            logger.debug(f"SpotipyFree unavailable: {e}")
            return False

    # -- search -----------------------------------------------------------
    def search_tracks(self, query: str, limit: int = 10) -> list[dict]:
        try:
            res = self._sf_client().search(query, limit=limit) or {}
            items = ((res.get('tracks') or {}).get('items')) or []
            return items[:limit]
        except Exception as e:
            logger.debug(f"SpotipyFree search_tracks failed: {e}")
            return []

    def search_artists(self, query: str, limit: int = 10) -> list[dict]:
        try:
            pages = self._public_client().artist_search(query)
            first = next(iter(pages), [])
            out = []
            for item in first[:limit]:
                data = item.get('data') if isinstance(item, dict) else None
                if data:
                    out.append(normalize_artist(data))
            return out
        except Exception as e:
            logger.debug(f"SpotipyFree search_artists failed: {e}")
            return []

    def search_albums(self, query: str, limit: int = 10) -> list[dict]:
        # No album-name search exists in SpotipyFree/spotapi. Albums are only
        # reachable by id or via an artist's discography.
        return []

    # -- entity lookups ---------------------------------------------------
    def get_album(self, album_id: str, include_tracks: bool = True) -> Optional[dict]:
        try:
            return self._sf_client().album(album_id)
        except Exception as e:
            logger.debug(f"SpotipyFree get_album({album_id}) failed: {e}")
            return None

    def get_track_details(self, track_id: str) -> Optional[dict]:
        try:
            return self._sf_client().track(track_id)
        except Exception as e:
            logger.debug(f"SpotipyFree get_track_details({track_id}) failed: {e}")
            return None

    def get_album_tracks(self, album_id: str) -> Optional[dict]:
        try:
            return self._sf_client().album_tracks(album_id)
        except Exception as e:
            logger.debug(f"SpotipyFree get_album_tracks({album_id}) failed: {e}")
            return None

    def get_artist(self, artist_id: str) -> Optional[dict]:
        try:
            raw = self._sf_client().artist(artist_id)
            return normalize_artist(raw) if raw else None
        except Exception as e:
            logger.debug(f"SpotipyFree get_artist({artist_id}) failed: {e}")
            return None

    def get_artist_albums_list(self, artist_id: str, limit: int = 50) -> list[dict]:
        try:
            res = self._sf_client().artist_albums(artist_id) or {}
            return (res.get('items') or [])[:limit]
        except Exception as e:
            logger.debug(f"SpotipyFree get_artist_albums_list({artist_id}) failed: {e}")
            return []
