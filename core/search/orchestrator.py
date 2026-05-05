"""Enhanced-search orchestration.

Two routes funnel through here:
- `/api/enhanced-search` → `run_enhanced_search`
  - Always returns library DB matches
  - Single-source mode (request body has `source: "spotify"` etc) skips fan-out
  - Default mode resolves a primary source, runs it synchronously, and
    returns the list of alternate sources for the frontend to fetch async
- `/api/enhanced-search/source/<src>` → `stream_source_search` (generator)
  - NDJSON: yields one line per kind (artists / albums / tracks) as each
    finishes, plus a final `{"type":"done"}` line
  - Has its own special-case for `youtube_videos` which uses yt-dlp

The route layer wraps the generator in a Flask `Response(...,
mimetype='application/x-ndjson')`. Everything else is plain Python.

Cross-cutting deps are passed in as a `SearchDeps` dataclass to keep the
function signatures readable. Each field is a live reference (not a
snapshot) so callers see config changes without restart.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

from . import sources

logger = logging.getLogger(__name__)

VALID_SOURCES = (
    'spotify', 'itunes', 'deezer', 'discogs', 'hydrabase', 'musicbrainz',
)

VALID_STREAM_SOURCES = VALID_SOURCES + ('youtube_videos',)


@dataclass
class SearchDeps:
    """Bundle of cross-cutting deps used by the orchestrator.

    All fields are lazily evaluated where possible (live providers, not
    cached values) so settings changes take effect without restart.
    """
    database: Any
    config_manager: Any
    spotify_client: Any
    hydrabase_client: Any
    hydrabase_worker: Any
    soulseek_client: Any
    fix_artist_image_url: Callable[[Optional[str]], Optional[str]]
    is_hydrabase_active: Callable[[], bool]
    get_metadata_fallback_source: Callable[[], str]
    get_metadata_fallback_client: Callable[[], Any]
    get_itunes_client: Callable[[], Any]
    get_deezer_client: Callable[[], Any]
    get_discogs_client: Callable[[Optional[str]], Any]
    run_background_comparison: Callable[..., None]
    run_async: Callable
    dev_mode_enabled_provider: Callable[[], bool]


def resolve_client(source_name: str, deps: SearchDeps) -> tuple[Any, bool]:
    """Return (client, is_available) for an explicit metadata source request."""
    if source_name == 'spotify':
        if deps.spotify_client and deps.spotify_client.is_spotify_authenticated():
            return deps.spotify_client, True
        return None, False
    if source_name == 'itunes':
        return deps.get_itunes_client(), True
    if source_name == 'deezer':
        return deps.get_deezer_client(), True
    if source_name == 'discogs':
        token = deps.config_manager.get('discogs.token', '')
        if not token:
            return None, False
        return deps.get_discogs_client(token), True
    if source_name == 'hydrabase':
        if deps.hydrabase_client and deps.hydrabase_client.is_connected():
            return deps.hydrabase_client, True
        return None, False
    if source_name == 'musicbrainz':
        try:
            from core.musicbrainz_search import MusicBrainzSearchClient
            return MusicBrainzSearchClient(), True
        except Exception as e:
            logger.warning(f"MusicBrainz search client init failed: {e}")
            return None, False
    return None, False


def _build_db_artists(query: str, deps: SearchDeps) -> list[dict]:
    active_server = deps.config_manager.get_active_media_server()
    artist_objs = deps.database.search_artists(query, limit=5, server_source=active_server)
    out: list[dict] = []
    for artist in artist_objs:
        image_url = None
        if hasattr(artist, 'thumb_url') and artist.thumb_url:
            image_url = deps.fix_artist_image_url(artist.thumb_url)
        out.append({
            'id': artist.id,
            'name': artist.name,
            'image_url': image_url,
        })
    return out


def _short_query_response(db_artists: list[dict], requested_source: str, deps: SearchDeps) -> dict:
    """Skip the remote search for queries shorter than 3 chars."""
    short_source = requested_source or deps.get_metadata_fallback_source()
    return {
        'db_artists': db_artists,
        'spotify_artists': [],
        'spotify_albums': [],
        'spotify_tracks': [],
        'metadata_source': short_source,
        'primary_source': short_source,
        'alternate_sources': [],
        'sources': {},
    }


def _single_source_response(
    query: str,
    db_artists: list[dict],
    requested_source: str,
    deps: SearchDeps,
) -> dict:
    """Run a single-source search — bypasses the fan-out."""
    client, available = resolve_client(requested_source, deps)
    if not client:
        return {
            'db_artists': db_artists,
            'spotify_artists': [],
            'spotify_albums': [],
            'spotify_tracks': [],
            'metadata_source': requested_source,
            'primary_source': requested_source,
            'alternate_sources': [],
            'source_available': False,
        }

    try:
        source_results = sources.search_source(query, client, requested_source)
    except Exception as e:
        logger.warning(f"Single-source search ({requested_source}) failed: {e}")
        source_results = {'artists': [], 'albums': [], 'tracks': [], 'available': False}

    logger.info(
        f"Enhanced search [source={requested_source}] results: "
        f"{len(db_artists)} DB, {len(source_results['artists'])} artists, "
        f"{len(source_results['albums'])} albums, {len(source_results['tracks'])} tracks"
    )

    return {
        'db_artists': db_artists,
        'spotify_artists': source_results['artists'],
        'spotify_albums': source_results['albums'],
        'spotify_tracks': source_results['tracks'],
        'metadata_source': requested_source,
        'primary_source': requested_source,
        'alternate_sources': [],
        'source_available': True,
    }


def _alternate_sources(primary_source: str, deps: SearchDeps) -> list[str]:
    """Build the list of alternate sources the frontend should fetch async."""
    spotify_available = bool(deps.spotify_client and deps.spotify_client.is_spotify_authenticated())
    hydrabase_available = bool(deps.hydrabase_client and deps.hydrabase_client.is_connected())
    discogs_available = bool(deps.config_manager.get('discogs.token', ''))

    alts: list[str] = []
    if primary_source != 'spotify' and spotify_available:
        alts.append('spotify')
    if primary_source != 'itunes':
        alts.append('itunes')
    if primary_source != 'deezer':
        alts.append('deezer')
    if primary_source != 'discogs' and discogs_available:
        alts.append('discogs')
    if primary_source != 'hydrabase' and hydrabase_available:
        alts.append('hydrabase')
    alts.append('youtube_videos')   # always available (yt-dlp, no auth)
    alts.append('musicbrainz')      # always available (public API)
    return alts


def _fan_out_response(query: str, db_artists: list[dict], deps: SearchDeps) -> dict:
    """Default flow: pick a primary source, run it, list alternates."""
    # Per-request empty marker — used for identity check at the spotify-fallback
    # gate below. Local (not module-level) so a future caller can't accidentally
    # mutate it across requests.
    empty_source = {"artists": [], "albums": [], "tracks": [], "available": False}

    primary_source = 'spotify'
    primary_results = empty_source

    if deps.is_hydrabase_active():
        primary_source = 'hydrabase'
        try:
            primary_results = sources.search_source(query, deps.hydrabase_client)
            deps.run_background_comparison(query, hydrabase_counts={
                'tracks': len(primary_results['tracks']),
                'artists': len(primary_results['artists']),
                'albums': len(primary_results['albums']),
            })
        except Exception as e:
            logger.error(f"Hydrabase search failed: {e}")
            primary_source = 'spotify'
            primary_results = empty_source

    if primary_source != 'hydrabase':
        if deps.hydrabase_worker and deps.dev_mode_enabled_provider():
            deps.hydrabase_worker.enqueue(query, 'tracks')
            deps.hydrabase_worker.enqueue(query, 'albums')
            deps.hydrabase_worker.enqueue(query, 'artists')

        fb_source = deps.get_metadata_fallback_source()
        try:
            primary_results = sources.search_source(query, deps.get_metadata_fallback_client(), fb_source)
            primary_source = fb_source
        except Exception as e:
            logger.debug(f"Primary source ({fb_source}) search failed: {e}")

        if primary_results is empty_source and fb_source != 'spotify':
            if deps.spotify_client and deps.spotify_client.is_spotify_authenticated():
                try:
                    primary_results = sources.search_source(query, deps.spotify_client, 'spotify')
                    primary_source = 'spotify'
                except Exception as e:
                    logger.debug(f"Spotify fallback search failed: {e}")

    alternate_sources = _alternate_sources(primary_source, deps)

    logger.info(
        f"Enhanced search results ({primary_source}): {len(db_artists)} DB artists, "
        f"{len(primary_results['artists'])} artists, "
        f"{len(primary_results['albums'])} albums, "
        f"{len(primary_results['tracks'])} tracks | "
        f"Alt sources available: {alternate_sources}"
    )

    return {
        'db_artists': db_artists,
        'spotify_artists': primary_results['artists'],
        'spotify_albums': primary_results['albums'],
        'spotify_tracks': primary_results['tracks'],
        'metadata_source': primary_source,
        'primary_source': primary_source,
        'alternate_sources': alternate_sources,
    }


def empty_response() -> dict:
    """Response shape for an empty query — preserves the legacy spotify-default keys."""
    return {
        'db_artists': [],
        'spotify_artists': [],
        'spotify_albums': [],
        'spotify_tracks': [],
        'sources': {},
        'primary_source': 'spotify',
        'metadata_source': 'spotify',
    }


def run_enhanced_search(query: str, requested_source: str, deps: SearchDeps) -> dict:
    """Main flow: build db_artists, then dispatch to the right strategy.

    Caller is responsible for cache lookup / store and request shape; this
    function returns a plain dict.
    """
    db_artists = _build_db_artists(query, deps)

    if len(query) < 3:
        return _short_query_response(db_artists, requested_source, deps)

    if requested_source:
        return _single_source_response(query, db_artists, requested_source, deps)

    return _fan_out_response(query, db_artists, deps)


# ---------------------------------------------------------------------------
# NDJSON streaming for /api/enhanced-search/source/<src>
# ---------------------------------------------------------------------------

def resolve_youtube_videos_client(deps: SearchDeps):
    """Return the YouTube download client (used for music-video search)
    via the orchestrator's generic accessor, or None when unavailable."""
    if not deps.soulseek_client or not hasattr(deps.soulseek_client, 'client'):
        return None
    return deps.soulseek_client.client('youtube')


def stream_youtube_videos(query: str, youtube_client, run_async: Callable) -> Iterator[str]:
    """yt-dlp video search generator — yields one videos chunk + done marker.

    Caller is responsible for verifying youtube_client is not None.
    """
    try:
        video_query = f"{query} official music video"
        results = run_async(youtube_client.search_videos(video_query, max_results=20))
        videos = []
        for v in (results or []):
            videos.append({
                'video_id': v.video_id,
                'title': v.title,
                'channel': v.channel,
                'duration': v.duration,
                'thumbnail': v.thumbnail,
                'url': v.url,
                'view_count': v.view_count,
                'upload_date': v.upload_date,
            })
        yield json.dumps({'type': 'videos', 'data': videos}) + '\n'
    except Exception as e:
        logger.error(f"YouTube music video search failed: {e}")
        yield json.dumps({'type': 'videos', 'data': []}) + '\n'
    yield json.dumps({'type': 'done'}) + '\n'


def stream_metadata_source(source_name: str, query: str, client) -> Iterator[str]:
    """Fan three search-kinds out and yield each as it lands.

    Caller is responsible for resolving and validating the client.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(sources.search_kind, client, query, 'artists', source_name): 'artists',
            executor.submit(sources.search_kind, client, query, 'albums', source_name): 'albums',
            executor.submit(sources.search_kind, client, query, 'tracks', source_name): 'tracks',
        }
        for future in as_completed(futures):
            kind = futures[future]
            try:
                payload = future.result()
            except Exception as e:
                logger.warning(f"{kind.title()} search failed for {source_name}: {e}", exc_info=True)
                payload = []
            yield json.dumps({'type': kind, 'data': payload}) + '\n'

    yield json.dumps({'type': 'done'}) + '\n'
