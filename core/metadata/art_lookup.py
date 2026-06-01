"""Per-source album-art lookups + availability for the cover-art picker.

Bridges the pure resolver in ``art_sources.py`` to the real metadata clients.
Each supported source contributes two things:

- **availability** — is this source usable for the current user right now?
  Free sources (CAA, Deezer, iTunes, AudioDB) are always available; account
  sources (Spotify) only when connected. This is what powers "not everybody
  has access to every source": the UI offers only available sources and the
  resolver skips the rest.
- **lookup** — ``(artist, album, metadata) -> cover_url | None``, calling an
  EXISTING client method. Every lookup is individually guarded so any error or
  miss degrades to ``None`` (the resolver then falls through to the next
  source, finally to the download's own art) — a flaky source can never raise
  into, or break, a download.

Lookups are cached per album via ``build_art_lookup`` so resolving art for a
16-track album hits each source at most once.

Client accessors are imported lazily inside each function to keep this module
import-light (the pure resolver + its tests never pull a network client).
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional

from utils.logging_config import get_logger

from core.metadata.art_sources import ART_CAPABLE_SOURCES

logger = get_logger("metadata.art_lookup")

# Sources that need no account/config — always offered.
_FREE_SOURCES = ("caa", "deezer", "itunes", "audiodb")


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def _spotify_available() -> bool:
    """Spotify art is only usable when the user is connected. Reuse the
    canonical accessor, which returns a client only when authenticated."""
    try:
        from core.metadata.registry import get_client_for_source
        return get_client_for_source("spotify") is not None
    except Exception:
        return False


_AVAILABILITY: Dict[str, Callable[[], bool]] = {
    "spotify": _spotify_available,
}


def is_art_source_available(source: str) -> bool:
    """Is ``source`` usable right now? Free sources are always available;
    account sources defer to their connection check. Unknown/unsupported
    sources are never available."""
    name = (source or "").strip().lower()
    if name not in ART_CAPABLE_SOURCES:
        return False
    if name in _FREE_SOURCES:
        return True
    check = _AVAILABILITY.get(name)
    return bool(check()) if check else False


def available_art_sources() -> List[str]:
    """The supported art sources currently usable for this user, in the
    default priority order — for populating the settings UI."""
    return [s for s in ART_CAPABLE_SOURCES if is_art_source_available(s)]


# ---------------------------------------------------------------------------
# Album-match validation. Every client's search returns its top hit
# unvalidated (results[0]), so a source that lacks the album could hand back a
# DIFFERENT one — embedding wrong-album art, which is worse than the download's
# own cover. We therefore confirm the returned album matches the request before
# trusting its art; a mismatch returns None so the resolver falls through,
# preserving the "worst case = the cover you'd get today" guarantee.
# ---------------------------------------------------------------------------

_STOPWORDS = {"the", "a", "an"}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _significant_tokens(s) -> set:
    return {t for t in _norm(s).split() if t not in _STOPWORDS}


def _result_album_artist(obj):
    """Best-effort (album_name, artist_name) from a search result — handles
    both raw dicts (Deezer/AudioDB) and dataclasses (iTunes/Spotify)."""
    if isinstance(obj, dict):
        name = obj.get("title") or obj.get("name") or obj.get("strAlbum") or ""
        artist = obj.get("strArtist") or ""
        a = obj.get("artist")
        if not artist and isinstance(a, dict):
            artist = a.get("name", "")
        elif not artist and isinstance(a, str):
            artist = a
        return name, artist
    name = getattr(obj, "name", None) or getattr(obj, "title", None) or ""
    arts = getattr(obj, "artists", None)
    if arts is None:
        arts = getattr(obj, "artist", None)
    if isinstance(arts, (list, tuple)):
        artist = ", ".join(str(x) for x in arts if x)
    else:
        artist = str(arts) if arts else ""
    return name, artist


def _album_matches(req_artist, req_album, got_artist, got_album) -> bool:
    """True when the returned album plausibly IS the requested one.

    Both album and artist are compared by significant-token subset (stopwords
    dropped), which tolerates leading articles, word order, "(Deluxe)"/
    "- Remastered" suffixes, punctuation, and "feat."/"&"/multi-artist ordering.
    Lenient enough to never reject a genuine hit — a false reject just falls
    back to today's art — yet strict enough to drop a different album (the
    generic-title case, e.g. two "Greatest Hits", is caught by the artist gate)."""
    ra, ga = _significant_tokens(req_album), _significant_tokens(got_album)
    if not ra or not ga:
        return False
    if not (ra <= ga or ga <= ra):
        return False
    ta, tg = _significant_tokens(req_artist), _significant_tokens(got_artist)
    if not ta:
        return True                      # requested artist unknown -> album match suffices
    if not tg:
        return False                     # asked for an artist, none returned -> can't confirm
    return ta <= tg or tg <= ta or bool(ta & tg)


# ---------------------------------------------------------------------------
# Per-source lookups — each returns a cover URL or None, never raises.
# Non-CAA sources validate the returned album before trusting its art.
# ---------------------------------------------------------------------------


def _caa_art(artist: str, album: str, metadata: dict) -> Optional[str]:
    # Cover Art Archive is keyed by the MusicBrainz release id, so it's THE
    # release's art by definition — no fuzzy match to validate. Resolves only
    # once MusicBrainz enrichment has found the release.
    mbid = (metadata or {}).get("musicbrainz_release_id")
    if not mbid:
        return None
    return f"https://coverartarchive.org/release/{mbid}/front-1200"


def _deezer_art(artist: str, album: str, metadata: dict) -> Optional[str]:
    from core.metadata.registry import get_deezer_client
    client = get_deezer_client()
    if not client:
        return None
    data = client.search_album(artist, album)
    if not data:
        return None
    got_album, got_artist = _result_album_artist(data)
    if not _album_matches(artist, album, got_artist, got_album):
        return None
    url = data.get("cover_xl") or data.get("cover_big") or data.get("cover_medium")
    if not url:
        return None
    try:
        from core.deezer_client import _upgrade_deezer_cover_url
        return _upgrade_deezer_cover_url(url)
    except Exception:
        return url


def _itunes_art(artist: str, album: str, metadata: dict) -> Optional[str]:
    from core.metadata.registry import get_itunes_client
    client = get_itunes_client()
    if not client:
        return None
    for alb in (client.search_albums(f"{artist} {album}") or []):
        url = getattr(alb, "image_url", None)
        if not url:
            continue
        got_album, got_artist = _result_album_artist(alb)
        if _album_matches(artist, album, got_artist, got_album):
            return url
    return None


def _audiodb_art(artist: str, album: str, metadata: dict) -> Optional[str]:
    from core.audiodb_client import AudioDBClient
    data = AudioDBClient().search_album(artist, album)
    if not data:
        return None
    got_album, got_artist = _result_album_artist(data)
    if not _album_matches(artist, album, got_artist, got_album):
        return None
    return data.get("strAlbumThumb") or None


def _spotify_art(artist: str, album: str, metadata: dict) -> Optional[str]:
    from core.metadata.registry import get_client_for_source
    client = get_client_for_source("spotify")
    if not client:
        return None
    for alb in (client.search_albums(f"{artist} {album}") or []):
        url = getattr(alb, "image_url", None)
        if not url:
            continue
        got_album, got_artist = _result_album_artist(alb)
        if _album_matches(artist, album, got_artist, got_album):
            return url
    return None


_LOOKUPS: Dict[str, Callable[[str, str, dict], Optional[str]]] = {
    "caa": _caa_art,
    "deezer": _deezer_art,
    "itunes": _itunes_art,
    "audiodb": _audiodb_art,
    "spotify": _spotify_art,
}


def select_preferred_art_url(
    artist: Optional[str],
    album: Optional[str],
    metadata: Optional[dict],
    configured_order,
) -> Optional[str]:
    """Pick a cover-art URL from the user's configured source order, or None.

    ``None`` means "feature off, or nothing in the list resolved" — the caller
    then keeps its existing art (today's behavior), so the worst case is simply
    the cover you'd get today. This is the single entry point the art pipeline
    calls; it's a no-op (returns ``None`` immediately) unless ``album_art_order``
    is an explicit non-empty list, which keeps every existing install untouched.
    """
    if not isinstance(configured_order, (list, tuple)) or not configured_order:
        return None
    from core.metadata.art_sources import effective_art_order, resolve_cover_art
    order = [s for s in effective_art_order(configured_order) if is_art_source_available(s)]
    if not order:
        return None
    lookup = build_art_lookup(artist or "", album or "", metadata or {})
    url, _src = resolve_cover_art(order, lookup)
    return url


def build_art_lookup(
    artist: str,
    album: str,
    metadata: Optional[dict] = None,
) -> Callable[[str], Optional[str]]:
    """Return a ``source_name -> cover_url | None`` callable for one album,
    suitable to pass straight to ``art_sources.resolve_cover_art``. Results are
    cached per source so re-resolving across an album's tracks costs at most one
    lookup per source, and every lookup is guarded (errors → None)."""
    meta = metadata or {}
    cache: Dict[str, Optional[str]] = {}

    def lookup(source: str) -> Optional[str]:
        name = (source or "").strip().lower()
        if name in cache:
            return cache[name]
        fn = _LOOKUPS.get(name)
        url: Optional[str] = None
        if fn is not None:
            try:
                url = fn(artist, album, meta)
            except Exception as exc:
                logger.debug("[art] %s lookup failed: %s", name, exc)
                url = None
        cache[name] = url
        return url

    return lookup
