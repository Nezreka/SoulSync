"""Artist image lookup helpers for metadata API."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from core.metadata import registry as metadata_registry
from core.metadata.discography import _extract_lookup_value
from utils.logging_config import get_logger

logger = get_logger("metadata.artist_image")

__all__ = [
    "get_artist_image_url",
    "gather_artist_image_candidates",
    "is_placeholder_artist_image",
]


# Images a provider hands out when it has NO photo. They are valid, downloadable
# URLs, so nothing downstream can tell them from a real portrait — the library
# just showed seven artists the same grey Last.fm star. Matched on the asset id
# so every size/extension variant of the same asset is caught.
_PLACEHOLDER_IMAGE_MARKERS = (
    # Last.fm's generic "no artist image" star — the one actually observed in
    # the wild. Add a marker here only for an asset id confirmed to be a
    # provider's stand-in, never a guessed one.
    "2a96cbd8b46e442fc41c2b86b821562f",
)

# Deezer does not use a magic asset id: when an artist has no photo it returns
# the normal CDN URL with the asset hash simply MISSING —
# ``/images/artist//1000x1000-000000-80-0-0.jpg`` — and serves a grey
# silhouette for it. Verified live against api.deezer.com/artist/5541359.
_EMPTY_ASSET_PATH = re.compile(r"/images/[a-z]+//")


def is_placeholder_artist_image(url: Any) -> bool:
    """True when ``url`` is a provider's stand-in for "no photo at all"."""
    text = str(url or "").strip().lower()
    if not text:
        return False
    if any(marker in text for marker in _PLACEHOLDER_IMAGE_MARKERS):
        return True
    return _EMPTY_ASSET_PATH.search(text) is not None


def _real_artist_image(url: Optional[str]) -> Optional[str]:
    """``url`` unless it is a provider placeholder — then nothing at all.

    "No photo" has to travel as no photo: storing the placeholder makes the
    artist look enriched, stops every later lookup from trying again, and puts
    the identical grey star on every artist the provider could not picture.
    """
    return None if is_placeholder_artist_image(url) else url


def _extract_artist_image_url(artist_data: Any) -> Optional[str]:
    if not artist_data:
        return None

    images = _extract_lookup_value(artist_data, 'images', default=[]) or []
    if not isinstance(images, list):
        try:
            images = list(images)
        except TypeError:
            images = []

    if images:
        first_image = images[0]
        image_url = _real_artist_image(_extract_lookup_value(first_image, 'url'))
        if image_url:
            return image_url

    return _real_artist_image(_extract_lookup_value(
        artist_data,
        'image_url',
        'thumb_url',
        'cover_image',
        'picture_xl',
        'picture_big',
        'picture_medium',
    ))


def _get_artist_image_from_source(source: str, artist_id: str) -> Optional[str]:
    client = metadata_registry.get_client_for_source(source)
    if not client:
        return None

    try:
        if source == 'spotify':
            artist_data = client.get_artist(artist_id, allow_fallback=False)
        else:
            artist_data = client.get_artist(artist_id)
    except Exception as exc:
        logger.debug("Could not fetch artist image for %s on %s: %s", artist_id, source, exc)
        artist_data = None

    image_url = _extract_artist_image_url(artist_data)
    if image_url:
        return image_url

    if hasattr(client, '_get_artist_image_from_albums'):
        try:
            return _real_artist_image(client._get_artist_image_from_albums(artist_id))
        except Exception as exc:
            logger.debug("Could not fetch artist album art for %s on %s: %s", artist_id, source, exc)

    return None


def _lookup_artist_image_by_name(name: str) -> Optional[str]:
    """Look up an artist image by name across fallback sources."""
    name = (name or '').strip()
    if not name:
        return None

    skip_sources = {'musicbrainz', 'soulseek', 'youtube_videos', 'hydrabase'}
    for source in metadata_registry.get_source_priority(metadata_registry.get_primary_source()):
        if source in skip_sources:
            continue
        client = metadata_registry.get_client_for_source(source)
        if not client or not hasattr(client, 'search_artists'):
            continue
        try:
            results = client.search_artists(name, limit=1) or []
            if results:
                top = results[0]
                image_url = getattr(top, 'image_url', None) or (
                    top.get('image_url') if isinstance(top, dict) else None
                )
                if _real_artist_image(image_url):
                    return image_url
        except Exception as exc:
            logger.debug("Artist image lookup by name failed on %s for %r: %s", source, name, exc)
            continue

    return None


# mbid -> (fetched_at, url|None). The MB lookup is a real API call behind a
# 1 rps limiter; the search page lazy-loads several cards at once and re-runs
# on every search, so repeats must be free. None results cache too (an artist
# with no relations shouldn't be re-asked every render).
_MB_RELATION_IMAGE_CACHE: dict = {}
_MB_RELATION_IMAGE_TTL_S = 6 * 3600

_MB_URL_REL_PATTERNS = (
    ('deezer', re.compile(r'deezer\.com/(?:[a-z]{2}/)?artist/(\d+)', re.I)),
    ('spotify', re.compile(r'open\.spotify\.com/artist/([A-Za-z0-9]+)', re.I)),
    ('itunes', re.compile(r'music\.apple\.com/.+?/(?:artist/)?(?:[^/]*/)?(\d+)', re.I)),
)


def _image_from_musicbrainz_relations(mbid: str) -> Optional[str]:
    """Resolve an MB artist's image via its url relations (exact per-source
    artist ids), never by name. Cached; returns None when MB has no usable
    streaming relation or the lookup fails."""
    import time as _time
    now = _time.time()
    hit = _MB_RELATION_IMAGE_CACHE.get(mbid)
    if hit and now - hit[0] < _MB_RELATION_IMAGE_TTL_S:
        return hit[1]

    url = None
    try:
        from core.musicbrainz_client import MusicBrainzClient
        artist = MusicBrainzClient("SoulSync", "2").get_artist(mbid, includes=['url-rels'])
        for rel in ((artist or {}).get('relations') or []):
            resource = str(((rel or {}).get('url') or {}).get('resource') or '')
            if not resource:
                continue
            for source, pattern in _MB_URL_REL_PATTERNS:
                m = pattern.search(resource)
                if m:
                    url = _get_artist_image_from_source(source, m.group(1))
                    if url:
                        break
            if url:
                break
    except Exception as exc:
        logger.debug("MB url-relation image lookup failed for %s: %s", mbid, exc)

    _MB_RELATION_IMAGE_CACHE[mbid] = (now, url)
    return url


def get_artist_image_url(
    artist_id: str,
    source_override: Optional[str] = None,
    plugin: Optional[str] = None,
    artist_name: Optional[str] = None,
) -> Optional[str]:
    """Resolve an artist image URL using the configured source priority."""
    if not artist_id:
        return None

    if artist_id.startswith('soul_'):
        return None

    source_override = (source_override or '').strip().lower()
    plugin = (plugin or '').strip().lower()

    if source_override == 'hydrabase':
        if plugin in ('deezer', 'itunes'):
            return _get_artist_image_from_source(plugin, artist_id)
        if artist_id.isdigit():
            return _get_artist_image_from_source('itunes', artist_id)
        return None

    if source_override == 'musicbrainz':
        # MB stores no artist images, but it DOES store url relations to the
        # artist's exact Deezer/Spotify/Apple pages. Resolve through those
        # FIRST: the name fallback takes the first source's top hit for the
        # name, and a same-named artist can hijack the photo (#1036 — the MB
        # "Korn" card wore a Thai pop duo's art while opening the metal
        # band's discography). Only when MB has no usable relation does the
        # name lookup run.
        image_url = _image_from_musicbrainz_relations(artist_id)
        if image_url:
            return image_url
        if not artist_name:
            return None
        return _lookup_artist_image_by_name(artist_name)

    if source_override:
        return _get_artist_image_from_source(source_override, artist_id)

    for source in metadata_registry.get_source_priority(metadata_registry.get_primary_source()):
        image_url = _get_artist_image_from_source(source, artist_id)
        if image_url:
            return image_url

    return None


# Which artists-table column holds each source's artist id (for direct, exact
# lookups in the candidate gather — beats a name search when we have it).
_SOURCE_ID_COLUMNS = {
    'spotify': 'spotify_artist_id',
    'deezer': 'deezer_id',
    'itunes': 'itunes_artist_id',
    'audiodb': 'audiodb_id',
    'discogs': 'discogs_id',
}

# Sources that can't produce an artist photo (or aren't image services at all).
_CANDIDATE_SKIP_SOURCES = {'musicbrainz', 'soulseek', 'youtube_videos', 'hydrabase'}

# TheAudioDB isn't in the metadata priority chain (it's an enrichment worker,
# not a browse source) but it has excellent keyless artist photos — the picker
# queries it explicitly. Lazy singleton: one requests session, reused.
_AUDIODB_CLIENT = None


def _audiodb():
    global _AUDIODB_CLIENT
    if _AUDIODB_CLIENT is None:
        try:
            from core.audiodb_client import AudioDBClient
            _AUDIODB_CLIENT = AudioDBClient()
        except Exception:
            return None
    return _AUDIODB_CLIENT


def _spotify_artist_image(client, artist_id: str):
    """Artist image via the Spotify WRAPPER, with a free-metadata fall-through:
    Spotify 403s dev apps whose owner lacks an active Premium subscription —
    auth still LOOKS healthy (token refresh succeeds), so the wrapper's own
    free routing never engages. The no-creds backend answers regardless."""
    try:
        url = _extract_artist_image_url(client.get_artist(artist_id))
        if url:
            return url
    except Exception as exc:
        logger.debug("spotify official get_artist(%s) failed, trying free: %s", artist_id, exc)
    try:
        return _extract_artist_image_url(client._free_meta.get_artist(artist_id))
    except Exception:
        return None


def _audiodb_candidate(name: str, sid: str):
    client = _audiodb()
    if not client:
        return None
    data = None
    if sid:
        data = client.lookup_artist_by_id(sid)
    if not data and name:
        data = client.search_artist(name)
    url = (data or {}).get('strArtistThumb') or (data or {}).get('strArtistFanart')
    return ('audiodb', url) if url else None


# iss27-03: a per-source fetch is isolated by try/except already, but had no
# time budget — a slow/rate-limited provider (iTunes' interval throttle,
# Discogs' backoff sleep, both of which sleep for tens of seconds INSIDE the
# worker thread) blocked the whole request past the frontend's client
# timeout, silently discarding every source's result including ones that had
# already succeeded. Bounding the wait means one slow source just misses this
# round instead of taking every other source down with it.
_CANDIDATE_GATHER_TIMEOUT_S = 10

# The fan-out runs on ONE process-wide pool, not a fresh executor per call.
# Per call, `max_workers=len(sources)` looks bounded — the priority chain is
# single digits — but nothing capped threads ACROSS calls: the gather returns
# after its wall-clock budget while the stragglers keep sleeping inside their
# providers' rate-limit backoff (tens of seconds), so every picker open added
# a full set of live threads on top of the last one's. A shared pool trades
# that for a real ceiling; sized well above one chain so an ordinary picker
# open still starts every source at once, and a source that does queue behind
# a busy moment loses only this round, exactly like one that times out.
_CANDIDATE_POOL_MAX_WORKERS = 16
_candidate_pool_lock = threading.Lock()
_candidate_pool_instance = None


def _candidate_pool():
    """The shared, lazily-built executor for candidate fan-out."""
    global _candidate_pool_instance
    if _candidate_pool_instance is None:
        with _candidate_pool_lock:
            if _candidate_pool_instance is None:
                _candidate_pool_instance = ThreadPoolExecutor(
                    max_workers=_CANDIDATE_POOL_MAX_WORKERS,
                    thread_name_prefix="soulsync-art-candidate",
                )
    return _candidate_pool_instance


def gather_artist_image_candidates(artist_name: str, source_ids: Optional[dict] = None) -> list:
    """One candidate photo per CONNECTED metadata source, for the artist
    image picker (mirrors ``gather_album_art_candidates``).

    For each source in the configured priority chain: use the artist's stored
    per-source id when the library row has one (exact), otherwise search the
    source by name and take its top hit's image. Sources fan out concurrently
    under a shared time budget (``_CANDIDATE_GATHER_TIMEOUT_S``) — a failing
    OR merely slow source contributes nothing rather than blocking the rest.
    Returns ``[{source, url}, ...]`` deduped by URL, in chain order.

    MusicBrainz is excluded from the generic by-name search (unreliable —
    see ``_CANDIDATE_SKIP_SOURCES``), but its EXACT url-relations lookup
    (``_image_from_musicbrainz_relations``) is added as its own candidate
    whenever the artist's MBID is known, so a provider the picker already
    presents as "connected" actually gets asked.
    """
    name = (artist_name or '').strip()
    ids = source_ids or {}
    sources = [s for s in metadata_registry.get_source_priority(metadata_registry.get_primary_source())
               if s not in _CANDIDATE_SKIP_SOURCES]
    if 'audiodb' not in sources:
        sources.append('audiodb')       # the docstring always promised it
    mbid = str(ids.get('musicbrainz_artist_id') or '').strip()
    if mbid:
        sources.append('musicbrainz')   # exact-id lookup, not the excluded by-name path

    def _one(source: str):
        try:
            if source == 'musicbrainz':
                url = _image_from_musicbrainz_relations(mbid)
                return ('musicbrainz', url) if url else None
            sid = str(ids.get(_SOURCE_ID_COLUMNS.get(source, '')) or '').strip()
            if source == 'audiodb':
                return _audiodb_candidate(name, sid)
            if source == 'spotify':
                # the registry gate requires FULL Spotify auth, but the wrapper
                # serves artist metadata in Free mode via its fallback routing —
                # the picker only needs an image, so ask the wrapper directly
                client = metadata_registry.get_spotify_client()
            else:
                client = metadata_registry.get_client_for_source(source)
            if not client:
                return None
            url = None
            if sid:
                if source == 'spotify':
                    url = _spotify_artist_image(client, sid)
                else:
                    url = _get_artist_image_from_source(source, sid)
            if not url and name and hasattr(client, 'search_artists'):
                results = client.search_artists(name, limit=1) or []
                if results:
                    top = results[0]
                    url = getattr(top, 'image_url', None) or (
                        top.get('image_url') if isinstance(top, dict) else None)
                    if not url:
                        # some sources (iTunes by design) return imageless
                        # search hits — a second exact fetch by the hit's id
                        # gets the artwork the search withheld
                        top_id = str(getattr(top, 'id', '') or (
                            top.get('id') if isinstance(top, dict) else '') or '').strip()
                        if top_id:
                            if source == 'spotify':
                                url = _spotify_artist_image(client, top_id)
                            else:
                                url = _get_artist_image_from_source(source, top_id)
            return (source, url) if url else None
        except Exception as exc:
            logger.debug("artist image candidate failed for %s: %s", source, exc)
            return None

    from concurrent.futures import wait as _wait_futures

    pool = _candidate_pool()
    future_by_source = {
        source: pool.submit(_one, source)
        for source in sources
    }
    done, not_done = _wait_futures(
        future_by_source.values(),
        timeout=_CANDIDATE_GATHER_TIMEOUT_S,
    )
    results = []
    # Futures complete in timing order, but duplicate URLs must be resolved in
    # configured provider order. Iterating ``done`` (a set) made a faster
    # fallback source nondeterministically steal the preferred source's card.
    for source, future in future_by_source.items():
        if future not in done:
            continue
        try:
            results.append(future.result())
        except Exception as exc:
            logger.debug("artist image candidate failed for %s: %s", source, exc)
    for source, future in future_by_source.items():
        if future not in not_done:
            continue
        # Still running past the budget — leave it be (threads can't be
        # killed) rather than block the response on it; it just misses
        # this round's candidate list. The pool is shared and long-lived, so
        # the straggler releases its worker when its own HTTP call returns
        # and nothing is shut down under it.
        logger.debug("artist image candidate timed out for %s", source)

    candidates, seen = [], set()
    for entry in results:
        if not entry:
            continue
        source, url = entry
        if url in seen:
            continue
        seen.add(url)
        candidates.append({'source': source, 'url': url})
    return candidates
