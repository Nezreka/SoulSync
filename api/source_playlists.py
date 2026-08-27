"""Per-source playlist systems - lifted from web_server.py.

the whole band in one piece: source track search (spotify/itunes/deezer/
musicbrainz), the discover album fetch, hifi instance management, the
deezer-download / amazon / tidal-download / qobuz auth surfaces, and the
playlist + discovery + sync systems for tidal, deezer, qobuz, spotify
public links, itunes/apple music links and youtube - plus the shared
discovery/sync spine they all run on (states, executors, the pause/resume
enrichment dance, the mirrored-playlist bridge).

lifted WHOLE on purpose: per-source slices were tried before and reverted,
the sources share this spine and cutting between them is what bleeds.
outside users (beatport, listenbrainz, wishlist, debug-info, the wiring
for artist_watchlist and soulid) import the states/executors/helpers back
from here.

bodies byte-identical; only the decorator changed and the rebindable boot
globals (clients, enrichment workers, hydrabase pair, dev_mode, the
automation deps) became injected getters - each is rebound at auth/settings
time, so holding the object would hold a stale one.
"""

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

import requests
from flask import Blueprint, jsonify, request

from core.downloads import history as _downloads_history
from core.metadata.cache import get_metadata_cache
from core.profile_context import admin_only, get_current_profile_id
from core.spotify_client import _is_globally_rate_limited as _spotify_rate_limited
from utils.async_helpers import run_async
from utils.logging_config import get_logger

logger = get_logger("web_server")

# ── injected by configure() ──────────────────────────────────────────────────
# stable boot objects and helper functions (bound once, never rebound)
sync_executor = None
sync_lock = None
sync_states = None
active_sync_workers = None
socketio = None
download_orchestrator = None
media_server_engine = None
sync_service = None
automation_engine = None
get_database = None
config_manager = None
add_activity_item = None
_format_playlist_sync_status = None
_get_active_discovery_source = None
_get_batch_max_concurrent = None
_is_hydrabase_active = None
_load_sync_status_file = None
_process_wishlist_automatically = None
_record_sync_history_start = None
_recover_youtube_artist_cleaned = None
_run_full_missing_tracks_process = None
_tracks_with_mirrored_quality_profile = None
_update_and_save_sync_status = None
_update_automation_progress = None
is_wishlist_actually_processing = None
get_tidal_client_for_profile = None
parse_youtube_playlist = None
# rebindable boot globals - injected as getters
_spotify_client = None
_tidal_client = None
_matching_engine = None
_hydrabase_client = None
_hydrabase_worker = None
_itunes_enrichment_worker = None
_qobuz_enrichment_worker = None
_spotify_enrichment_worker = None
_tidal_enrichment_worker = None
_dev_mode_enabled = None
_get_automation_deps = None
_ytmusic_auth_headers = None


def configure(**deps):
    """bind every injected name above. web_server calls this once at wiring
    time with keyword arguments matching the global names exactly."""
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"source_playlists.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('source_playlists', __name__)


def create_blueprint():
    return bp

@bp.route('/api/spotify/search', methods=['GET'])
def search_spotify():
    """Generic Spotify search endpoint - supports tracks, albums, artists"""
    use_hydrabase = _is_hydrabase_active()
    if not use_hydrabase:
        if not _spotify_client() or not _spotify_client().is_authenticated():
            return jsonify({"error": "Spotify not authenticated."}), 401

    try:
        query = request.args.get('q', '').strip()
        search_type = request.args.get('type', 'track').strip()
        limit = int(request.args.get('limit', 20))

        if not query:
            return jsonify({"error": "Query parameter 'q' is required"}), 400

        if use_hydrabase:
            tracks = _hydrabase_client().search_tracks(query, limit=limit)
        else:
            # Mirror to Hydrabase P2P network
            if _hydrabase_worker() and _dev_mode_enabled():
                _hydrabase_worker().enqueue(query, search_type)
            tracks = _spotify_client().search_tracks(query, limit=limit)

        tracks_items = [{
            'id': t.id,
            'name': t.name,
            'artists': t.artists if isinstance(t.artists, list) else [t.artists],
            'album': t.album,
            'duration_ms': t.duration_ms,
            'uri': f"spotify:track:{t.id}"
        } for t in tracks]

        return jsonify({'tracks': {'items': tracks_items}})

    except Exception as e:
        logger.error(f"Error searching Spotify: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/spotify/search_tracks', methods=['GET'])
def search_spotify_tracks():
    """Search for tracks on Spotify - used by discovery fix modal"""
    use_hydrabase = _is_hydrabase_active()
    if not use_hydrabase:
        if not _spotify_client() or not _spotify_client().is_authenticated():
            return jsonify({"error": "Spotify not authenticated."}), 401

    try:
        # Support field-specific search params (track, artist) or legacy combined query
        track_q = request.args.get('track', '').strip()
        artist_q = request.args.get('artist', '').strip()
        legacy_query = request.args.get('query', '').strip()
        limit = int(request.args.get('limit', 20))

        # Plain combined query — NOT field-scoped (`track:X artist:Y`). That Spotify
        # syntax leaks to non-Spotify sources when search falls back (Deezer aborted
        # the connection on it); the iTunes/Deezer endpoints already dropped it for the
        # same reason, and the rerank below recovers precision. (Pool-fix "no results".)
        from core.metadata.relevance import build_combined_search_query
        query = build_combined_search_query(track_q, artist_q, legacy_query)
        if not query:
            return jsonify({"error": "Query parameter is required"}), 400

        if use_hydrabase:
            tracks = _hydrabase_client().search_tracks(query, limit=limit)
        else:
            if _hydrabase_worker() and _dev_mode_enabled():
                _hydrabase_worker().enqueue(query, 'tracks')
            tracks = _spotify_client().search_tracks(query, limit=limit)

        # Local rerank — same helper Deezer + iTunes use. Spotify's
        # ranking is usually clean but karaoke / cover variants do
        # leak through; this is the safety net so all three sources
        # behave consistently from the user's perspective.
        if track_q or artist_q:
            from core.metadata.relevance import rerank_tracks
            tracks = rerank_tracks(
                tracks,
                expected_title=track_q,
                expected_artist=artist_q,
            )

        tracks_dict = [{
            'id': t.id,
            'name': t.name,
            'artists': t.artists,
            'album': t.album,
            'duration_ms': t.duration_ms,
            'image_url': getattr(t, 'image_url', None),
        } for t in tracks]

        return jsonify({'tracks': tracks_dict})

    except Exception as e:
        logger.error(f"Error searching Spotify tracks: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/itunes/search_tracks', methods=['GET'])
def search_itunes_tracks():
    """Search for tracks on iTunes — used by the import-modal
    "Search for Match" dialog and by discovery-fix flows.

    iTunes API doesn't expose a field-scoped search syntax, so the
    query stays as a free-text join of track + artist. But the
    response often still contains karaoke / cover / tribute variants
    (just usually fewer than Deezer), so the same
    ``core.metadata.relevance.rerank_tracks`` pass applies. Boosts
    exact-artist-match + penalises known cover/karaoke patterns.
    """
    try:
        # Support field-specific search params or legacy combined query
        track_q = request.args.get('track', '').strip()
        artist_q = request.args.get('artist', '').strip()
        legacy_query = request.args.get('query', '').strip()
        limit = int(request.args.get('limit', 20))

        if track_q or artist_q:
            parts = []
            if track_q:
                parts.append(track_q)
            if artist_q:
                parts.append(artist_q)
            query = ' '.join(parts)
        elif legacy_query:
            query = legacy_query
        else:
            return jsonify({"error": "Query parameter is required"}), 400

        use_hydrabase = _is_hydrabase_active()
        if use_hydrabase:
            tracks = _hydrabase_client().search_tracks(query, limit=limit)
            source = 'hydrabase'
        else:
            if _hydrabase_worker() and _dev_mode_enabled():
                _hydrabase_worker().enqueue(query, 'tracks')
            fallback_client = _get_metadata_fallback_client()
            tracks = fallback_client.search_tracks(query, limit=limit)
            source = _get_metadata_fallback_source()

        # Local rerank — same helper Deezer uses, applied wherever we
        # have an expected title/artist signal. Catches karaoke / cover
        # / tribute results that slip through iTunes's own ranking.
        if track_q or artist_q:
            from core.metadata.relevance import rerank_tracks
            tracks = rerank_tracks(
                tracks,
                expected_title=track_q,
                expected_artist=artist_q,
            )

        tracks_dict = [{
            'id': t.id,
            'name': t.name,
            'artists': t.artists,
            'album': t.album,
            'duration_ms': t.duration_ms,
            'image_url': t.image_url,
            'source': source
        } for t in tracks]

        return jsonify({'tracks': tracks_dict})

    except Exception as e:
        logger.error(f"Error searching iTunes tracks: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/deezer/search_tracks', methods=['GET'])
def search_deezer_tracks():
    """Search for tracks on Deezer — used by the import-modal "Search
    for Match" dialog and by discovery-fix flows.

    Issue #534: Deezer's free-text ranking buries canonical recordings
    under karaoke / cover / "originally performed by" variants in some
    regions. The fix here is the local relevance rerank
    (``core.metadata.relevance.rerank_tracks``) which penalises cover /
    karaoke / tribute / remaster patterns + boosts exact-artist-match.
    Catches the user-reported case (karaoke at top) and the inverse
    (live-version compilation noise) regardless of which Deezer
    region's ranking the user hits.

    Field-scoped advanced-syntax queries (`track:"X" artist:"Y"`) were
    initially considered as a second tightening layer, but live-API
    testing showed Deezer's advanced-query ranking has its own bias —
    e.g. it surfaced a 2008 Remaster on `track:"Dirty White Boy"
    artist:"Foreigner"` and didn't return the canonical Head Games cut
    at all. The free-text path actually returns the canonical
    recording first more reliably, so this endpoint stays free-text +
    local rerank. Client-level kwarg support remains in
    ``DeezerClient.search_tracks`` for future callers (e.g. exact-match
    flows where filtering is more important than ranking).
    """
    try:
        track_q = request.args.get('track', '').strip()
        artist_q = request.args.get('artist', '').strip()
        legacy_query = request.args.get('query', '').strip()
        limit = int(request.args.get('limit', 20))

        if track_q or artist_q:
            query = ' '.join(p for p in (track_q, artist_q) if p)
        elif legacy_query:
            query = legacy_query
        else:
            return jsonify({"error": "Query parameter is required"}), 400

        client = _get_deezer_client()
        tracks = client.search_tracks(query, limit=limit)

        # Local rerank — only when we have an expected title/artist
        # signal. Free-text-only searches have nothing to rank against.
        if track_q or artist_q:
            from core.metadata.relevance import rerank_tracks
            tracks = rerank_tracks(
                tracks,
                expected_title=track_q,
                expected_artist=artist_q,
            )

        tracks_dict = [{
            'id': t.id,
            'name': t.name,
            'artists': t.artists,
            'album': t.album,
            'duration_ms': t.duration_ms,
            'image_url': getattr(t, 'image_url', None),
            'source': 'deezer'
        } for t in tracks]

        return jsonify({'tracks': tracks_dict})

    except Exception as e:
        logger.error(f"Error searching Deezer tracks: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/musicbrainz/search_tracks', methods=['GET'])
def search_musicbrainz_tracks():
    """Search for tracks on MusicBrainz — used by the Discovery Fix popup
    cascade and any future surface that needs track-level MB search in the
    Fix-popup track shape.

    Mirrors the spotify / itunes / deezer search_tracks endpoints exactly:
    accepts `track` + `artist` (or legacy `query`) plus `limit`, returns
    `{tracks: [{id, name, artists, album, duration_ms, image_url, source}]}`.

    Uses MB's bare-query mode for max recall (diacritic-folded,
    alias/sortname indexed) — same rationale as the manual MBID-paste
    endpoint shipped earlier. The Fix popup is a user-facing fuzzy search
    where the user picks from the result list, so recall beats precision.
    """
    try:
        track_q = request.args.get('track', '').strip()
        artist_q = request.args.get('artist', '').strip()
        legacy_query = request.args.get('query', '').strip()
        limit = int(request.args.get('limit', 20))

        if not (track_q or artist_q or legacy_query):
            return jsonify({"error": "Query parameter is required"}), 400

        from core.musicbrainz_search import MusicBrainzSearchClient
        mb_search = MusicBrainzSearchClient()

        if track_q or artist_q:
            tracks = mb_search.search_tracks_with_artist(
                track_q or legacy_query, artist_q, limit=limit
            )
        else:
            # Legacy single-string query — let MB's structured-query
            # dispatch decide artist-first browse vs text search.
            tracks = mb_search.search_tracks(legacy_query, limit=limit)

        # Local rerank — same helper Deezer / iTunes use. Penalises
        # cover / karaoke / tribute patterns + boosts exact-artist match.
        # `prefer_known_duration=True` is MB-specific: MB has multiple
        # recordings per song (single / album / compilation / remaster
        # editions) and not every recording carries length data. The
        # flag promotes length-known recordings ahead of length-less
        # duplicates when relevance scores tie, so the user sees the
        # actionable 3:04 row before the 0:00 sibling.
        if track_q or artist_q:
            from core.metadata.relevance import rerank_tracks
            tracks = rerank_tracks(
                tracks,
                expected_title=track_q,
                expected_artist=artist_q,
                prefer_known_duration=True,
            )

        tracks_dict = [{
            'id': t.id,
            'name': t.name,
            'artists': t.artists,
            'album': t.album,
            'duration_ms': t.duration_ms,
            'image_url': t.image_url,
            'source': 'musicbrainz',
        } for t in tracks]

        return jsonify({'tracks': tracks_dict})

    except Exception as e:
        logger.error(f"Error searching MusicBrainz tracks: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/itunes/album/<album_id>', methods=['GET'])
def get_itunes_album_tracks(album_id):
    """Fetches full track details for a specific iTunes album."""
    try:
        # Try Hydrabase first when active — look up by album soul_id
        if _is_hydrabase_active():
            album_name = request.args.get('name', '')
            album_artist = request.args.get('artist', '')
            try:
                hydra_tracks = _hydrabase_client().get_album_tracks(album_id, limit=50)
                if hydra_tracks:
                    track_items = []
                    for t in hydra_tracks:
                        artist_list = t.artists if isinstance(t.artists, list) else [t.artists] if t.artists else []
                        track_items.append({
                            'name': t.name,
                            'track_number': t.track_number or 0,
                            'disc_number': t.disc_number or 1,
                            'duration_ms': t.duration_ms,
                            'id': t.id,
                            'artists': [{'name': a} if isinstance(a, str) else a for a in artist_list],
                            'uri': ''
                        })
                    return jsonify({
                        'id': album_id,
                        'name': album_name or hydra_tracks[0].album or '',
                        'artists': [{'name': album_artist}] if album_artist else [],
                        'release_date': '',
                        'total_tracks': len(track_items),
                        'album_type': 'album',
                        'images': [],
                        'tracks': track_items,
                        'source': 'hydrabase'
                    })
            except Exception as e:
                logger.warning(f"Hydrabase album_tracks failed for '{album_id}', falling back to iTunes: {e}")

        fallback_client = _get_metadata_fallback_client()
        album_data = fallback_client.get_album(album_id)

        if not album_data:
            return jsonify({"error": "Album not found"}), 404

        # Get tracks for this album
        tracks_data = fallback_client.get_album_tracks(album_id)
        tracks = tracks_data.get('items', []) if tracks_data else []

        # Format response to match Spotify structure for frontend compatibility
        album_dict = {
            'id': album_data.get('id', album_id),
            'name': album_data.get('name', 'Unknown Album'),
            'artists': album_data.get('artists', []),
            'release_date': album_data.get('release_date', ''),
            'total_tracks': album_data.get('total_tracks', len(tracks)),
            'album_type': album_data.get('album_type', 'album'),
            'images': album_data.get('images', []),
            'tracks': tracks,
            'source': _get_metadata_fallback_source()
        }
        return jsonify(album_dict)

    except Exception as e:
        logger.error(f"Error fetching album tracks: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/discover/album/<source>/<album_id>', methods=['GET'])
def get_discover_album(source, album_id):
    """
    Source-agnostic album endpoint for discover page.
    Fetches album from the appropriate source (spotify, itunes, or hydrabase when active).
    """
    try:
        # Try Hydrabase first when active — look up by album soul_id
        if _is_hydrabase_active():
            album_name = request.args.get('name', '')
            album_artist = request.args.get('artist', '')
            try:
                hydra_tracks = _hydrabase_client().get_album_tracks(album_id, limit=50)
                if hydra_tracks:
                    track_items = []
                    for t in hydra_tracks:
                        artist_list = t.artists if isinstance(t.artists, list) else [t.artists] if t.artists else []
                        track_items.append({
                            'name': t.name,
                            'track_number': t.track_number or 0,
                            'disc_number': t.disc_number or 1,
                            'duration_ms': t.duration_ms,
                            'id': t.id,
                            'artists': [{'name': a} if isinstance(a, str) else a for a in artist_list],
                            'uri': ''
                        })
                    return jsonify({
                        'id': album_id,
                        'name': album_name or hydra_tracks[0].album or '',
                        'artists': [{'name': album_artist}] if album_artist else [],
                        'release_date': '',
                        'total_tracks': len(track_items),
                        'album_type': 'album',
                        'images': [],
                        'tracks': track_items,
                        'source': 'hydrabase'
                    })
            except Exception as e:
                logger.warning(f"Hydrabase album_tracks failed for '{album_id}', falling back to {source}: {e}")

        if source == 'spotify':
            album_data = _spotify_client().get_album(album_id) if _spotify_client() and _spotify_client().is_authenticated() else None

            if album_data:
                tracks = album_data.get('tracks', {}).get('items', [])
                if not tracks:
                    tracks_data = _spotify_client().get_album_tracks(album_id)
                    if tracks_data and 'items' in tracks_data:
                        tracks = tracks_data['items']

                return jsonify({
                    'id': album_data['id'],
                    'name': album_data['name'],
                    'artists': album_data.get('artists', []),
                    'release_date': album_data.get('release_date', ''),
                    'total_tracks': album_data.get('total_tracks', 0),
                    'album_type': album_data.get('album_type', 'album'),
                    'images': album_data.get('images', []),
                    'tracks': tracks,
                    'source': 'spotify'
                })

            # Spotify failed (not authenticated, album removed, rate limited) — try fallback
            album_name = request.args.get('name', '')
            album_artist = request.args.get('artist', '')
            fallback = _get_metadata_fallback_client()
            if fallback and (album_name or album_artist):
                clean_name = album_name.replace(' - Single', '').replace(' - EP', '').replace(' (Single)', '').strip()
                search_query = f"{album_artist} {clean_name}" if album_artist else clean_name
                try:
                    results = fallback.search_albums(search_query, limit=3)
                    for r in (results or []):
                        tracks_data = fallback.get_album_tracks(str(r.id))
                        tracks = tracks_data.get('items', []) if tracks_data else []
                        if tracks:
                            return jsonify({
                                'id': str(r.id),
                                'name': r.name,
                                'artists': [{'name': getattr(r, 'artist', album_artist) or album_artist}],
                                'release_date': getattr(r, 'release_date', '') or '',
                                'total_tracks': getattr(r, 'total_tracks', len(tracks)),
                                'album_type': getattr(r, 'album_type', 'album') or 'album',
                                'images': [{'url': r.image_url}] if getattr(r, 'image_url', None) else [],
                                'tracks': tracks,
                                'source': _get_metadata_fallback_source(),
                            })
                except Exception as e:
                    logger.debug(f"Fallback album resolve failed: {e}")

            return jsonify({"error": "Album not found"}), 404

        elif source in ('itunes', 'deezer'):
            # Use the source-specific client, not just the active fallback
            if source == 'deezer':
                fallback_client = _get_deezer_client()
                fallback_source = 'deezer'
            else:
                fallback_client = _get_itunes_client()
                fallback_source = 'itunes'

            album_data = fallback_client.get_album(album_id)

            # If ID doesn't resolve (cross-source ID), search by name+artist
            if not album_data:
                album_name = request.args.get('name', '')
                album_artist = request.args.get('artist', '')
                if album_name or album_artist:
                    clean_name = album_name.replace(' - Single', '').replace(' - EP', '').replace(' (Single)', '').strip()
                    search_query = f"{album_artist} {clean_name}" if album_artist else clean_name
                    try:
                        results = fallback_client.search_albums(search_query, limit=3)
                        for r in (results or []):
                            tracks_data = fallback_client.get_album_tracks(str(r.id))
                            tracks = tracks_data.get('items', []) if tracks_data else []
                            if tracks:
                                return jsonify({
                                    'id': str(r.id),
                                    'name': r.name,
                                    'artists': [{'name': getattr(r, 'artist', album_artist) or album_artist}],
                                    'release_date': getattr(r, 'release_date', '') or '',
                                    'total_tracks': getattr(r, 'total_tracks', len(tracks)),
                                    'album_type': getattr(r, 'album_type', 'album') or 'album',
                                    'images': [{'url': r.image_url}] if getattr(r, 'image_url', None) else [],
                                    'tracks': tracks,
                                    'source': fallback_source,
                                })
                    except Exception as e:
                        logger.debug(f"Fallback album name search failed: {e}")

            if not album_data:
                return jsonify({"error": "Album not found"}), 404

            tracks_data = fallback_client.get_album_tracks(album_id)
            tracks = tracks_data.get('items', []) if tracks_data else []

            return jsonify({
                'id': album_data.get('id', album_id),
                'name': album_data.get('name', 'Unknown Album'),
                'artists': album_data.get('artists', []),
                'release_date': album_data.get('release_date', ''),
                'total_tracks': album_data.get('total_tracks', len(tracks)),
                'album_type': album_data.get('album_type', 'album'),
                'images': album_data.get('images', []),
                'tracks': tracks,
                'source': fallback_source,
            })

        elif source == 'tidal':
            # Tidal albums from Your Albums (sourced via the V2 user-
            # collection endpoint). Two-call resolution: get_album for
            # metadata, get_album_tracks for the cursor-paginated
            # tracklist. `get_album_tracks` returns `Track` objects
            # with `track_number` / `disc_number` annotated so the
            # download modal renders in album order across multi-disc
            # releases. Serialise to the same shape Spotify/Deezer
            # return so the frontend track-mapping stays uniform.
            if not _tidal_client() or not _tidal_client().is_authenticated():
                return jsonify({"error": "Tidal not authenticated"}), 401

            album_meta = _tidal_client().get_album(album_id)
            tidal_tracks = _tidal_client().get_album_tracks(album_id)

            if not album_meta and not tidal_tracks:
                return jsonify({"error": "Tidal album not found"}), 404

            album_name = (album_meta or {}).get('title') or request.args.get('name', '')
            release_date = (album_meta or {}).get('releaseDate', '')
            total_tracks = (album_meta or {}).get('numberOfItems') or len(tidal_tracks)
            album_artist_name = request.args.get('artist', '')

            # Build cover image URL from the album metadata. Tidal
            # exposes cover art via the `coverArt` relationship which
            # `get_album` doesn't fetch (it's a one-shot attributes
            # call). Best-effort: request it inline.
            cover_url = ''
            try:
                cover_resp = _tidal_client().session.get(
                    f"{_tidal_client().base_url}/albums/{album_id}",
                    params={'countryCode': 'US', 'include': 'coverArt'},
                    headers={'accept': 'application/vnd.api+json'},
                    timeout=10,
                )
                if cover_resp.status_code == 200:
                    payload = cover_resp.json()
                    _, artworks = _tidal_client()._build_included_maps(payload.get('included', []))
                    cover_rel = (payload.get('data') or {}).get('relationships', {}).get('coverArt', {})
                    cover_url = _tidal_client()._first_artwork_url(cover_rel, artworks) or ''
            except Exception as e:
                logger.debug(f"Tidal cover-art resolve failed for album {album_id}: {e}")

            tracks_out = []
            for t in tidal_tracks:
                tracks_out.append({
                    'id': t.id,
                    'name': t.name,
                    'artists': [{'name': a} for a in (t.artists or [])],
                    'duration_ms': t.duration_ms,
                    'track_number': getattr(t, 'track_number', 0),
                    'disc_number': getattr(t, 'disc_number', 1),
                })

            # Album-level artist name preference: explicit ?artist=
            # query (passed by frontend with the saved-album row) wins
            # over guessing from the first track. The saved-album row
            # already resolved the canonical artist via the V2
            # collection endpoint.
            if not album_artist_name and tidal_tracks:
                first_artists = tidal_tracks[0].artists or []
                album_artist_name = first_artists[0] if first_artists else ''

            return jsonify({
                'id': album_id,
                'name': album_name or 'Unknown Album',
                'artists': [{'name': album_artist_name}] if album_artist_name else [],
                'release_date': release_date,
                'total_tracks': total_tracks,
                'album_type': 'album',
                'images': [{'url': cover_url}] if cover_url else [],
                'tracks': tracks_out,
                'source': 'tidal',
            })

        elif source == 'discogs':
            # Discogs release detail. release_id comes from the Your
            # Albums Discogs source. Tracklist needs normalizing —
            # Discogs uses {position, title, duration} (duration as
            # string like "3:45") so map to the standard
            # {name, track_number, duration_ms, artists} shape the
            # download modal expects.
            from core.discogs_client import DiscogsClient
            try:
                rel_id = int(album_id)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid Discogs release id"}), 400

            release = DiscogsClient().get_release(rel_id)
            if not release:
                return jsonify({"error": "Discogs release not found"}), 404

            import re as _re
            _disambig_re = _re.compile(r'\s*\(\d+\)$')
            artists_raw = release.get('artists') or []
            artist_names = []
            for a in artists_raw:
                name = (a.get('name') or '').strip() if isinstance(a, dict) else str(a)
                # Strip Discogs disambiguation suffix "(N)"
                name = _disambig_re.sub('', name)
                if name:
                    artist_names.append({'name': name})

            tracks_out = []
            for idx, t in enumerate(release.get('tracklist', []) or [], start=1):
                if not isinstance(t, dict):
                    continue
                title = (t.get('title') or '').strip()
                if not title:
                    continue
                # Discogs duration: "3:45" or "1:23:45". Convert to ms.
                dur_ms = 0
                dur_str = (t.get('duration') or '').strip()
                if dur_str:
                    try:
                        parts = [int(p) for p in dur_str.split(':')]
                        if len(parts) == 2:
                            dur_ms = (parts[0] * 60 + parts[1]) * 1000
                        elif len(parts) == 3:
                            dur_ms = (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
                    except (ValueError, TypeError):
                        dur_ms = 0
                tracks_out.append({
                    'id': f"discogs_{rel_id}_{idx}",
                    'name': title,
                    'track_number': idx,
                    'duration_ms': dur_ms,
                    'artists': artist_names,
                })

            images = release.get('images') or []
            cover_url = ''
            if images and isinstance(images[0], dict):
                cover_url = images[0].get('uri') or images[0].get('uri150') or ''

            year = release.get('year')
            release_date = str(year) if year and int(year) > 0 else ''

            return jsonify({
                'id': str(rel_id),
                'name': release.get('title', ''),
                'artists': artist_names,
                'release_date': release_date,
                'total_tracks': len(tracks_out),
                'album_type': 'album',
                'images': [{'url': cover_url}] if cover_url else [],
                'tracks': tracks_out,
                'source': 'discogs',
            })

        else:
            return jsonify({"error": f"Unknown source: {source}"}), 400

    except Exception as e:
        logger.error(f"Error fetching discover album: {e}")
        return jsonify({"error": str(e)}), 500


# ===================================================================
# HIFI DOWNLOAD ENDPOINTS
# ===================================================================

@bp.route('/api/hifi/status', methods=['GET'])
def hifi_status():
    """Check if HiFi API instances are reachable."""
    try:
        hifi = download_orchestrator.client("hifi")
        available = hifi.is_available()
        version = hifi.get_version() if available else None
        return jsonify({
            "available": available,
            "version": version,
            "instance": hifi._get_instance(),
        })
    except Exception as e:
        return jsonify({"available": False, "error": str(e)})


@bp.route('/api/soundcloud/status', methods=['GET'])
def soundcloud_status():
    """Report SoundCloud client availability + a quick reachability probe.

    SoundCloud anonymous mode needs no credentials, so "configured" is
    really "yt-dlp is installed and SoundCloud responds to a search."
    The check fans out a real (cheap) yt-dlp call so the settings page's
    Test Connection button gives a meaningful pass/fail signal instead
    of just verifying the import succeeded.
    """
    try:
        sc = download_orchestrator.client("soundcloud") if download_orchestrator and hasattr(download_orchestrator, 'client') else None
        if not sc:
            return jsonify({
                "available": False,
                "configured": False,
                "error": "SoundCloud client not initialized — check yt-dlp install",
            })
        if not sc.is_available():
            return jsonify({
                "available": False,
                "configured": False,
                "error": "yt-dlp not installed",
            })
        reachable = run_async(sc.check_connection())
        return jsonify({
            "available": True,
            "configured": True,
            "reachable": bool(reachable),
        })
    except Exception as exc:
        return jsonify({"available": False, "configured": False, "error": str(exc)})


@bp.route('/api/hifi/instances', methods=['GET'])
def hifi_instances():
    """Check availability of all HiFi API instances."""
    try:
        hifi = download_orchestrator.client("hifi")
        instances = list(hifi._instances)
        results = []
        for url in instances:
            results.append(hifi.check_instance_capabilities(url))
        return jsonify({'instances': results, 'active': hifi._get_instance()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/hifi/instances', methods=['POST'])
@admin_only
def hifi_add_instance():
    """Add a new HiFi API instance."""
    try:
        data = request.get_json() or {}
        url = data.get('url', '').strip().rstrip('/')
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'}), 400
        if not url.startswith(('http://', 'https://')):
            return jsonify({'success': False, 'error': 'URL must start with http:// or https://'}), 400
        from database.music_database import get_database
        db = get_database()
        # Get current count to assign next priority
        existing = db.get_all_hifi_instances()
        priority = len(existing)
        added = db.add_hifi_instance(url, priority)
        if not added:
            return jsonify({'success': False, 'error': 'Instance already exists'}), 400
        # Reload the HiFi client
        if download_orchestrator:
            download_orchestrator.reload_instances('hifi')
        return jsonify({'success': True, 'url': url})
    except Exception as e:
        logger.error(f"Error adding HiFi instance: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/hifi/instances', methods=['DELETE'])
@admin_only
def hifi_remove_instance():
    """Remove a HiFi API instance."""
    try:
        url = (request.args.get('url') or '').strip().rstrip('/')
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'}), 400
        from database.music_database import get_database
        db = get_database()
        removed = db.remove_hifi_instance(url)
        if not removed:
            return jsonify({'success': False, 'error': 'Instance not found'}), 404
        # Reload the HiFi client
        if download_orchestrator:
            download_orchestrator.reload_instances('hifi')
        return jsonify({'success': True, 'url': url})
    except Exception as e:
        logger.error(f"Error removing HiFi instance: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/hifi/instances/toggle', methods=['POST'])
@admin_only
def hifi_toggle_instance():
    """Toggle enabled state of a HiFi API instance."""
    try:
        data = request.get_json() or {}
        url = data.get('url', '').strip().rstrip('/')
        enabled = data.get('enabled', True)
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'}), 400
        from database.music_database import get_database
        db = get_database()
        db.toggle_hifi_instance(url, enabled)
        if download_orchestrator:
            download_orchestrator.reload_instances('hifi')
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error toggling HiFi instance: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/hifi/instances/reorder', methods=['POST'])
@admin_only
def hifi_reorder_instances():
    """Reorder HiFi API instances."""
    try:
        data = request.get_json() or {}
        urls = data.get('urls', [])
        if not urls:
            return jsonify({'success': False, 'error': 'URL list is required'}), 400
        from database.music_database import get_database
        db = get_database()
        if not db.reorder_hifi_instances(urls):
            return jsonify({'success': False, 'error': 'One or more URLs not found'}), 400
        # Reload the HiFi client
        if download_orchestrator:
            download_orchestrator.reload_instances('hifi')
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error reordering HiFi instances: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/hifi/instances/reset', methods=['POST'])
@admin_only
def hifi_reset_instances():
    """Restore any built-in default HiFi instances that were removed.

    Non-destructive (#Sokhi): keeps user-added instances and the existing
    order/enabled state, and only re-adds the provided defaults that are
    currently missing — so you can recover ones you removed by accident without
    wiping the working instance you just found."""
    try:
        from database.music_database import get_database
        from core.hifi_client import DEFAULT_INSTANCES
        db = get_database()
        existing = {(i.get('url') or '').rstrip('/') for i in db.get_all_hifi_instances()}
        priority = len(existing)
        restored = []
        for url in DEFAULT_INSTANCES:
            u = url.rstrip('/')
            if u not in existing and db.add_hifi_instance(u, priority):
                restored.append(u)
                priority += 1
        if download_orchestrator:
            download_orchestrator.reload_instances('hifi')
        return jsonify({'success': True, 'restored': len(restored), 'urls': restored})
    except Exception as e:
        logger.error(f"Error resetting HiFi instances: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/hifi/instances/list', methods=['GET'])
@admin_only
def hifi_list_instances():
    """Get editable list of HiFi API instances."""
    try:
        from database.music_database import get_database
        from core.hifi_client import DEFAULT_INSTANCES
        db = get_database()
        db.seed_hifi_instances(DEFAULT_INSTANCES)
        instances = db.get_all_hifi_instances()
        return jsonify({'instances': instances})
    except Exception as e:
        logger.error(f"Error listing HiFi instances: {e}")
        return jsonify({'error': str(e)}), 500


# ===================================================================
# DEEZER DOWNLOAD ENDPOINTS
# ===================================================================

@bp.route('/api/deezer-download/test', methods=['POST'])
def deezer_download_test():
    """Test Deezer ARL token authentication."""
    try:
        data = request.get_json() or {}
        # An empty/redaction-sentinel value means "test the SAVED token" — the
        # settings field round-trips a mask for a saved-but-untouched secret, so
        # testing it must use the stored ARL, not the mask (#870).
        arl = config_manager.resolve_secret('deezer_download.arl', data.get('arl'))
        if not arl:
            return jsonify({'success': False, 'error': 'No ARL token provided'})

        import requests as req
        import threading

        session = req.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        session.cookies.set('arl', arl)

        resp = session.post(
            'https://www.deezer.com/ajax/gw-light.php',
            params={'method': 'deezer.getUserData', 'api_version': '1.0', 'api_token': 'null'},
            json={},
            # (connect, read): a host that blackholes deezer.com (VPS ranges,
            # blocked regions — #1137) fails in 5s instead of pinning one of
            # gunicorn's 8 request threads for the full 15 (this test runs
            # synchronously in the handler, and the settings page auto-fires
            # every source test on load).
            timeout=(5, 15)
        )
        logger.debug(f"Deezer test raw response status={resp.status_code}, body_preview={resp.text[:500]}")
        resp.raise_for_status()
        result = resp.json().get('results', {})

        user = result.get('USER', {})
        user_id = user.get('USER_ID', 0)
        logger.info(f"Deezer test: USER_ID={user_id}, keys={list(result.keys())}, user_keys={list(user.keys()) if user else 'none'}")
        if not user_id or user_id == 0:
            # Log more detail for debugging
            error_info = result.get('error', result.get('ERROR', ''))
            logger.warning(f"Deezer ARL test failed — USER_ID={user_id}, error={error_info}, response_keys={list(result.keys())}")
            return jsonify({'success': False, 'error': f'Invalid ARL token — Deezer returned no user (USER_ID={user_id})'})

        user_name = user.get('BLOG_NAME', 'Unknown')
        options = user.get('OPTIONS', {})
        can_lossless = options.get('web_lossless', False)
        can_hq = options.get('web_hq', False)
        tier = 'HiFi' if can_lossless else ('Premium' if can_hq else 'Free')

        return jsonify({'success': True, 'user': user_name, 'tier': tier})
    except Exception as e:
        logger.error(f"Deezer download test failed: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/deezer-download/test-search', methods=['GET'])
def deezer_download_test_search():
    """Test Deezer download search (temporary testing endpoint)."""
    try:
        query = request.args.get('q', '')
        if not query:
            return jsonify({'success': False, 'error': 'No query provided'})

        arl = config_manager.get('deezer_download.arl', '')
        if not arl:
            return jsonify({'success': False, 'error': 'No ARL configured'})

        from core.deezer_download_client import DeezerDownloadClient
        client = DeezerDownloadClient()
        if not client.is_authenticated():
            client.reconnect(arl)
        if not client.is_authenticated():
            return jsonify({'success': False, 'error': 'Authentication failed'})

        tracks, albums = client._search_sync(query)
        results = []
        for t in tracks[:10]:
            results.append({
                'title': t.title,
                'artist': t.artist,
                'album': t.album,
                'quality': t.quality,
                'bitrate': t.bitrate,
                'duration_ms': t.duration,
                'size': t.size,
                'filename': t.filename,
            })
        return jsonify({'success': True, 'count': len(tracks), 'results': results})
    except Exception as e:
        logger.error(f"Deezer search test failed: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/api/deezer-download/test-download', methods=['POST'])
def deezer_download_test_download():
    """Test Deezer download of a single track (temporary testing endpoint)."""
    try:
        data = request.get_json() or {}
        filename = data.get('filename', '')
        if not filename:
            return jsonify({'success': False, 'error': 'No filename provided (use track_id||display_name format)'})

        arl = config_manager.get('deezer_download.arl', '')
        if not arl:
            return jsonify({'success': False, 'error': 'No ARL configured'})

        from core.deezer_download_client import DeezerDownloadClient
        client = DeezerDownloadClient()
        if not client.is_authenticated():
            client.reconnect(arl)
        if not client.is_authenticated():
            return jsonify({'success': False, 'error': 'Authentication failed'})

        download_id = run_async(client.download('deezer_dl', filename))
        if not download_id:
            return jsonify({'success': False, 'error': 'Download failed to start'})

        return jsonify({'success': True, 'download_id': download_id, 'message': 'Download started — check logs'})
    except Exception as e:
        logger.error(f"Deezer download test failed: {e}")
        return jsonify({'success': False, 'error': str(e)})


# ===================================================================
# AMAZON DOWNLOAD ENDPOINTS
# ===================================================================

@bp.route('/api/amazon/test-connection', methods=['GET'])
@admin_only
def amazon_test_connection():
    """Check whether the T2Tunes proxy is up and Amazon Music is reachable."""
    try:
        from core.amazon_client import AmazonClient
        c = AmazonClient()
        status = c.status()
        amazon_up = str(status.get('amazonMusic', '')).lower() == 'up'
        return jsonify({
            'connected': amazon_up,
            'status': status,
        })
    except Exception as e:
        return jsonify({'connected': False, 'error': str(e)}), 200


# TIDAL DOWNLOAD AUTH ENDPOINTS
# ===================================================================

def _get_tidal_download_client():
    """Get Tidal download client from the orchestrator, with helpful error if unavailable."""
    if not download_orchestrator:
        raise RuntimeError("Download orchestrator not initialized — check startup logs for errors")
    tidal = download_orchestrator.client("tidal") if hasattr(download_orchestrator, 'client') else None
    if not tidal:
        raise RuntimeError("Tidal download client not available — ensure tidalapi is installed")
    return tidal

@bp.route('/api/tidal/download/auth/start', methods=['POST'])
def tidal_download_auth_start():
    """Start Tidal device-code OAuth flow for download client."""
    try:
        tidal_dl = _get_tidal_download_client()
        result = tidal_dl.start_device_auth()
        if result:
            return jsonify({"success": True, **result})
        else:
            return jsonify({"error": "Failed to start Tidal auth. Is tidalapi installed?"}), 500
    except Exception as e:
        return jsonify({"error": f"Failed to start Tidal auth: {e}"}), 500


@bp.route('/api/tidal/download/auth/check', methods=['GET'])
def tidal_download_auth_check():
    """Check status of Tidal device-code OAuth flow."""
    try:
        tidal_dl = _get_tidal_download_client()
        result = tidal_dl.check_device_auth()
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route('/api/tidal/download/auth/status', methods=['GET'])
def tidal_download_auth_status():
    """Check if Tidal download client is authenticated."""
    try:
        tidal_dl = _get_tidal_download_client()
        authenticated = tidal_dl.is_authenticated()
        return jsonify({"authenticated": authenticated})
    except Exception as e:
        return jsonify({"authenticated": False, "error": str(e)})


# ===================================================================
# QOBUZ AUTH ENDPOINTS
# ===================================================================

def _sync_qobuz_credentials_to_worker():
    """Push the just-saved Qobuz session into the enrichment worker's
    QobuzClient. Two separate client instances run side by side (one for
    the auth endpoints, one for the worker thread); without this sync the
    worker's instance never sees the new token until the next process
    restart, which is what made the dashboard indicator stay yellow and
    the connection test return ``Qobuz not authenticated`` after a
    successful Connect."""
    try:
        worker = _qobuz_enrichment_worker()
        if worker and getattr(worker, 'client', None):
            worker.client.reload_credentials()
    except Exception as e:
        logger.debug(f"Could not sync Qobuz credentials to enrichment worker: {e}")


@bp.route('/api/qobuz/auth/login', methods=['POST'])
def qobuz_auth_login():
    """Login to Qobuz with email/password."""
    try:
        data = request.get_json()
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()

        if not email or not password:
            return jsonify({"success": False, "error": "Email and password required"}), 400

        qobuz = download_orchestrator.client("qobuz")
        result = qobuz.login(email, password)

        if result['status'] == 'success':
            _sync_qobuz_credentials_to_worker()
            return jsonify({"success": True, **result})
        else:
            return jsonify({"success": False, "error": result.get('message', 'Login failed')}), 400

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/qobuz/auth/token', methods=['POST'])
def qobuz_auth_token():
    """Login to Qobuz with a pasted user_auth_token (bypasses CAPTCHA)."""
    try:
        data = request.get_json()
        token = data.get('token', '').strip()

        if not token:
            return jsonify({"success": False, "error": "Auth token required"}), 400

        qobuz = download_orchestrator.client("qobuz")
        result = qobuz.login_with_token(token)

        if result['status'] == 'success':
            _sync_qobuz_credentials_to_worker()
            return jsonify({"success": True, **result})
        else:
            return jsonify({"success": False, "error": result.get('message', 'Token login failed')}), 400

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/qobuz/auth/status', methods=['GET'])
def qobuz_auth_status():
    """Check if Qobuz client is authenticated."""
    try:
        qobuz = download_orchestrator.client("qobuz")
        authenticated = qobuz.is_authenticated()
        user_info = {}
        if authenticated and qobuz.user_info:
            user_info = {
                'display_name': qobuz.user_info.get('display_name', ''),
                'subscription': qobuz.user_info.get('credential', {}).get('label', 'Unknown'),
            }
        return jsonify({"authenticated": authenticated, "user": user_info})
    except Exception as e:
        return jsonify({"authenticated": False, "error": str(e)})


@bp.route('/api/qobuz/auth/logout', methods=['POST'])
def qobuz_auth_logout():
    """Logout from Qobuz."""
    try:
        download_orchestrator.client("qobuz").logout()
        _sync_qobuz_credentials_to_worker()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ===================================================================
# TIDAL PLAYLIST API ENDPOINTS
# ===================================================================

@bp.route('/api/tidal/disconnect', methods=['POST'])
def tidal_disconnect():
    """Clear saved Tidal auth state. Use when re-authentication doesn't
    pick up newly-added scopes (e.g. existing token predates a scope
    expansion and `prompt=consent` alone isn't enough to force fresh
    consent on this user's auth flow)."""
    if not _tidal_client():
        return jsonify({"error": "Tidal client not available."}), 500
    try:
        _tidal_client().disconnect()
        return jsonify({
            'success': True,
            'message': 'Tidal disconnected. Re-authenticate from Settings → Connections.',
            'authenticated': False,
        })
    except Exception as e:
        logger.error(f"Tidal disconnect error: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/tidal/playlists', methods=['GET'])
def get_tidal_playlists():
    """Fetches all user playlists from Tidal with full track data (like sync.py)."""
    client = get_tidal_client_for_profile() or _tidal_client()
    if not client or not client.is_authenticated():
        return jsonify({"error": "Tidal not authenticated."}), 401
    try:
        # Use same method as sync.py - this already includes all track data
        playlists = client.get_user_playlists_metadata_only()
        
        playlist_data = []
        for p in playlists:
            # Get track count from metadata (set during listing) or actual tracks
            track_count = getattr(p, 'track_count', 0) or (len(p.tracks) if hasattr(p, 'tracks') and p.tracks else 0)
            
            playlist_dict = {
                "id": p.id, 
                "name": p.name, 
                "owner": getattr(p, 'owner', 'Unknown'),
                "track_count": track_count,
                "image_url": getattr(p, 'image_url', None),
                "description": getattr(p, 'description', ''),
                "tracks": []  # Add tracks data like sync.py
            }
            
            # Include full track data if available (like sync.py has)
            if hasattr(p, 'tracks') and p.tracks:
                playlist_dict['tracks'] = [{
                    'id': t.id,
                    'name': t.name, 
                    'artists': t.artists or [],
                    'album': getattr(t, 'album', 'Unknown Album'),
                    'duration_ms': getattr(t, 'duration_ms', 0),
                    'track_number': getattr(t, 'track_number', 0)
                } for t in p.tracks]
                
            playlist_data.append(playlist_dict)

        # Append virtual "Favorite Tracks" playlist at the END (mirrors
        # Spotify's "Liked Songs" treatment — count-only here, full
        # track fetch deferred to the per-playlist detail endpoint).
        # When the saved Tidal token doesn't have `collection.read`
        # scope (existing tokens predate the scope expansion), the
        # endpoint returns 401 — we still surface the entry but with
        # a `needs_reconnect` flag + a reconnect-hint name so the user
        # has something visible to act on instead of a silently missing
        # row.
        try:
            from core.tidal_client import (
                COLLECTION_PLAYLIST_ID,
                COLLECTION_PLAYLIST_NAME,
                COLLECTION_PLAYLIST_DESCRIPTION,
            )
            collection_count = client.get_collection_tracks_count()
            needs_reconnect = client.collection_needs_reconnect()

            if needs_reconnect:
                playlist_data.append({
                    "id": COLLECTION_PLAYLIST_ID,
                    "name": f"{COLLECTION_PLAYLIST_NAME} (reconnect Tidal to enable)",
                    "owner": "You",
                    "track_count": 0,
                    "image_url": None,
                    "description": "Reconnect Tidal in Settings → Connections to grant the new collection.read scope.",
                    "needs_reconnect": True,
                    "tracks": [],
                })
                logger.info(
                    "Tidal Favorite Tracks: token missing `collection.read` scope — surfacing reconnect hint."
                )
            elif collection_count > 0:
                playlist_data.append({
                    "id": COLLECTION_PLAYLIST_ID,
                    "name": COLLECTION_PLAYLIST_NAME,
                    "owner": "You",
                    "track_count": collection_count,
                    "image_url": None,
                    "description": COLLECTION_PLAYLIST_DESCRIPTION,
                    "tracks": [],
                })
                logger.info(
                    f"Added virtual '{COLLECTION_PLAYLIST_NAME}' playlist with {collection_count} tracks (count only)"
                )
        except Exception as collection_error:
            logger.error(f"Failed to add Tidal Favorite Tracks playlist: {collection_error}")
            # Don't fail the entire request if Favorite Tracks fails

        logger.info(f"Loaded {len(playlist_data)} Tidal playlists with track data")
        return jsonify(playlist_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route('/api/tidal/playlist/<playlist_id>', methods=['GET'])
def get_tidal_playlist_tracks(playlist_id):
    """Fetches full track details for a specific Tidal playlist (matches sync.py pattern)."""
    client = get_tidal_client_for_profile() or _tidal_client()
    if not client or not client.is_authenticated():
        return jsonify({"error": "Tidal not authenticated."}), 401
    try:
        logger.info(f"Getting full Tidal playlist with tracks for: {playlist_id}")

        # Fetch this single playlist directly — no need to re-fetch all playlists.
        # `get_playlist` recognizes the virtual `tidal-favorites` ID and
        # dispatches to the userCollectionTracks endpoint internally, so
        # the rest of this handler treats it identically to a real playlist.
        full_playlist = client.get_playlist(playlist_id)
        if not full_playlist:
            return jsonify({"error": "Playlist not found or unable to access. This may be due to privacy settings or Tidal API restrictions."}), 404
            
        if not full_playlist.tracks:
            return jsonify({"error": "This playlist appears to have no tracks or they cannot be accessed"}), 403
        
        logger.info(f"Loaded {len(full_playlist.tracks)} tracks from Tidal playlist: {full_playlist.name}")
        
        # Convert playlist to dict (matches sync.py structure)
        playlist_dict = {
            'id': full_playlist.id,
            'name': full_playlist.name,
            'description': getattr(full_playlist, 'description', ''),
            'owner': getattr(full_playlist, 'owner', 'Unknown'),
            'track_count': len(full_playlist.tracks),
            'image_url': getattr(full_playlist, 'image_url', None),
            'tracks': []
        }
        
        # Convert tracks to dict format (for discovery modal)
        playlist_dict['tracks'] = [{
            'id': t.id,
            'name': t.name, 
            'artists': t.artists or [],
            'album': getattr(t, 'album', 'Unknown Album'),
            'duration_ms': getattr(t, 'duration_ms', 0),
            'track_number': getattr(t, 'track_number', 0)
        } for t in full_playlist.tracks]
        
        return jsonify(playlist_dict)
    except Exception as e:
        logger.error(f"Error getting Tidal playlist tracks: {e}")
        return jsonify({"error": str(e)}), 500


# ===================================================================
# TIDAL DISCOVERY API ENDPOINTS
# ===================================================================

# Global state for Tidal playlist discovery management
tidal_discovery_states = {}  # Key: playlist_id, Value: discovery state
tidal_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tidal_discovery")

@bp.route('/api/tidal/discovery/start/<playlist_id>', methods=['POST'])
def start_tidal_discovery(playlist_id):
    """Start Spotify discovery process for a Tidal playlist"""
    try:
        # Get playlist data from Tidal
        if not _tidal_client() or not _tidal_client().is_authenticated():
            return jsonify({"error": "Tidal not authenticated."}), 401

        # Fetch this single playlist directly — no need to re-fetch all playlists
        target_playlist = _tidal_client().get_playlist(playlist_id)

        if not target_playlist:
            return jsonify({"error": "Tidal playlist not found"}), 404

        if not target_playlist.tracks:
            return jsonify({"error": "Playlist has no tracks"}), 400
        
        # Initialize discovery state if it doesn't exist, or update existing state
        if playlist_id in tidal_discovery_states:
            existing_state = tidal_discovery_states[playlist_id]
            if existing_state['phase'] == 'discovering':
                return jsonify({"error": "Discovery already in progress"}), 400
            # Update existing state for discovery
            existing_state['phase'] = 'discovering'
            existing_state['status'] = 'discovering' 
            existing_state['last_accessed'] = time.time()
            state = existing_state
        else:
            # Create new state for first-time discovery
            state = {
                'playlist': target_playlist,
                'phase': 'discovering', # fresh -> discovering -> discovered -> syncing -> sync_complete -> downloading -> download_complete
                'status': 'discovering',
                'discovery_progress': 0,
                'spotify_matches': 0,
                'spotify_total': len(target_playlist.tracks),
                'discovery_results': [],
                'sync_playlist_id': None,
                'converted_spotify_playlist_id': None,
                'download_process_id': None,  # Track associated download missing tracks process
                'created_at': time.time(),
                'last_accessed': time.time(),
                'discovery_future': None,
                'sync_progress': {}
            }
            tidal_discovery_states[playlist_id] = state
        
        # Add activity for discovery start
        add_activity_item("", "Tidal Discovery Started", f"'{target_playlist.name}' - {len(target_playlist.tracks)} tracks", "Now")
        
        # Start discovery worker (capture profile ID while we have Flask context)
        state['_profile_id'] = get_current_profile_id()
        future = tidal_discovery_executor.submit(_run_tidal_discovery_worker, playlist_id)
        state['discovery_future'] = future
        
        logger.info(f"Started Spotify discovery for Tidal playlist: {target_playlist.name}")
        return jsonify({"success": True, "message": "Discovery started"})
        
    except Exception as e:
        logger.error(f"Error starting Tidal discovery: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/tidal/discovery/status/<playlist_id>', methods=['GET'])
def get_tidal_discovery_status(playlist_id):
    """Get real-time discovery status for a Tidal playlist"""
    return _get_source_discovery_status(tidal_discovery_states, playlist_id, "Tidal discovery not found", "Tidal")


@bp.route('/api/tidal/discovery/update_match', methods=['POST'])
def update_tidal_discovery_match():
    """Update a Tidal discovery result with manually selected Spotify track"""
    return _update_source_discovery_match(tidal_discovery_states, "tidal", "Tidal", "tidal_track", _first_artist_str_or_obj)


@bp.route('/api/tidal/playlists/states', methods=['GET'])
def get_tidal_playlist_states():
    """Get all stored Tidal playlist discovery states for frontend hydration (similar to YouTube playlists)"""
    return _get_source_playlist_states(tidal_discovery_states, "Tidal", "Tidal")

@bp.route('/api/tidal/state/<playlist_id>', methods=['GET'])
def get_tidal_playlist_state(playlist_id):
    """Get specific Tidal playlist state (detailed version matching YouTube's state endpoint)"""
    try:
        if playlist_id not in tidal_discovery_states:
            return jsonify({"error": "Tidal playlist not found"}), 404
        
        state = tidal_discovery_states[playlist_id]
        state['last_accessed'] = time.time()
        
        # Return full state information (including results for modal hydration)
        response = {
            'playlist_id': playlist_id,
            'playlist': state['playlist'].__dict__ if hasattr(state['playlist'], '__dict__') else state['playlist'],
            'phase': state['phase'],
            'status': state['status'],
            'discovery_progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'discovery_results': state['discovery_results'],
            'sync_playlist_id': state.get('sync_playlist_id'),
            'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
            'download_process_id': state.get('download_process_id'),
            'sync_progress': state.get('sync_progress', {}),
            'last_accessed': state['last_accessed']
        }
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error getting Tidal playlist state: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/tidal/reset/<playlist_id>', methods=['POST'])
def reset_tidal_playlist(playlist_id):
    """Reset Tidal playlist to fresh phase (clear discovery/sync data)"""
    return _reset_source_playlist(tidal_discovery_states, playlist_id, "Tidal", "Tidal playlist not found")

@bp.route('/api/tidal/delete/<playlist_id>', methods=['POST'])
def delete_tidal_playlist(playlist_id):
    """Delete Tidal playlist state completely"""
    return _delete_source_playlist(tidal_discovery_states, playlist_id, "Tidal", "Tidal playlist not found")

@bp.route('/api/tidal/update_phase/<playlist_id>', methods=['POST'])
def update_tidal_playlist_phase(playlist_id):
    """Update Tidal playlist phase (used when modal closes to reset from download_complete to discovered)"""
    return _update_source_playlist_phase(tidal_discovery_states, playlist_id, "Tidal playlist not found", "Tidal", _PHASE_LIST, False)


_playlist_discovery_cancelled = set()  # Set of automation_ids that have been cancelled

def _pause_enrichment_workers(label='discovery'):
    """Pause enrichment workers during discovery to reduce API contention.
    Returns dict of {name: was_running} for resume."""
    was_running = {}
    workers = {
        'Spotify': _spotify_enrichment_worker(),
        'iTunes': _itunes_enrichment_worker(),
        'Tidal': _tidal_enrichment_worker(),
        'Qobuz': _qobuz_enrichment_worker(),
    }
    for name, worker in workers.items():
        try:
            if worker and not worker.paused:
                worker.pause()
                was_running[name] = True
                logger.warning(f"Paused {name} enrichment worker during {label}")
        except Exception as e:
            logger.debug("enrichment worker pause failed: %s", e)
    return was_running


def _resume_enrichment_workers(was_running, label='discovery'):
    """Resume enrichment workers that were paused by _pause_enrichment_workers."""
    workers = {
        'Spotify': _spotify_enrichment_worker(),
        'iTunes': _itunes_enrichment_worker(),
        'Tidal': _tidal_enrichment_worker(),
        'Qobuz': _qobuz_enrichment_worker(),
    }
    for name, worker in workers.items():
        try:
            if was_running.get(name) and worker:
                worker.resume()
                logger.info(f"Resumed {name} enrichment worker after {label}")
        except Exception as e:
            logger.debug("enrichment worker resume failed: %s", e)


def _sync_discovery_results_to_mirrored(source_type, source_playlist_id, discovery_results, discovery_source, profile_id=1):
    """Write discovery results back to the mirrored playlist's extra_data.
    Called after Tidal/Deezer/Beatport discovery completes.
    Matches by source_track_id first, then by track position (index)."""
    try:
        db = get_database()
        playlists = db.get_mirrored_playlists(profile_id=profile_id)
        mirrored_pl = None
        for pl in playlists:
            if pl.get('source') == source_type and str(pl.get('source_playlist_id')) == str(source_playlist_id):
                mirrored_pl = pl
                break

        if not mirrored_pl:
            logger.warning(f"[Discovery Sync] No mirrored playlist found for {source_type}:{source_playlist_id} (profile {profile_id})")
            return

        logger.info(f"[Discovery Sync] Found mirrored playlist '{mirrored_pl.get('name')}' (DB id={mirrored_pl['id']}) for {source_type}:{source_playlist_id}")
        mirrored_tracks = db.get_mirrored_playlist_tracks(mirrored_pl['id'])
        if not mirrored_tracks:
            return

        # Build lookup maps: source_track_id → db_id AND position → db_id
        source_id_to_db_id = {}
        position_to_db_id = {}
        for mt in mirrored_tracks:
            sid = mt.get('source_track_id', '')
            if sid:
                source_id_to_db_id[str(sid)] = mt['id']
            pos = mt.get('position')
            if pos is not None:
                position_to_db_id[pos] = mt['id']

        updated = 0
        for result in discovery_results:
            if result.get('status') not in ('found', 'Found', 'Wing It'):
                continue

            match_data = result.get('match_data') or result.get('spotify_data')
            if not match_data:
                continue

            confidence = result.get('confidence', 0.85)

            # Try to find the mirrored track DB ID
            db_track_id = None

            # Method 1: match by source track ID
            source_track = result.get('tidal_track') or result.get('source_track') or {}
            source_tid = str(source_track.get('id', '')) if source_track else ''
            if source_tid and source_tid in source_id_to_db_id:
                db_track_id = source_id_to_db_id[source_tid]

            # Method 2: match by position/index
            if not db_track_id:
                idx = result.get('index')
                if idx is not None and idx in position_to_db_id:
                    db_track_id = position_to_db_id[idx]

            if not db_track_id:
                continue

            extra_data = {
                'discovered': True,
                'provider': discovery_source,
                'confidence': confidence,
                'matched_data': match_data,
            }
            if result.get('wing_it_fallback'):
                extra_data['wing_it_fallback'] = True
                extra_data['provider'] = 'wing_it_fallback'
            db.update_mirrored_track_extra_data(db_track_id, extra_data)
            updated += 1

        if updated > 0:
            logger.info(f"Synced {updated} discovery results back to mirrored playlist '{mirrored_pl.get('name', '')}'")

    except Exception as e:
        import traceback
        logger.error(f"Failed to sync discovery results to mirrored playlist: {e}")
        traceback.print_exc()


# Mirrored-playlist discovery worker logic lives in core/discovery/playlist.py.
from core.discovery import playlist as _discovery_playlist


def _lookup_artist_aliases(artist_name):
    """Alternate spellings for an artist name, or [] if none can be resolved.

    Thin wrapper over MusicBrainzService.lookup_artist_aliases, which already
    does the caching (library row -> musicbrainz_cache -> live MB) and applies
    the trust gate that keeps a fuzzy near-miss from renaming an artist.
    Best-effort by contract: any failure returns [] so discovery falls back to
    exactly its prior behaviour.
    """
    try:
        from core.musicbrainz_service import get_musicbrainz_service
        return get_musicbrainz_service().lookup_artist_aliases(artist_name) or []
    except Exception as e:
        logger.debug("artist alias lookup unavailable for %r: %s", artist_name, e)
        return []


def _build_playlist_discovery_deps():
    """Build the PlaylistDiscoveryDeps bundle from web_server.py globals on each call."""
    return _discovery_playlist.PlaylistDiscoveryDeps(
        spotify_client=_spotify_client(),
        matching_engine=_matching_engine(),
        automation_engine=automation_engine,
        playlist_discovery_cancelled=_playlist_discovery_cancelled,
        pause_enrichment_workers=_pause_enrichment_workers,
        resume_enrichment_workers=_resume_enrichment_workers,
        get_active_discovery_source=_get_active_discovery_source,
        get_metadata_fallback_client=_get_metadata_fallback_client,
        get_metadata_fallback_source=_get_metadata_fallback_source,
        update_automation_progress=_update_automation_progress,
        get_database=get_database,
        get_discovery_cache_key=_get_discovery_cache_key,
        validate_discovery_cache_artist=_validate_discovery_cache_artist,
        discovery_score_candidates=_discovery_score_candidates,
        get_metadata_cache=get_metadata_cache,
        build_discovery_wing_it_stub=_build_discovery_wing_it_stub,
        lookup_artist_aliases=_lookup_artist_aliases,
    )


def _run_playlist_discovery_worker(playlists, automation_id=None):
    return _discovery_playlist.run_playlist_discovery_worker(
        playlists, automation_id, _build_playlist_discovery_deps()
    )



def _extract_artist_name(artist):
    """Extract artist name string from either a string or dict ({"name": "..."}) format."""
    if isinstance(artist, dict):
        return artist.get('name', '')
    return artist or ''

def _extract_artist_names(artists):
    """Extract a list of artist name strings from a list that may contain dicts or strings."""
    return [_extract_artist_name(a) for a in (artists or [])]

def _join_artist_names(artists):
    """Join artist names from a list that may contain dicts or strings."""
    return ', '.join(_extract_artist_names(artists))

def _get_discovery_cache_key(title, artist):
    """Normalize title/artist for discovery cache lookup using _matching_engine()."""
    norm_title = _matching_engine().clean_title(title)
    norm_artist = _matching_engine().clean_artist(_extract_artist_name(artist))
    return (norm_title, norm_artist)


def _validate_discovery_cache_artist(source_artist, cached_match):
    """Check if a cached discovery match has a valid artist. Returns False if the
    cached result's artist doesn't match the source artist (stale/wrong cache entry)."""
    min_artist_similarity = 0.5
    source_artist_cleaned = _matching_engine().clean_artist(source_artist)
    if not source_artist_cleaned:
        return True  # No source artist to validate against

    cached_artists = cached_match.get('artists', [])
    if not cached_artists:
        return True  # No cached artist to check

    best_sim = 0.0
    for cand_artist in cached_artists:
        if not cand_artist:
            continue
        # Handle both string artists and dict artists ({"name": "..."})
        if isinstance(cand_artist, dict):
            cand_artist = cand_artist.get('name', '')
            if not cand_artist:
                continue
        cand_normalized = _matching_engine().normalize_string(cand_artist)
        if source_artist_cleaned in cand_normalized:
            return True
        cand_cleaned = _matching_engine().clean_artist(cand_artist)
        sim = _matching_engine().similarity_score(source_artist_cleaned, cand_cleaned)
        if sim > best_sim:
            best_sim = sim

    if best_sim < min_artist_similarity:
        logger.info(f"Cache artist mismatch: source='{source_artist}' vs cached='{cached_artists[0]}' (sim={best_sim:.2f}), re-searching")
        return False
    return True


from core.discovery.scoring import (
    _discovery_score_candidates,
    _search_spotify_for_tidal_track,
    init as _init_discovery_scoring,
)


# Tidal discovery worker logic lives in core/discovery/tidal.py.
from core.discovery import tidal as _discovery_tidal
# Source-agnostic discovery route helpers (lifted from the per-source copies).
from core.discovery.endpoints import (
    convert_results_to_spotify_tracks,
    cancel_sync as _cancel_sync_core,
    delete_playlist_state as _delete_playlist_state_core,
    get_sync_status as _get_sync_status_core,
    reconcile_sync_phase as _reconcile_sync_phase,
    get_discovery_status as _get_discovery_status_core,
    reset_playlist as _reset_playlist_core,
    get_playlist_states as _get_playlist_states_core,
    start_sync as _start_sync_core,
    update_discovery_match as _update_discovery_match_core,
    update_playlist_phase as _update_playlist_phase_core,
    save_bubble_snapshot as _save_bubble_snapshot_core,
    playlist_name_attr_or_unknown as _pl_name_attr_or_unknown,
    playlist_name_strict as _pl_name_strict,
    playlist_name_safe as _pl_name_safe,
    playlist_name_obj as _pl_name_obj,
    playlist_image_obj as _pl_image_obj,
    playlist_image_dict as _pl_image_dict,
    first_artist_str_or_obj as _first_artist_str_or_obj,
    first_artist_plain as _first_artist_plain,
)


def _cancel_source_sync(states, key, label, not_found_message):
    """Thin glue: wire web_server's sync infra into the lifted cancel_sync
    helper and jsonify the result. Used by the per-source cancel routes."""
    body, code = _cancel_sync_core(
        states, key, label=label, not_found_message=not_found_message,
        sync_lock=sync_lock, sync_states=sync_states,
        active_sync_workers=active_sync_workers,
    )
    return jsonify(body), code


def _delete_source_playlist(states, key, label, not_found_message):
    """Thin glue for the per-source delete routes that share the identical
    delete body (Tidal/Deezer/Qobuz/Spotify-Public)."""
    body, code = _delete_playlist_state_core(
        states, key, label=label, not_found_message=not_found_message,
    )
    return jsonify(body), code


def _get_source_sync_status(states, key, not_found_message, error_label,
                            activity_subject, name_getter):
    """Thin glue for the per-source get_*_sync_status routes — wires the sync
    infra + add_activity_item into the lifted helper and jsonifies."""
    body, code = _get_sync_status_core(
        states, key, not_found_message=not_found_message, error_label=error_label,
        activity_subject=activity_subject, playlist_name_getter=name_getter,
        sync_lock=sync_lock, sync_states=sync_states, add_activity_item=add_activity_item,
    )
    return jsonify(body), code


def _get_source_discovery_status(states, key, not_found_message, error_label):
    """Thin glue for the per-source get_*_discovery_status routes."""
    body, code = _get_discovery_status_core(
        states, key, not_found_message=not_found_message, error_label=error_label,
    )
    return jsonify(body), code


def _reset_source_playlist(states, key, label, not_found_message):
    """Thin glue for the per-source reset routes that share the identical
    reset body (Tidal/Deezer/Qobuz/Spotify-Public)."""
    body, code = _reset_playlist_core(
        states, key, label=label, not_found_message=not_found_message,
    )
    return jsonify(body), code


def _get_source_playlist_states(states, error_label, info_log_label=None):
    """Thin glue for the per-source get_*_playlist_states bulk-hydration routes
    (Tidal/Deezer/Qobuz/Spotify-Public/iTunes-Link)."""
    body, code = _get_playlist_states_core(
        states, error_label=error_label, info_log_label=info_log_label,
    )
    return jsonify(body), code


def _submit_sync_task(sync_playlist_id, playlist_name, spotify_tracks, playlist_image_url):
    """Submit a sync to the shared executor (closes over sync_executor /
    _run_sync_task / get_current_profile_id so the lifted start_sync helper
    stays free of those globals).

    Used by ALL per-source discovery syncs (Spotify-Public/Tidal/Deezer/Qobuz/
    YouTube/iTunes-link/ListenBrainz/Beatport). These have no per-request mode
    selector, so honor the configured default (Settings > Playlist sync mode) —
    otherwise they always ran 'replace' regardless of the setting (#792)."""
    from core.sync.playlist_edit import normalize_sync_mode
    _mode = normalize_sync_mode(None, config_manager.get('playlist_sync.mode', 'replace'))
    return sync_executor.submit(
        _run_sync_task, sync_playlist_id, playlist_name, spotify_tracks,
        None, get_current_profile_id(), playlist_image_url, _mode,
    )


def _start_source_sync(states, key, *, sync_id_prefix, not_found_message,
                       not_ready_message, convert_fn, name_getter, image_getter,
                       activity_label, error_label):
    """Thin glue for the per-source start_*_sync routes (Tidal/Deezer/Qobuz/
    Spotify-Public/YouTube) — wires sync infra into the lifted start_sync."""
    body, code = _start_sync_core(
        states, key, sync_id_prefix=sync_id_prefix,
        not_found_message=not_found_message, not_ready_message=not_ready_message,
        convert_fn=convert_fn, playlist_name_getter=name_getter,
        playlist_image_getter=image_getter, activity_label=activity_label,
        error_label=error_label, sync_lock=sync_lock, sync_states=sync_states,
        active_sync_workers=active_sync_workers, submit_sync_task=_submit_sync_task,
        add_activity_item=add_activity_item,
    )
    return jsonify(body), code


def _update_source_discovery_match(states, source_log_label, error_label,
                                   original_track_key, artist_getter):
    """Thin glue for the per-source update_*_discovery_match (fix-modal) routes
    (Tidal/Deezer/Qobuz/Spotify-Public) — injects the web_server helpers."""
    body, code = _update_discovery_match_core(
        states, lambda: request.get_json(),
        source_log_label=source_log_label, error_label=error_label,
        original_track_key=original_track_key, original_artist_getter=artist_getter,
        join_artist_names=_join_artist_names, extract_artist_name=_extract_artist_name,
        build_fix_modal_spotify_data=_build_fix_modal_spotify_data,
        get_discovery_cache_key=_get_discovery_cache_key, get_database=get_database,
        get_active_discovery_source=_get_active_discovery_source,
    )
    return jsonify(body), code


# Valid phase lists for update_*_playlist_phase (YouTube additionally allows 'parsed').
_PHASE_LIST = ['fresh', 'discovering', 'discovered', 'syncing', 'sync_complete', 'downloading', 'download_complete']
_PHASE_LIST_YT = ['fresh', 'parsed', 'discovering', 'discovered', 'syncing', 'sync_complete', 'downloading', 'download_complete']


def _update_source_playlist_phase(states, key, not_found_message, error_label,
                                  valid_phases, apply_extra_fields):
    """Thin glue for the per-source update_*_playlist_phase routes
    (Tidal/Deezer/Qobuz/Spotify-Public/YouTube)."""
    body, code = _update_playlist_phase_core(
        states, key, lambda: request.get_json(),
        not_found_message=not_found_message, error_label=error_label,
        valid_phases=valid_phases, apply_extra_fields=apply_extra_fields,
    )
    return jsonify(body), code


def _save_source_bubble_snapshot(payload_key, no_data_error, snapshot_kind,
                                 success_noun, log_subject, log_noun):
    """Thin glue for the snapshot routes (discover_downloads / artist_bubbles /
    search_bubbles / beatport_bubbles)."""
    body, code = _save_bubble_snapshot_core(
        lambda: request.json, payload_key=payload_key, no_data_error=no_data_error,
        snapshot_kind=snapshot_kind, success_noun=success_noun, log_subject=log_subject,
        log_noun=log_noun, get_database=get_database,
        get_current_profile_id=get_current_profile_id,
    )
    return jsonify(body), code


def _build_tidal_discovery_deps():
    """Build the TidalDiscoveryDeps bundle from web_server.py globals on each call."""
    return _discovery_tidal.TidalDiscoveryDeps(
        tidal_discovery_states=tidal_discovery_states,
        spotify_client=_spotify_client(),
        pause_enrichment_workers=_pause_enrichment_workers,
        resume_enrichment_workers=_resume_enrichment_workers,
        get_active_discovery_source=_get_active_discovery_source,
        get_metadata_fallback_client=_get_metadata_fallback_client,
        get_discovery_cache_key=_get_discovery_cache_key,
        get_database=get_database,
        validate_discovery_cache_artist=_validate_discovery_cache_artist,
        search_spotify_for_tidal_track=_search_spotify_for_tidal_track,
        build_discovery_wing_it_stub=_build_discovery_wing_it_stub,
        add_activity_item=add_activity_item,
        sync_discovery_results_to_mirrored=_sync_discovery_results_to_mirrored,
    )


def _run_tidal_discovery_worker(playlist_id):
    return _discovery_tidal.run_tidal_discovery_worker(playlist_id, _build_tidal_discovery_deps())





def convert_tidal_results_to_spotify_tracks(discovery_results):
    """Convert Tidal discovery results to Spotify tracks format for sync"""
    return convert_results_to_spotify_tracks(discovery_results, "Tidal")


# ===================================================================
# TIDAL SYNC API ENDPOINTS
# ===================================================================

@bp.route('/api/tidal/sync/start/<playlist_id>', methods=['POST'])
def start_tidal_sync(playlist_id):
    """Start sync process for a Tidal playlist using discovered Spotify tracks"""
    return _start_source_sync(
        tidal_discovery_states, playlist_id, sync_id_prefix="tidal",
        not_found_message="Tidal playlist not found",
        not_ready_message="Tidal playlist not ready for sync",
        convert_fn=convert_tidal_results_to_spotify_tracks,
        name_getter=_pl_name_obj, image_getter=_pl_image_obj,
        activity_label="Tidal", error_label="Tidal")

@bp.route('/api/tidal/sync/status/<playlist_id>', methods=['GET'])
def get_tidal_sync_status(playlist_id):
    """Get sync status for a Tidal playlist"""
    return _get_source_sync_status(tidal_discovery_states, playlist_id, "Tidal playlist not found", "Tidal", "Tidal playlist", _pl_name_attr_or_unknown)

@bp.route('/api/tidal/sync/cancel/<playlist_id>', methods=['POST'])
def cancel_tidal_sync(playlist_id):
    """Cancel sync for a Tidal playlist"""
    return _cancel_source_sync(tidal_discovery_states, playlist_id, "Tidal", "Tidal playlist not found")


# ===================================================================
# DEEZER PLAYLIST DISCOVERY API ENDPOINTS
# ===================================================================

# Global state for Deezer playlist discovery management
deezer_discovery_states = {}  # Key: playlist_id, Value: discovery state
deezer_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="deezer_discovery")
deezer_playlist_load_jobs = {}
deezer_playlist_load_lock = threading.Lock()


def _run_deezer_playlist_load_job(job_id, playlist_id):
    with deezer_playlist_load_lock:
        state = deezer_playlist_load_jobs.get(job_id)
        if state:
            state['status'] = 'running'
            state['started_at'] = time.time()

    def _emit_progress(done, total, phase):
        frame = {
            'playlist_id': str(playlist_id),
            'done': done,
            'total': total,
            'phase': phase,
        }
        with deezer_playlist_load_lock:
            state = deezer_playlist_load_jobs.get(job_id)
            if state:
                state['progress'] = frame
                state['updated_at'] = time.time()
        try:
            socketio.emit('deezer:playlist_progress', frame)
        except Exception as emit_err:   # noqa: BLE001 - narration must never break the fetch
            logger.debug("deezer playlist progress emit failed: %s", emit_err)

    try:
        logger.info("Started async Deezer playlist load for %s (job=%s)", playlist_id, job_id)
        playlist = _get_deezer_client().get_playlist(playlist_id, progress_cb=_emit_progress)
        if not playlist:
            raise ValueError("Deezer playlist not found")
        with deezer_playlist_load_lock:
            state = deezer_playlist_load_jobs.get(job_id)
            if state:
                state.update({
                    'status': 'complete',
                    'playlist': playlist,
                    'track_count': len(playlist.get('tracks', [])),
                    'updated_at': time.time(),
                })
        logger.info("Loaded %d tracks from Deezer playlist: %s (job=%s)",
                    len(playlist.get('tracks', [])), playlist.get('name'), job_id)
    except Exception as e:
        logger.error("Async Deezer playlist load failed for %s (job=%s): %s",
                     playlist_id, job_id, e, exc_info=True)
        with deezer_playlist_load_lock:
            state = deezer_playlist_load_jobs.get(job_id)
            if state:
                state.update({'status': 'error', 'error': str(e), 'updated_at': time.time()})


def _run_deezer_arl_playlist_load_job(job_id, playlist_id):
    with deezer_playlist_load_lock:
        state = deezer_playlist_load_jobs.get(job_id)
        if state:
            state['status'] = 'running'
            state['started_at'] = time.time()

    def _emit_progress(done, total, phase):
        frame = {
            'playlist_id': str(playlist_id),
            'done': done,
            'total': total,
            'phase': phase,
        }
        with deezer_playlist_load_lock:
            state = deezer_playlist_load_jobs.get(job_id)
            if state:
                state['progress'] = frame
                state['updated_at'] = time.time()
        try:
            socketio.emit('deezer:playlist_progress', frame)
        except Exception as emit_err:   # noqa: BLE001 - narration must never break the fetch
            logger.debug("deezer ARL playlist progress emit failed: %s", emit_err)

    try:
        deezer_dl = download_orchestrator.client("deezer_dl") if download_orchestrator and hasattr(download_orchestrator, 'client') else None
        if not deezer_dl or not deezer_dl.is_authenticated():
            raise PermissionError("Deezer ARL not authenticated.")
        logger.info("Started async Deezer ARL playlist load for %s (job=%s)", playlist_id, job_id)
        playlist = deezer_dl.get_playlist_tracks(playlist_id, progress_cb=_emit_progress)
        if not playlist:
            raise ValueError("Playlist not found or unable to access.")
        with deezer_playlist_load_lock:
            state = deezer_playlist_load_jobs.get(job_id)
            if state:
                state.update({
                    'status': 'complete',
                    'playlist': playlist,
                    'track_count': len(playlist.get('tracks', [])),
                    'updated_at': time.time(),
                })
        logger.info("Loaded %d tracks from Deezer ARL playlist: %s (job=%s)",
                    len(playlist.get('tracks', [])), playlist.get('name'), job_id)
    except Exception as e:
        logger.error("Async Deezer ARL playlist load failed for %s (job=%s): %s",
                     playlist_id, job_id, e, exc_info=True)
        with deezer_playlist_load_lock:
            state = deezer_playlist_load_jobs.get(job_id)
            if state:
                state.update({'status': 'error', 'error': str(e), 'updated_at': time.time()})


def _prune_deezer_playlist_load_jobs(max_age_seconds=3600):
    cutoff = time.time() - max_age_seconds
    with deezer_playlist_load_lock:
        stale = [
            job_id for job_id, state in deezer_playlist_load_jobs.items()
            if state.get('status') in ('complete', 'error') and state.get('updated_at', 0) < cutoff
        ]
        for job_id in stale:
            deezer_playlist_load_jobs.pop(job_id, None)

def _get_deezer_client():
    """Get cached Deezer client."""
    from core.metadata.registry import get_deezer_client
    return get_deezer_client()

def _get_itunes_client():
    """Get cached iTunes client."""
    from core.metadata.registry import get_itunes_client
    return get_itunes_client()

def _get_discogs_client(token=None):
    """Get cached Discogs client."""
    from core.metadata.registry import get_discogs_client
    return get_discogs_client(token)

def _get_metadata_fallback_source():
    """Get the configured primary metadata source.
    Returns 'spotify', 'itunes', 'deezer', 'discogs', or 'hydrabase'.

    NOTE: This is a thin wrapper — canonical logic lives in core.metadata.registry.get_primary_source().
    Kept as a local function because 70+ callers reference it by name."""
    from core.metadata.registry import get_primary_source
    return get_primary_source()

def _get_metadata_fallback_client():
    """Get the active metadata client based on settings.
    Returns a SpotifyClient, iTunesClient, DeezerClient, DiscogsClient, or HydrabaseClient instance."""
    source = _get_metadata_fallback_source()
    from core.metadata.registry import get_client_for_source

    client = get_client_for_source(source)
    if client is not None:
        return client
    if source == 'spotify':
        return _get_deezer_client()
    if source == 'discogs':
        token = config_manager.get('discogs.token', '')
        if token:
            return _get_discogs_client(token)
        return _get_itunes_client()
    if source == 'hydrabase':
        if _hydrabase_client() and _hydrabase_client().is_connected():
            return _hydrabase_client()
        return _get_itunes_client()
    if source == 'jiosaavn':
        from core.metadata.registry import get_jiosaavn_client
        client = get_jiosaavn_client()
        if client is not None:
            return client
    return _get_itunes_client()

@bp.route('/api/deezer/arl-status', methods=['GET'])
def get_deezer_arl_status():
    """Check if Deezer ARL is configured and authenticated."""
    try:
        deezer_dl = download_orchestrator.client("deezer_dl") if download_orchestrator and hasattr(download_orchestrator, 'client') else None
        if deezer_dl and deezer_dl.is_authenticated():
            user_data = deezer_dl._user_data or {}
            return jsonify({
                'authenticated': True,
                'user_name': user_data.get('BLOG_NAME', 'Unknown'),
                'user_id': user_data.get('USER_ID'),
            })
        return jsonify({'authenticated': False})
    except Exception as e:
        return jsonify({'authenticated': False, 'error': str(e)})


@bp.route('/api/deezer/arl-playlists', methods=['GET'])
def get_deezer_arl_playlists():
    """Fetch user playlists via Deezer ARL authentication (like /api/spotify/playlists)."""
    try:
        deezer_dl = download_orchestrator.client("deezer_dl") if download_orchestrator and hasattr(download_orchestrator, 'client') else None
        if not deezer_dl or not deezer_dl.is_authenticated():
            return jsonify({'error': 'Deezer ARL not authenticated. Configure your ARL token in Settings > Downloads.'}), 401

        playlists = deezer_dl.get_user_playlists()

        # Real sync status, same file every other source reads. This was a
        # hardcoded 'Never Synced' literal — the field was added "to match
        # Spotify format" but only ever carried the shape, never the value, so a
        # Deezer playlist read as never synced no matter how many times it had
        # been (TheHomeGuy: sync ran, tracks downloaded, playlist appeared on the
        # server, card still said NEVER SYNCED).
        #
        # The write side was always fine. These cards are shimmed into
        # spotifyPlaylists with id = `deezer_arl_<id>` (-sync.accounts.ts), and
        # startPlaylistSync posts that card id straight to /api/sync/start — so
        # the status lands under the PREFIXED id. The bare `deezer_<id>` belongs
        # to the other engine, the per-source discovery flow behind
        # /api/deezer/sync/start; check it second so a playlist synced that way
        # still reads as synced.
        #
        # No snapshot passed: Deezer has no snapshot/etag equivalent, so there is
        # nothing to compare a stored one against. That leaves the two states the
        # Deezer card actually renders — deezerArlStatusClass only distinguishes
        # 'Synced' from never, with no 'Needs Sync' arm.
        sync_statuses = _load_sync_status_file()
        playlist_data = []
        for p in playlists:
            status_info = (sync_statuses.get(f"deezer_arl_{p['id']}")
                           or sync_statuses.get(f"deezer_{p['id']}")
                           or {})
            playlist_data.append({
                'id': p['id'],
                'name': p['name'],
                'owner': p.get('owner', ''),
                'track_count': p.get('track_count', 0),
                'image_url': p.get('image_url', ''),
                'sync_status': _format_playlist_sync_status(status_info, None),
            })

        logger.info(f"Loaded {len(playlist_data)} Deezer user playlists via ARL")
        return jsonify(playlist_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/deezer/arl-playlist/<playlist_id>', methods=['GET'])
def get_deezer_arl_playlist_tracks(playlist_id):
    """Fetch full playlist with tracks via ARL (like /api/spotify/playlist/<id>)."""
    try:
        deezer_dl = download_orchestrator.client("deezer_dl") if download_orchestrator and hasattr(download_orchestrator, 'client') else None
        if not deezer_dl or not deezer_dl.is_authenticated():
            return jsonify({'error': 'Deezer ARL not authenticated.'}), 401

        if request.args.get('async') in ('1', 'true', 'yes'):
            _prune_deezer_playlist_load_jobs()
            with deezer_playlist_load_lock:
                for existing_id, existing in deezer_playlist_load_jobs.items():
                    if (existing.get('kind') == 'arl'
                            and existing.get('playlist_id') == str(playlist_id)
                            and existing.get('status') in ('queued', 'running')):
                        return jsonify({
                            "pending": True,
                            "job_id": existing_id,
                            "playlist_id": str(playlist_id),
                            "status": existing.get('status'),
                            "progress": existing.get('progress') or {},
                        }), 202

                job_id = f"deezer_arl_playlist_{uuid.uuid4().hex[:12]}"
                deezer_playlist_load_jobs[job_id] = {
                    'job_id': job_id,
                    'kind': 'arl',
                    'playlist_id': str(playlist_id),
                    'status': 'queued',
                    'progress': {'playlist_id': str(playlist_id), 'done': 0, 'total': 0, 'phase': 'queued'},
                    'created_at': time.time(),
                    'updated_at': time.time(),
                }
            deezer_discovery_executor.submit(_run_deezer_arl_playlist_load_job, job_id, str(playlist_id))
            return jsonify({
                "pending": True,
                "job_id": job_id,
                "playlist_id": str(playlist_id),
                "status": "queued",
            }), 202

        # Narrate the wait. Resolving a 1200-track playlist means ~1,750
        # rate-limited requests, so this GET legitimately runs for minutes and a
        # bare spinner cannot tell working from hung — which is how it was
        # reported ("seems to hang", "sit here for several minutes doing
        # nothing"). Emitted on the socket the shell already owns; core.js
        # re-broadcasts it as an ss: CustomEvent for the React card, the same
        # seam repair:progress and the scan frames use.
        def _emit_progress(done, total, phase):
            try:
                socketio.emit('deezer:playlist_progress', {
                    'playlist_id': str(playlist_id),
                    'done': done,
                    'total': total,
                    'phase': phase,
                })
            except Exception as emit_err:   # noqa: BLE001 - never let narration break the fetch
                logger.debug("deezer playlist progress emit failed: %s", emit_err)

        playlist = deezer_dl.get_playlist_tracks(playlist_id, progress_cb=_emit_progress)
        if not playlist:
            return jsonify({'error': 'Playlist not found or unable to access.'}), 404

        logger.info(f"Loaded {len(playlist.get('tracks', []))} tracks from Deezer playlist: {playlist.get('name')}")
        return jsonify(playlist)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/deezer/playlist/<playlist_id>', methods=['GET'])
def get_deezer_playlist(playlist_id):
    """Fetch a Deezer playlist by ID or URL"""
    try:
        from core.deezer_client import DeezerClient

        # Resolve, don't just parse: Deezer's own Share button copies a
        # link.deezer.com/s/… short link that carries no id until it is
        # followed. Telling a user their app's share link is "invalid" is the
        # kind of error that makes the feature look broken.
        parsed_id = DeezerClient.resolve_playlist_url(playlist_id)
        if not parsed_id:
            if DeezerClient.is_share_url(playlist_id):
                return jsonify({"error":
                                "That Deezer share link could not be resolved. Open it "
                                "in a browser and paste the deezer.com/playlist/... "
                                "address instead."}), 400
            return jsonify({"error": "Invalid Deezer playlist ID or URL"}), 400

        if request.args.get('async') in ('1', 'true', 'yes'):
            _prune_deezer_playlist_load_jobs()
            with deezer_playlist_load_lock:
                for existing_id, existing in deezer_playlist_load_jobs.items():
                    if (existing.get('kind') == 'link'
                            and existing.get('playlist_id') == str(parsed_id)
                            and existing.get('status') in ('queued', 'running')):
                        return jsonify({
                            "pending": True,
                            "job_id": existing_id,
                            "playlist_id": str(parsed_id),
                            "status": existing.get('status'),
                            "progress": existing.get('progress') or {},
                        }), 202

                job_id = f"deezer_playlist_{uuid.uuid4().hex[:12]}"
                deezer_playlist_load_jobs[job_id] = {
                    'job_id': job_id,
                    'kind': 'link',
                    'playlist_id': str(parsed_id),
                    'status': 'queued',
                    'progress': {'playlist_id': str(parsed_id), 'done': 0, 'total': 0, 'phase': 'queued'},
                    'created_at': time.time(),
                    'updated_at': time.time(),
                }
            deezer_discovery_executor.submit(_run_deezer_playlist_load_job, job_id, str(parsed_id))
            return jsonify({
                "pending": True,
                "job_id": job_id,
                "playlist_id": str(parsed_id),
                "status": "queued",
            }), 202

        # Narrate the wait, exactly as the ARL playlist endpoint does. A
        # 1500-track playlist resolves ~1,000 unique albums for real track
        # numbers, which is minutes — and this path emitted nothing at all, so
        # the button sat on "Loading..." with no logs and no frames. Same event
        # and shape as the ARL one, so the existing core.js bridge and the
        # DeezerPlaylistProgress consumer both work unchanged.
        def _emit_progress(done, total, phase):
            try:
                socketio.emit('deezer:playlist_progress', {
                    'playlist_id': str(parsed_id),
                    'done': done,
                    'total': total,
                    'phase': phase,
                })
            except Exception as emit_err:   # noqa: BLE001 - narration must never break the fetch
                logger.debug("deezer playlist progress emit failed: %s", emit_err)

        client = _get_deezer_client()
        playlist = client.get_playlist(parsed_id, progress_cb=_emit_progress)

        if not playlist:
            return jsonify({"error": "Deezer playlist not found"}), 404

        logger.info(f"Loaded {len(playlist.get('tracks', []))} tracks from Deezer playlist: "
                    f"{playlist.get('name')}")

        return jsonify(playlist)

    except Exception as e:
        logger.error(f"Error fetching Deezer playlist: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/deezer/playlist-load/<job_id>', methods=['GET'])
def get_deezer_playlist_load_status(job_id):
    with deezer_playlist_load_lock:
        state = deezer_playlist_load_jobs.get(job_id)
        if not state:
            return jsonify({"error": "Deezer playlist load not found"}), 404
        status = state.get('status')
        payload = {
            "job_id": job_id,
            "playlist_id": state.get('playlist_id'),
            "status": status,
            "progress": state.get('progress') or {},
        }
        if status == 'complete':
            payload["playlist"] = state.get('playlist')
        elif status == 'error':
            payload["error"] = state.get('error') or 'Deezer playlist load failed'
        return jsonify(payload)

@bp.route('/api/deezer/discovery/start/<playlist_id>', methods=['POST'])
def start_deezer_discovery(playlist_id):
    """Start Spotify discovery process for a Deezer playlist"""
    try:
        from core.deezer_client import DeezerClient

        # Parse URL if needed
        parsed_id = DeezerClient.parse_playlist_url(playlist_id)
        if parsed_id:
            playlist_id = parsed_id

        # Initialize discovery state if it doesn't exist, or update existing state
        if playlist_id in deezer_discovery_states:
            existing_state = deezer_discovery_states[playlist_id]
            if existing_state['phase'] == 'discovering':
                return jsonify({"error": "Discovery already in progress"}), 400

            # Fetch fresh playlist data if not already stored
            if not existing_state.get('playlist'):
                client = _get_deezer_client()
                playlist_data = client.get_playlist(playlist_id)
                if not playlist_data:
                    return jsonify({"error": "Deezer playlist not found"}), 404
                existing_state['playlist'] = playlist_data

            # Update existing state for discovery
            existing_state['phase'] = 'discovering'
            existing_state['status'] = 'discovering'
            existing_state['last_accessed'] = time.time()
            state = existing_state
        else:
            # Fetch playlist data from Deezer
            client = _get_deezer_client()
            playlist_data = client.get_playlist(playlist_id)

            if not playlist_data:
                return jsonify({"error": "Deezer playlist not found"}), 404

            if not playlist_data.get('tracks'):
                return jsonify({"error": "Playlist has no tracks"}), 400

            # Create new state for first-time discovery
            state = {
                'playlist': playlist_data,
                'phase': 'discovering',  # fresh -> discovering -> discovered -> syncing -> sync_complete -> downloading -> download_complete
                'status': 'discovering',
                'discovery_progress': 0,
                'spotify_matches': 0,
                'spotify_total': len(playlist_data['tracks']),
                'discovery_results': [],
                'sync_playlist_id': None,
                'converted_spotify_playlist_id': None,
                'download_process_id': None,
                'created_at': time.time(),
                'last_accessed': time.time(),
                'discovery_future': None,
                'sync_progress': {}
            }
            deezer_discovery_states[playlist_id] = state

        # Add activity for discovery start
        playlist_name = state['playlist']['name']
        track_count = len(state['playlist']['tracks'])
        add_activity_item("", "Deezer Discovery Started", f"'{playlist_name}' - {track_count} tracks", "Now")

        # Start discovery worker (capture profile ID while we have Flask context)
        deezer_discovery_states[playlist_id]['_profile_id'] = get_current_profile_id()
        future = deezer_discovery_executor.submit(_run_deezer_discovery_worker, playlist_id)
        state['discovery_future'] = future

        logger.info(f"Started Spotify discovery for Deezer playlist: {playlist_name}")
        return jsonify({"success": True, "message": "Discovery started"})

    except Exception as e:
        logger.error(f"Error starting Deezer discovery: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/deezer/discovery/status/<playlist_id>', methods=['GET'])
def get_deezer_discovery_status(playlist_id):
    """Get real-time discovery status for a Deezer playlist"""
    return _get_source_discovery_status(deezer_discovery_states, playlist_id, "Deezer discovery not found", "Deezer")

@bp.route('/api/deezer/discovery/update_match', methods=['POST'])
def update_deezer_discovery_match():
    """Update a Deezer discovery result with manually selected Spotify track"""
    return _update_source_discovery_match(deezer_discovery_states, "deezer", "Deezer", "deezer_track", _first_artist_plain)

@bp.route('/api/deezer/playlists/states', methods=['GET'])
def get_deezer_playlist_states():
    """Get all stored Deezer playlist discovery states for frontend hydration"""
    return _get_source_playlist_states(deezer_discovery_states, "Deezer", "Deezer")

@bp.route('/api/deezer/state/<playlist_id>', methods=['GET'])
def get_deezer_playlist_state(playlist_id):
    """Get specific Deezer playlist state (detailed version)"""
    try:
        if playlist_id not in deezer_discovery_states:
            return jsonify({"error": "Deezer playlist not found"}), 404

        state = deezer_discovery_states[playlist_id]
        state['last_accessed'] = time.time()

        # Deezer playlist is a dict, no __dict__ needed
        response = {
            'playlist_id': playlist_id,
            'playlist': state['playlist'],
            'phase': state['phase'],
            'status': state['status'],
            'discovery_progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'discovery_results': state['discovery_results'],
            'sync_playlist_id': state.get('sync_playlist_id'),
            'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
            'download_process_id': state.get('download_process_id'),
            'sync_progress': state.get('sync_progress', {}),
            'last_accessed': state['last_accessed']
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting Deezer playlist state: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/deezer/reset/<playlist_id>', methods=['POST'])
def reset_deezer_playlist(playlist_id):
    """Reset Deezer playlist to fresh phase (clear discovery/sync data)"""
    return _reset_source_playlist(deezer_discovery_states, playlist_id, "Deezer", "Deezer playlist not found")

@bp.route('/api/deezer/delete/<playlist_id>', methods=['POST'])
def delete_deezer_playlist(playlist_id):
    """Delete Deezer playlist state completely"""
    return _delete_source_playlist(deezer_discovery_states, playlist_id, "Deezer", "Deezer playlist not found")

@bp.route('/api/deezer/update_phase/<playlist_id>', methods=['POST'])
def update_deezer_playlist_phase(playlist_id):
    """Update Deezer playlist phase (used when modal closes to reset from download_complete to discovered)"""
    return _update_source_playlist_phase(deezer_discovery_states, playlist_id, "Deezer playlist not found", "Deezer", _PHASE_LIST, True)


# Deezer discovery worker logic lives in core/discovery/deezer.py.
from core.discovery import deezer as _discovery_deezer


def _build_deezer_discovery_deps():
    """Build the DeezerDiscoveryDeps bundle from web_server.py globals on each call."""
    return _discovery_deezer.DeezerDiscoveryDeps(
        deezer_discovery_states=deezer_discovery_states,
        spotify_client=_spotify_client(),
        pause_enrichment_workers=_pause_enrichment_workers,
        resume_enrichment_workers=_resume_enrichment_workers,
        get_active_discovery_source=_get_active_discovery_source,
        get_metadata_fallback_client=_get_metadata_fallback_client,
        get_discovery_cache_key=_get_discovery_cache_key,
        get_database=get_database,
        validate_discovery_cache_artist=_validate_discovery_cache_artist,
        search_spotify_for_tidal_track=_search_spotify_for_tidal_track,
        build_discovery_wing_it_stub=_build_discovery_wing_it_stub,
        add_activity_item=add_activity_item,
        sync_discovery_results_to_mirrored=_sync_discovery_results_to_mirrored,
    )


def _run_deezer_discovery_worker(playlist_id):
    return _discovery_deezer.run_deezer_discovery_worker(playlist_id, _build_deezer_discovery_deps())



def convert_deezer_results_to_spotify_tracks(discovery_results):
    """Convert Deezer discovery results to Spotify tracks format for sync"""
    return convert_results_to_spotify_tracks(discovery_results, "Deezer")


# ===================================================================
# DEEZER SYNC API ENDPOINTS
# ===================================================================

@bp.route('/api/deezer/sync/start/<playlist_id>', methods=['POST'])
def start_deezer_sync(playlist_id):
    """Start sync process for a Deezer playlist using discovered Spotify tracks"""
    return _start_source_sync(
        deezer_discovery_states, playlist_id, sync_id_prefix="deezer",
        not_found_message="Deezer playlist not found",
        not_ready_message="Deezer playlist not ready for sync",
        convert_fn=convert_deezer_results_to_spotify_tracks,
        name_getter=_pl_name_strict, image_getter=_pl_image_dict,
        activity_label="Deezer", error_label="Deezer")

@bp.route('/api/deezer/sync/status/<playlist_id>', methods=['GET'])
def get_deezer_sync_status(playlist_id):
    """Get sync status for a Deezer playlist"""
    return _get_source_sync_status(deezer_discovery_states, playlist_id, "Deezer playlist not found", "Deezer", "Deezer playlist", _pl_name_strict)

@bp.route('/api/deezer/sync/cancel/<playlist_id>', methods=['POST'])
def cancel_deezer_sync(playlist_id):
    """Cancel sync for a Deezer playlist"""
    return _cancel_source_sync(deezer_discovery_states, playlist_id, "Deezer", "Deezer playlist not found")


# ===================================================================
# QOBUZ PLAYLIST DISCOVERY API ENDPOINTS
# ===================================================================
#
# Mirrors the Tidal + Deezer endpoint set for parity on the Sync page.
# Qobuz playlists arrive from `core/qobuz_client.py` as dicts (matching
# the Deezer client's shape), so the state + endpoint code follows the
# Deezer template rather than Tidal's dataclass-based one. Github issue
# #677.

# Global state for Qobuz playlist discovery management
qobuz_discovery_states = {}  # Key: playlist_id, Value: discovery state
qobuz_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="qobuz_discovery")


def _get_qobuz_client_for_sync():
    """Resolve the Qobuz client via the download orchestrator.

    The orchestrator owns the canonical instance (same one Settings →
    Connections authenticates against), so the Sync page tab always sees
    fresh auth state without a second login flow.
    """
    if not download_orchestrator or not hasattr(download_orchestrator, 'client'):
        return None
    try:
        return download_orchestrator.client("qobuz")
    except Exception as exc:
        logger.debug(f"Qobuz client lookup failed: {exc}")
        return None


@bp.route('/api/qobuz/playlists', methods=['GET'])
def get_qobuz_playlists():
    """Fetches the authenticated user's Qobuz playlists (metadata only).

    Tracks are fetched on demand by the per-playlist detail endpoint —
    matches the Tidal + Deezer behaviour so the Sync page renderer can
    treat all three services uniformly.
    """
    qobuz = _get_qobuz_client_for_sync()
    if not qobuz or not qobuz.is_authenticated():
        return jsonify({"error": "Qobuz not authenticated."}), 401

    try:
        playlists = qobuz.get_user_playlists()

        playlist_data = []
        for p in playlists:
            playlist_data.append({
                "id": p['id'],
                "name": p['name'],
                "owner": "You",
                "track_count": p.get('track_count', 0),
                "image_url": p.get('image_url') or None,
                "description": p.get('description', ''),
                "tracks": []
            })

        # Append virtual "Favorite Tracks" entry at the END (mirrors
        # Tidal's COLLECTION_PLAYLIST_ID pattern — count only here, full
        # fetch deferred to the per-playlist detail endpoint).
        try:
            from core.qobuz_client import QobuzClient as _QobuzClientTypeRef
            favorites_count = qobuz.get_user_favorite_tracks_count()
            if favorites_count > 0:
                playlist_data.append({
                    "id": qobuz.QOBUZ_FAVORITES_ID,
                    "name": qobuz.QOBUZ_FAVORITES_NAME,
                    "owner": "You",
                    "track_count": favorites_count,
                    "image_url": None,
                    "description": qobuz.QOBUZ_FAVORITES_DESCRIPTION,
                    "tracks": [],
                })
                logger.info(
                    f"Added virtual '{qobuz.QOBUZ_FAVORITES_NAME}' playlist with {favorites_count} tracks (count only)"
                )
        except Exception as favorites_error:
            logger.error(f"Failed to add Qobuz Favorite Tracks playlist: {favorites_error}")

        logger.info(f"Loaded {len(playlist_data)} Qobuz playlists")
        return jsonify(playlist_data)
    except Exception as e:
        logger.error(f"Error loading Qobuz playlists: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/qobuz/playlist/<playlist_id>', methods=['GET'])
def get_qobuz_playlist_tracks(playlist_id):
    """Fetches full track details for a specific Qobuz playlist."""
    qobuz = _get_qobuz_client_for_sync()
    if not qobuz or not qobuz.is_authenticated():
        return jsonify({"error": "Qobuz not authenticated."}), 401

    try:
        logger.info(f"Getting full Qobuz playlist with tracks for: {playlist_id}")
        full_playlist = qobuz.get_playlist(playlist_id)
        if not full_playlist:
            return jsonify({"error": "Playlist not found or unable to access."}), 404

        tracks = full_playlist.get('tracks') or []
        if not tracks:
            return jsonify({"error": "This playlist appears to have no tracks or they cannot be accessed"}), 403

        logger.info(f"Loaded {len(tracks)} tracks from Qobuz playlist: {full_playlist['name']}")

        playlist_dict = {
            'id': full_playlist['id'],
            'name': full_playlist['name'],
            'description': full_playlist.get('description', ''),
            'owner': 'You',
            'track_count': len(tracks),
            'image_url': full_playlist.get('image_url') or None,
            'tracks': tracks,
        }
        return jsonify(playlist_dict)
    except Exception as e:
        logger.error(f"Error getting Qobuz playlist tracks: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/qobuz/discovery/start/<playlist_id>', methods=['POST'])
def start_qobuz_discovery(playlist_id):
    """Start Spotify discovery process for a Qobuz playlist."""
    try:
        qobuz = _get_qobuz_client_for_sync()
        if not qobuz or not qobuz.is_authenticated():
            return jsonify({"error": "Qobuz not authenticated."}), 401

        if playlist_id in qobuz_discovery_states:
            existing_state = qobuz_discovery_states[playlist_id]
            if existing_state['phase'] == 'discovering':
                return jsonify({"error": "Discovery already in progress"}), 400

            if not existing_state.get('playlist'):
                playlist_data = qobuz.get_playlist(playlist_id)
                if not playlist_data:
                    return jsonify({"error": "Qobuz playlist not found"}), 404
                existing_state['playlist'] = playlist_data

            existing_state['phase'] = 'discovering'
            existing_state['status'] = 'discovering'
            existing_state['last_accessed'] = time.time()
            state = existing_state
        else:
            playlist_data = qobuz.get_playlist(playlist_id)

            if not playlist_data:
                return jsonify({"error": "Qobuz playlist not found"}), 404

            if not playlist_data.get('tracks'):
                return jsonify({"error": "Playlist has no tracks"}), 400

            state = {
                'playlist': playlist_data,
                'phase': 'discovering',
                'status': 'discovering',
                'discovery_progress': 0,
                'spotify_matches': 0,
                'spotify_total': len(playlist_data['tracks']),
                'discovery_results': [],
                'sync_playlist_id': None,
                'converted_spotify_playlist_id': None,
                'download_process_id': None,
                'created_at': time.time(),
                'last_accessed': time.time(),
                'discovery_future': None,
                'sync_progress': {}
            }
            qobuz_discovery_states[playlist_id] = state

        playlist_name = state['playlist']['name']
        track_count = len(state['playlist']['tracks'])
        add_activity_item("", "Qobuz Discovery Started", f"'{playlist_name}' - {track_count} tracks", "Now")

        qobuz_discovery_states[playlist_id]['_profile_id'] = get_current_profile_id()
        future = qobuz_discovery_executor.submit(_run_qobuz_discovery_worker, playlist_id)
        state['discovery_future'] = future

        logger.info(f"Started Spotify discovery for Qobuz playlist: {playlist_name}")
        return jsonify({"success": True, "message": "Discovery started"})

    except Exception as e:
        logger.error(f"Error starting Qobuz discovery: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/qobuz/discovery/status/<playlist_id>', methods=['GET'])
def get_qobuz_discovery_status(playlist_id):
    """Get real-time discovery status for a Qobuz playlist."""
    return _get_source_discovery_status(qobuz_discovery_states, playlist_id, "Qobuz discovery not found", "Qobuz")


@bp.route('/api/qobuz/discovery/update_match', methods=['POST'])
def update_qobuz_discovery_match():
    """Update a Qobuz discovery result with manually selected Spotify track"""
    return _update_source_discovery_match(qobuz_discovery_states, "qobuz", "Qobuz", "qobuz_track", _first_artist_plain)


@bp.route('/api/qobuz/playlists/states', methods=['GET'])
def get_qobuz_playlist_states():
    """Get all stored Qobuz playlist discovery states for frontend hydration."""
    return _get_source_playlist_states(qobuz_discovery_states, "Qobuz", "Qobuz")


@bp.route('/api/qobuz/state/<playlist_id>', methods=['GET'])
def get_qobuz_playlist_state(playlist_id):
    """Get specific Qobuz playlist state (detailed version)."""
    try:
        if playlist_id not in qobuz_discovery_states:
            return jsonify({"error": "Qobuz playlist not found"}), 404

        state = qobuz_discovery_states[playlist_id]
        state['last_accessed'] = time.time()

        response = {
            'playlist_id': playlist_id,
            'playlist': state['playlist'],
            'phase': state['phase'],
            'status': state['status'],
            'discovery_progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'discovery_results': state['discovery_results'],
            'sync_playlist_id': state.get('sync_playlist_id'),
            'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
            'download_process_id': state.get('download_process_id'),
            'sync_progress': state.get('sync_progress', {}),
            'last_accessed': state['last_accessed']
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting Qobuz playlist state: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/qobuz/reset/<playlist_id>', methods=['POST'])
def reset_qobuz_playlist(playlist_id):
    """Reset Qobuz playlist to fresh phase (clear discovery/sync data)."""
    return _reset_source_playlist(qobuz_discovery_states, playlist_id, "Qobuz", "Qobuz playlist not found")


@bp.route('/api/qobuz/delete/<playlist_id>', methods=['POST'])
def delete_qobuz_playlist(playlist_id):
    """Delete Qobuz playlist state completely."""
    return _delete_source_playlist(qobuz_discovery_states, playlist_id, "Qobuz", "Qobuz playlist not found")


@bp.route('/api/qobuz/update_phase/<playlist_id>', methods=['POST'])
def update_qobuz_playlist_phase(playlist_id):
    """Update Qobuz playlist phase (used when modal closes to reset from download_complete to discovered)."""
    return _update_source_playlist_phase(qobuz_discovery_states, playlist_id, "Qobuz playlist not found", "Qobuz", _PHASE_LIST, True)


# Qobuz discovery worker logic lives in core/discovery/qobuz.py.
from core.discovery import qobuz as _discovery_qobuz


def _build_qobuz_discovery_deps():
    """Build the QobuzDiscoveryDeps bundle from web_server.py globals on each call."""
    return _discovery_qobuz.QobuzDiscoveryDeps(
        qobuz_discovery_states=qobuz_discovery_states,
        spotify_client=_spotify_client(),
        pause_enrichment_workers=_pause_enrichment_workers,
        resume_enrichment_workers=_resume_enrichment_workers,
        get_active_discovery_source=_get_active_discovery_source,
        get_metadata_fallback_client=_get_metadata_fallback_client,
        get_discovery_cache_key=_get_discovery_cache_key,
        get_database=get_database,
        validate_discovery_cache_artist=_validate_discovery_cache_artist,
        search_spotify_for_tidal_track=_search_spotify_for_tidal_track,
        build_discovery_wing_it_stub=_build_discovery_wing_it_stub,
        add_activity_item=add_activity_item,
        sync_discovery_results_to_mirrored=_sync_discovery_results_to_mirrored,
    )


def _run_qobuz_discovery_worker(playlist_id):
    return _discovery_qobuz.run_qobuz_discovery_worker(playlist_id, _build_qobuz_discovery_deps())


def convert_qobuz_results_to_spotify_tracks(discovery_results):
    """Convert Qobuz discovery results to Spotify tracks format for sync."""
    return convert_results_to_spotify_tracks(discovery_results, "Qobuz")


# ===================================================================
# QOBUZ SYNC API ENDPOINTS
# ===================================================================

@bp.route('/api/qobuz/sync/start/<playlist_id>', methods=['POST'])
def start_qobuz_sync(playlist_id):
    """Start sync process for a Qobuz playlist using discovered Spotify tracks."""
    return _start_source_sync(
        qobuz_discovery_states, playlist_id, sync_id_prefix="qobuz",
        not_found_message="Qobuz playlist not found",
        not_ready_message="Qobuz playlist not ready for sync",
        convert_fn=convert_qobuz_results_to_spotify_tracks,
        name_getter=_pl_name_strict, image_getter=_pl_image_dict,
        activity_label="Qobuz", error_label="Qobuz")


@bp.route('/api/qobuz/sync/status/<playlist_id>', methods=['GET'])
def get_qobuz_sync_status(playlist_id):
    """Get sync status for a Qobuz playlist."""
    return _get_source_sync_status(qobuz_discovery_states, playlist_id, "Qobuz playlist not found", "Qobuz", "Qobuz playlist", _pl_name_strict)


@bp.route('/api/qobuz/sync/cancel/<playlist_id>', methods=['POST'])
def cancel_qobuz_sync(playlist_id):
    """Cancel sync for a Qobuz playlist."""
    return _cancel_source_sync(qobuz_discovery_states, playlist_id, "Qobuz", "Qobuz playlist not found")


# ===================================================================
# SPOTIFY PUBLIC PLAYLIST DISCOVERY API ENDPOINTS
# ===================================================================

# Global state for Spotify Public playlist management
spotify_public_discovery_states = {}  # Key: url_hash, Value: discovery state
spotify_public_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="spotify_public_discovery")

# Global state for iTunes/Apple Music link imports
itunes_link_discovery_states = {}  # Key: url_hash, Value: discovery state
itunes_link_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="itunes_link_discovery")
_apple_music_token_cache = {'token': None, 'fetched_at': 0}
_apple_music_token_lock = threading.Lock()
_APPLE_MUSIC_TOKEN_TTL = 6 * 60 * 60  # seconds
_APPLE_MUSIC_JWT_RE = re.compile(r'eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+')
_APPLE_MUSIC_BUNDLE_SCRAPE_CAP = 8

def _parse_itunes_link_url(url):
    """Return {'type': 'album'|'track'|'playlist', 'id': str} for supported Apple links."""
    import re

    raw = (url or '').strip()
    if not raw:
        return None

    uri_match = re.match(r'^(?:itunes|applemusic):(album|track|playlist):([A-Za-z0-9._-]+)$', raw, re.IGNORECASE)
    if uri_match:
        return {'type': uri_match.group(1).lower(), 'id': uri_match.group(2), 'country': 'us'}

    parsed = urlparse(raw)
    host = (parsed.netloc or '').lower()
    path = parsed.path or ''
    query = parse_qs(parsed.query or '')

    if 'itunes.apple.com' not in host and 'music.apple.com' not in host:
        return None

    # Apple Music track links are album URLs with ?i=<trackId>.
    track_id = (query.get('i') or [None])[0]
    if track_id and str(track_id).isdigit():
        return {'type': 'track', 'id': str(track_id), 'country': _apple_music_country_from_path(path)}

    song_match = re.search(r'/song(?:/[^/]+)?/(\d+)', path)
    if song_match:
        return {'type': 'track', 'id': song_match.group(1), 'country': _apple_music_country_from_path(path)}

    album_match = re.search(r'/album(?:/[^/]+)?/(\d+)', path)
    if album_match:
        return {'type': 'album', 'id': album_match.group(1), 'country': _apple_music_country_from_path(path)}

    playlist_match = re.search(r'/playlist(?:/[^/]+)?/(pl\.[A-Za-z0-9._-]+)', path)
    if playlist_match:
        return {'type': 'playlist', 'id': playlist_match.group(1), 'country': _apple_music_country_from_path(path)}

    return None


def _apple_music_country_from_path(path):
    import re
    match = re.match(r'^/([a-z]{2})(?:/|$)', path or '', re.IGNORECASE)
    return match.group(1).lower() if match else 'us'


def _itunes_album_image_url(album_data):
    images = album_data.get('images') or []
    if images and isinstance(images[0], dict):
        return images[0].get('url', '')
    return ''


def _itunes_track_to_link_track(track_data, fallback_album=None):
    artists = track_data.get('artists') or []
    if artists and isinstance(artists[0], dict):
        artists = [a.get('name', '') for a in artists if a.get('name')]
    elif isinstance(artists, str):
        artists = [artists]

    album = track_data.get('album') or fallback_album or {}
    if isinstance(album, str):
        album = {'name': album, 'images': []}

    return {
        'id': str(track_data.get('id') or track_data.get('trackId') or ''),
        'name': track_data.get('name') or track_data.get('trackName') or '',
        'artists': artists,
        'album': album,
        'duration_ms': track_data.get('duration_ms') or track_data.get('trackTimeMillis') or 0,
        'explicit': track_data.get('explicit') or track_data.get('trackExplicitness') == 'explicit',
        'track_number': track_data.get('track_number') or track_data.get('trackNumber') or 0,
        'disc_number': track_data.get('disc_number') or track_data.get('discNumber') or 1,
        'external_urls': track_data.get('external_urls') or {'itunes': track_data.get('trackViewUrl', '')},
        '_source': 'itunes'
    }


def _apple_music_artwork_images(artwork):
    if not isinstance(artwork, dict):
        return []
    template = artwork.get('url') or ''
    if not template:
        return []
    sizes = [600, 300, 100]
    images = []
    for size in sizes:
        url = template.replace('{w}', str(size)).replace('{h}', str(size))
        url = url.replace('{f}', 'jpg').replace('{c}', '')
        images.append({'url': url, 'height': size, 'width': size})
    return images


def _apple_music_song_to_link_track(song):
    attrs = (song or {}).get('attributes') or {}
    album_images = _apple_music_artwork_images(attrs.get('artwork'))
    album = {
        'id': attrs.get('albumId') or '',
        'name': attrs.get('albumName') or '',
        'images': album_images,
        'release_date': attrs.get('releaseDate') or '',
        'album_type': 'album',
    }
    return {
        'id': str((song or {}).get('id') or ''),
        'name': attrs.get('name') or '',
        'artists': [attrs.get('artistName') or 'Unknown Artist'],
        'album': album,
        'duration_ms': attrs.get('durationInMillis') or 0,
        'explicit': attrs.get('contentRating') == 'explicit',
        'track_number': attrs.get('trackNumber') or 0,
        'disc_number': attrs.get('discNumber') or 1,
        'external_urls': {'itunes': attrs.get('url') or ''},
        '_source': 'itunes'
    }


def _looks_like_apple_music_token(token):
    """Decode JWT payload and confirm Apple media-api claims before trusting."""
    import base64

    if not token or token.count('.') != 2:
        return False
    try:
        payload_b64 = token.split('.')[1]
        padding = '=' * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    except Exception:
        return False
    # Apple media-api tokens carry root_https_origin (distinctive) or are
    # Apple-issued JWTs with iss + iat + exp claims.
    if payload.get('root_https_origin'):
        return True
    if payload.get('iss') and payload.get('iat') and payload.get('exp'):
        return True
    return False


def _extract_apple_music_web_token(html_text, session=None):
    import html
    from urllib.parse import unquote, urljoin

    if not html_text:
        return None

    meta_match = re.search(
        r'<meta[^>]+name=["\']desktop-music-app/config/environment["\'][^>]+content=["\']([^"\']+)["\']',
        html_text,
        re.IGNORECASE,
    )
    if meta_match:
        try:
            raw = html.unescape(meta_match.group(1))
            data = json.loads(unquote(raw))
            token = ((data.get('MEDIA_API') or {}).get('token')
                     or (data.get('media-api') or {}).get('token'))
            if token and _looks_like_apple_music_token(token):
                return token
        except Exception as e:
            logger.debug(f"Apple Music token meta parse failed: {e}")

    inline_match = re.search(r'"token"\s*:\s*"(' + _APPLE_MUSIC_JWT_RE.pattern + r')"', html_text)
    if inline_match and _looks_like_apple_music_token(inline_match.group(1)):
        return inline_match.group(1)

    if session is None:
        return None

    script_srcs = re.findall(
        r'<script[^>]+src=["\']([^"\']+\.js)["\']',
        html_text,
        re.IGNORECASE,
    )
    prioritized = sorted(
        script_srcs,
        key=lambda s: 0 if re.search(r'(index|chunk|main|app)[^/]*\.js$', s, re.IGNORECASE) else 1,
    )
    attempted = 0
    for src in prioritized:
        if attempted >= _APPLE_MUSIC_BUNDLE_SCRAPE_CAP:
            break
        attempted += 1
        try:
            js_url = urljoin('https://music.apple.com/', src)
            js_resp = session.get(js_url, timeout=15)
            js_resp.raise_for_status()
            for match in _APPLE_MUSIC_JWT_RE.finditer(js_resp.text):
                candidate = match.group(0)
                if _looks_like_apple_music_token(candidate):
                    return candidate
        except Exception as e:
            logger.debug(f"Apple Music bundle scrape failed for {src}: {e}")
            continue
    return None


def _get_apple_music_web_token(session, page_text, force_refresh=False):
    with _apple_music_token_lock:
        if not force_refresh:
            cached = _apple_music_token_cache.get('token')
            fetched_at = _apple_music_token_cache.get('fetched_at', 0)
            if cached and (time.time() - fetched_at) < _APPLE_MUSIC_TOKEN_TTL:
                return cached
        token = _extract_apple_music_web_token(page_text, session=session)
        if token:
            _apple_music_token_cache['token'] = token
            _apple_music_token_cache['fetched_at'] = time.time()
        elif force_refresh:
            _apple_music_token_cache['token'] = None
            _apple_music_token_cache['fetched_at'] = 0
        return token


def _invalidate_apple_music_token():
    with _apple_music_token_lock:
        _apple_music_token_cache['token'] = None
        _apple_music_token_cache['fetched_at'] = 0


def _apple_music_amp_get(session, page_text_provider, web_headers, page_url, api_url, params):
    """GET amp-api with current cached token; on 401 invalidate + refetch token + retry once."""
    def build_headers(tok):
        return {
            'Accept': 'application/json',
            'Origin': 'https://music.apple.com',
            'Referer': page_url,
            'User-Agent': web_headers['User-Agent'],
            'Authorization': f"Bearer {tok}",
        }

    token = _get_apple_music_web_token(session, page_text_provider())
    if not token:
        raise ValueError("Could not read Apple Music web token")
    resp = session.get(api_url, headers=build_headers(token), params=params, timeout=20)
    if resp.status_code == 401:
        _invalidate_apple_music_token()
        page = session.get(page_url, headers=web_headers, timeout=20)
        page.raise_for_status()
        token = _get_apple_music_web_token(session, page.text, force_refresh=True)
        if not token:
            raise ValueError("Could not read Apple Music web token")
        resp = session.get(api_url, headers=build_headers(token), params=params, timeout=20)
    resp.raise_for_status()
    return resp


def _fetch_apple_music_playlist(url, playlist_id, country):
    from urllib.parse import urljoin

    session = requests.Session()
    web_headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    cached_page_text = {'value': None}

    def page_text_provider():
        if cached_page_text['value'] is None:
            page = session.get(url, headers=web_headers, timeout=20)
            page.raise_for_status()
            cached_page_text['value'] = page.text
        return cached_page_text['value']

    country = (country or 'us').lower()
    api_url = f"https://amp-api.music.apple.com/v1/catalog/{country}/playlists/{playlist_id}"
    playlist_resp = _apple_music_amp_get(session, page_text_provider, web_headers, url, api_url, {'l': 'en-US'})
    playlist_payload = playlist_resp.json()
    playlist_item = (playlist_payload.get('data') or [{}])[0]
    playlist_attrs = playlist_item.get('attributes') or {}

    tracks = []
    tracks_url = f"{api_url}/tracks"
    params = {'l': 'en-US'}
    while tracks_url:
        tracks_resp = _apple_music_amp_get(session, page_text_provider, web_headers, url, tracks_url, params)
        tracks_payload = tracks_resp.json()
        tracks.extend(_apple_music_song_to_link_track(song) for song in tracks_payload.get('data') or [])
        next_path = tracks_payload.get('next')
        tracks_url = urljoin('https://amp-api.music.apple.com', next_path) if next_path else None
        params = None

    images = _apple_music_artwork_images(playlist_attrs.get('artwork'))
    return {
        'id': playlist_id,
        'type': 'playlist',
        'name': playlist_attrs.get('name') or 'Apple Music Playlist',
        'subtitle': playlist_attrs.get('curatorName') or playlist_attrs.get('editorialNotes', {}).get('standard') or 'Apple Music',
        'url': url,
        'track_count': len(tracks),
        'image_url': images[0]['url'] if images else '',
        'tracks': tracks,
    }


def _build_itunes_link_state(response_data):
    return {
        'playlist': response_data,
        'phase': 'fresh',
        'status': 'fresh',
        'discovery_progress': 0,
        'spotify_matches': 0,
        'spotify_total': len(response_data.get('tracks') or []),
        'discovery_results': [],
        'sync_playlist_id': None,
        'converted_spotify_playlist_id': None,
        'download_process_id': None,
        'created_at': time.time(),
        'last_accessed': time.time(),
        'discovery_future': None,
        'sync_progress': {}
    }


def _convert_link_results_to_spotify_tracks(discovery_results, label):
    return convert_results_to_spotify_tracks(discovery_results, label)

@bp.route('/api/spotify/parse-public', methods=['POST'])
def parse_spotify_public_endpoint():
    """Parse a public Spotify playlist or album URL without API auth"""
    try:
        data = request.get_json()
        url = data.get('url', '').strip()

        if not url:
            return jsonify({"error": "Spotify URL is required"}), 400

        from core.spotify_public_scraper import parse_spotify_url, fetch_spotify_public

        parsed = parse_spotify_url(url)
        if not parsed:
            return jsonify({"error": "Invalid Spotify URL. Please use a playlist or album link from open.spotify.com"}), 400

        logger.info(f"Fetching public Spotify {parsed['type']}: {parsed['id']}")

        result = fetch_spotify_public(parsed['type'], parsed['id'])

        if 'error' in result:
            return jsonify(result), 400

        # Convert scraped tracks to Spotify-compatible format
        spotify_tracks = []
        for track in result['tracks']:
            spotify_tracks.append({
                'id': track['id'],
                'name': track['name'],
                'artists': track['artists'],
                'album': {
                    'name': result['name'] if result['type'] == 'album' else '',
                    'images': []
                },
                'duration_ms': track['duration_ms'],
                'explicit': track.get('is_explicit', False),
                'track_number': track.get('track_number', 0)
            })

        url_hash = result['url_hash']

        response_data = {
            'id': result['id'],
            'type': result['type'],
            'name': result['name'],
            'subtitle': result['subtitle'],
            'url': result['url'],
            'url_hash': url_hash,
            'track_count': len(spotify_tracks),
            'tracks': spotify_tracks
        }

        # Store playlist data in state for discovery (if not already there)
        if url_hash not in spotify_public_discovery_states:
            spotify_public_discovery_states[url_hash] = {
                'playlist': response_data,
                'phase': 'fresh',
                'status': 'fresh',
                'discovery_progress': 0,
                'spotify_matches': 0,
                'spotify_total': len(spotify_tracks),
                'discovery_results': [],
                'sync_playlist_id': None,
                'converted_spotify_playlist_id': None,
                'download_process_id': None,
                'created_at': time.time(),
                'last_accessed': time.time(),
                'discovery_future': None,
                'sync_progress': {}
            }
        else:
            # Update playlist data in existing state
            spotify_public_discovery_states[url_hash]['playlist'] = response_data
            spotify_public_discovery_states[url_hash]['last_accessed'] = time.time()

        logger.info(f"Spotify {parsed['type']} scraped: {result['name']} ({len(spotify_tracks)} tracks)")
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Error parsing Spotify URL: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@bp.route('/api/spotify-public/discovery/start/<url_hash>', methods=['POST'])
def start_spotify_public_discovery(url_hash):
    """Start Spotify discovery process for a Spotify Public playlist"""
    try:
        # Initialize discovery state if it doesn't exist, or update existing state
        if url_hash in spotify_public_discovery_states:
            existing_state = spotify_public_discovery_states[url_hash]
            if existing_state['phase'] == 'discovering':
                return jsonify({"error": "Discovery already in progress"}), 400

            if not existing_state.get('playlist'):
                return jsonify({"error": "Spotify Public playlist not found. Please parse the URL first."}), 404

            # Update existing state for discovery
            existing_state['phase'] = 'discovering'
            existing_state['status'] = 'discovering'
            existing_state['last_accessed'] = time.time()
            state = existing_state
        else:
            return jsonify({"error": "Spotify Public playlist not found. Please parse the URL first."}), 404

        # Add activity for discovery start
        playlist_name = state['playlist']['name']
        track_count = len(state['playlist']['tracks'])
        add_activity_item("", "Spotify Link Discovery Started", f"'{playlist_name}' - {track_count} tracks", "Now")

        # Start discovery worker
        future = spotify_public_discovery_executor.submit(_run_spotify_public_discovery_worker, url_hash)
        state['discovery_future'] = future

        logger.info(f"Started Spotify discovery for Spotify Public playlist: {playlist_name}")
        return jsonify({"success": True, "message": "Discovery started"})

    except Exception as e:
        logger.error(f"Error starting Spotify Public discovery: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/spotify-public/discovery/status/<url_hash>', methods=['GET'])
def get_spotify_public_discovery_status(url_hash):
    """Get real-time discovery status for a Spotify Public playlist"""
    return _get_source_discovery_status(spotify_public_discovery_states, url_hash, "Spotify Public discovery not found", "Spotify Public")

@bp.route('/api/spotify-public/discovery/update_match', methods=['POST'])
def update_spotify_public_discovery_match():
    """Update a Spotify Public discovery result with manually selected Spotify track"""
    return _update_source_discovery_match(spotify_public_discovery_states, "spotify_public", "Spotify Public", "spotify_public_track", _first_artist_plain)

@bp.route('/api/spotify-public/playlists/states', methods=['GET'])
def get_spotify_public_playlist_states():
    """Get all stored Spotify Public playlist discovery states for frontend hydration"""
    return _get_source_playlist_states(spotify_public_discovery_states, "Spotify Public", "Spotify Public")

@bp.route('/api/spotify-public/state/<url_hash>', methods=['GET'])
def get_spotify_public_playlist_state(url_hash):
    """Get specific Spotify Public playlist state (detailed version)"""
    try:
        if url_hash not in spotify_public_discovery_states:
            return jsonify({"error": "Spotify Public playlist not found"}), 404

        state = spotify_public_discovery_states[url_hash]
        state['last_accessed'] = time.time()

        response = {
            'playlist_id': url_hash,
            'playlist': state['playlist'],
            'phase': state['phase'],
            'status': state['status'],
            'discovery_progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'discovery_results': state['discovery_results'],
            'sync_playlist_id': state.get('sync_playlist_id'),
            'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
            'download_process_id': state.get('download_process_id'),
            'sync_progress': state.get('sync_progress', {}),
            'last_accessed': state['last_accessed']
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting Spotify Public playlist state: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/spotify-public/reset/<url_hash>', methods=['POST'])
def reset_spotify_public_playlist(url_hash):
    """Reset Spotify Public playlist to fresh phase (clear discovery/sync data)"""
    return _reset_source_playlist(spotify_public_discovery_states, url_hash, "Spotify Public", "Spotify Public playlist not found")

@bp.route('/api/spotify-public/delete/<url_hash>', methods=['POST'])
def delete_spotify_public_playlist(url_hash):
    """Delete Spotify Public playlist state completely"""
    return _delete_source_playlist(spotify_public_discovery_states, url_hash, "Spotify Public", "Spotify Public playlist not found")

@bp.route('/api/spotify-public/update_phase/<url_hash>', methods=['POST'])
def update_spotify_public_playlist_phase(url_hash):
    """Update Spotify Public playlist phase (used when modal closes to reset from download_complete to discovered)"""
    return _update_source_playlist_phase(spotify_public_discovery_states, url_hash, "Spotify Public playlist not found", "Spotify Public", _PHASE_LIST, True)


# Spotify Public discovery worker logic lives in core/discovery/spotify_public.py.
from core.discovery import spotify_public as _discovery_spotify_public


def _build_spotify_public_discovery_deps():
    """Build the SpotifyPublicDiscoveryDeps bundle from web_server.py globals on each call."""
    return _discovery_spotify_public.SpotifyPublicDiscoveryDeps(
        spotify_public_discovery_states=spotify_public_discovery_states,
        spotify_client=_spotify_client(),
        pause_enrichment_workers=_pause_enrichment_workers,
        resume_enrichment_workers=_resume_enrichment_workers,
        get_active_discovery_source=_get_active_discovery_source,
        get_metadata_fallback_client=_get_metadata_fallback_client,
        get_discovery_cache_key=_get_discovery_cache_key,
        get_database=get_database,
        validate_discovery_cache_artist=_validate_discovery_cache_artist,
        search_spotify_for_tidal_track=_search_spotify_for_tidal_track,
        build_discovery_wing_it_stub=_build_discovery_wing_it_stub,
        add_activity_item=add_activity_item,
    )


def _run_spotify_public_discovery_worker(url_hash):
    return _discovery_spotify_public.run_spotify_public_discovery_worker(
        url_hash, _build_spotify_public_discovery_deps()
    )



def convert_spotify_public_results_to_spotify_tracks(discovery_results):
    """Convert Spotify Public discovery results to Spotify tracks format for sync"""
    return convert_results_to_spotify_tracks(discovery_results, "Spotify Public")


# ===================================================================
# SPOTIFY PUBLIC SYNC API ENDPOINTS
# ===================================================================

@bp.route('/api/spotify-public/sync/start/<url_hash>', methods=['POST'])
def start_spotify_public_sync(url_hash):
    """Start sync process for a Spotify Public playlist using discovered Spotify tracks"""
    return _start_source_sync(
        spotify_public_discovery_states, url_hash, sync_id_prefix="spotify_public",
        not_found_message="Spotify Public playlist not found",
        not_ready_message="Spotify Public playlist not ready for sync",
        convert_fn=convert_spotify_public_results_to_spotify_tracks,
        name_getter=_pl_name_strict, image_getter=_pl_image_dict,
        activity_label="Spotify Link", error_label="Spotify Public")

@bp.route('/api/spotify-public/sync/status/<url_hash>', methods=['GET'])
def get_spotify_public_sync_status(url_hash):
    """Get sync status for a Spotify Public playlist"""
    return _get_source_sync_status(spotify_public_discovery_states, url_hash, "Spotify Public playlist not found", "Spotify Public", "Spotify Link playlist", _pl_name_strict)

@bp.route('/api/spotify-public/sync/cancel/<url_hash>', methods=['POST'])
def cancel_spotify_public_sync(url_hash):
    """Cancel sync for a Spotify Public playlist"""
    return _cancel_source_sync(spotify_public_discovery_states, url_hash, "Spotify Public", "Spotify Public playlist not found")


# ===================================================================
# ITUNES LINK DISCOVERY API ENDPOINTS
# ===================================================================

@bp.route('/api/itunes-link/parse', methods=['POST'])
def parse_itunes_link_endpoint():
    """Parse an iTunes/Apple Music album or track URL into a virtual playlist."""
    try:
        data = request.get_json()
        url = data.get('url', '').strip()

        if not url:
            return jsonify({"error": "iTunes URL is required"}), 400

        parsed = _parse_itunes_link_url(url)
        if not parsed:
            return jsonify({"error": "Invalid iTunes or Apple Music link. Album and track links are supported."}), 400

        client = _get_itunes_client()
        tracks = []
        image_url = ''
        subtitle = 'iTunes'
        name = ''

        if parsed['type'] == 'playlist':
            response_data = _fetch_apple_music_playlist(url, parsed['id'], parsed.get('country') or 'us')
            tracks = response_data.get('tracks') or []
            image_url = response_data.get('image_url', '')
            subtitle = response_data.get('subtitle', 'Apple Music')
            name = response_data.get('name', '')
        elif parsed['type'] == 'album':
            album = client.get_album(parsed['id'], include_tracks=True)
            if not album:
                return jsonify({"error": "iTunes album not found"}), 404
            album_tracks = ((album.get('tracks') or {}).get('items') or [])
            tracks = [_itunes_track_to_link_track(t, fallback_album={
                'id': album.get('id'),
                'name': album.get('name', ''),
                'images': album.get('images') or [],
                'release_date': album.get('release_date', ''),
                'album_type': album.get('album_type', 'album')
            }) for t in album_tracks]
            name = album.get('name', '')
            artists = album.get('artists') or []
            subtitle = ', '.join(a.get('name', '') for a in artists if isinstance(a, dict)) or 'iTunes'
            image_url = _itunes_album_image_url(album)
        else:
            track = client.get_track_details(parsed['id'])
            if not track:
                return jsonify({"error": "iTunes track not found"}), 404
            tracks = [_itunes_track_to_link_track(track)]
            name = track.get('name', '')
            subtitle = ', '.join(track.get('artists') or []) or 'iTunes'
            album = track.get('album') or {}
            if isinstance(album, dict):
                name = f"{track.get('name', '')} - {subtitle}".strip(' -')

        if not tracks:
            return jsonify({"error": "No tracks found for this iTunes link"}), 400

        import hashlib
        canonical = f"itunes:{parsed['type']}:{parsed['id']}"
        url_hash = hashlib.md5(canonical.encode()).hexdigest()[:12]

        if parsed['type'] == 'playlist':
            response_data['url_hash'] = url_hash
            response_data['track_count'] = len(tracks)
        else:
            response_data = {
                'id': parsed['id'],
                'type': parsed['type'],
                'name': name or f"iTunes {parsed['type'].title()}",
                'subtitle': subtitle,
                'url': url,
                'url_hash': url_hash,
                'track_count': len(tracks),
                'image_url': image_url,
                'tracks': tracks
            }

        if url_hash not in itunes_link_discovery_states:
            itunes_link_discovery_states[url_hash] = _build_itunes_link_state(response_data)
        else:
            itunes_link_discovery_states[url_hash]['playlist'] = response_data
            itunes_link_discovery_states[url_hash]['last_accessed'] = time.time()

        logger.info(f"iTunes {parsed['type']} parsed: {response_data['name']} ({len(tracks)} tracks)")
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Error parsing iTunes link: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/itunes-link/discovery/start/<url_hash>', methods=['POST'])
def start_itunes_link_discovery(url_hash):
    try:
        if url_hash not in itunes_link_discovery_states:
            return jsonify({"error": "iTunes link not found. Please load the URL first."}), 404

        state = itunes_link_discovery_states[url_hash]
        if state['phase'] == 'discovering':
            return jsonify({"error": "Discovery already in progress"}), 400

        if not state.get('playlist'):
            return jsonify({"error": "iTunes link data missing. Please load the URL again."}), 404

        state['phase'] = 'discovering'
        state['status'] = 'discovering'
        state['last_accessed'] = time.time()
        state['discovery_results'] = []
        state['discovery_progress'] = 0
        state['spotify_matches'] = 0

        playlist_name = state['playlist']['name']
        track_count = len(state['playlist']['tracks'])
        add_activity_item("", "iTunes Link Discovery Started", f"'{playlist_name}' - {track_count} tracks", "Now")

        future = itunes_link_discovery_executor.submit(_run_itunes_link_discovery_worker, url_hash)
        state['discovery_future'] = future

        logger.info(f"Started discovery for iTunes Link: {playlist_name}")
        return jsonify({"success": True, "message": "Discovery started"})

    except Exception as e:
        logger.error(f"Error starting iTunes Link discovery: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/itunes-link/discovery/status/<url_hash>', methods=['GET'])
def get_itunes_link_discovery_status(url_hash):
    return _get_source_discovery_status(itunes_link_discovery_states, url_hash, "iTunes Link discovery not found", "iTunes Link")


@bp.route('/api/itunes-link/discovery/update_match', methods=['POST'])
def update_itunes_link_discovery_match():
    try:
        data = request.get_json()
        identifier = data.get('identifier')
        track_index = data.get('track_index')
        spotify_track = data.get('spotify_track')

        if not identifier or track_index is None or not spotify_track:
            return jsonify({'error': 'Missing required fields'}), 400

        result, error = _update_itunes_link_discovery_result(identifier, track_index, spotify_track)
        if error:
            message, status = error
            return jsonify({'error': message}), status

        try:
            original_track = result.get('itunes_link_track', {})
            original_name = original_track.get('name', spotify_track['name'])
            original_artists = original_track.get('artists', [])
            original_artist = original_artists[0] if original_artists else ''
            cache_key = _get_discovery_cache_key(original_name, original_artist)
            cache_db = get_database()
            cache_db.save_discovery_cache_match(
                cache_key[0], cache_key[1], _get_active_discovery_source(), 1.0,
                result['spotify_data'], original_name, original_artist
            )
        except Exception as cache_err:
            logger.error(f"Error saving iTunes Link manual fix to discovery cache: {cache_err}")

        return jsonify({'success': True, 'result': result})

    except Exception as e:
        logger.error(f"Error updating iTunes Link discovery match: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/itunes-link/playlists/states', methods=['GET'])
def get_itunes_link_playlist_states():
    return _get_source_playlist_states(itunes_link_discovery_states, "iTunes Link")


@bp.route('/api/itunes-link/state/<url_hash>', methods=['GET'])
def get_itunes_link_playlist_state(url_hash):
    try:
        if url_hash not in itunes_link_discovery_states:
            return jsonify({"error": "iTunes Link not found"}), 404
        state = itunes_link_discovery_states[url_hash]
        state['last_accessed'] = time.time()
        return jsonify({
            'playlist_id': url_hash,
            'playlist': state['playlist'],
            'phase': state['phase'],
            'status': state['status'],
            'discovery_progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'discovery_results': state['discovery_results'],
            'sync_playlist_id': state.get('sync_playlist_id'),
            'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
            'download_process_id': state.get('download_process_id'),
            'sync_progress': state.get('sync_progress', {}),
            'last_accessed': state['last_accessed']
        })
    except Exception as e:
        logger.error(f"Error getting iTunes Link state: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/itunes-link/reset/<url_hash>', methods=['POST'])
def reset_itunes_link_playlist(url_hash):
    try:
        if url_hash not in itunes_link_discovery_states:
            return jsonify({"error": "iTunes Link not found"}), 404
        state = itunes_link_discovery_states[url_hash]
        if state.get('discovery_future'):
            state['discovery_future'].cancel()
        state.update({
            'phase': 'fresh',
            'status': 'fresh',
            'discovery_results': [],
            'discovery_progress': 0,
            'spotify_matches': 0,
            'sync_playlist_id': None,
            'converted_spotify_playlist_id': None,
            'download_process_id': None,
            'sync_progress': {},
            'discovery_future': None,
            'last_accessed': time.time()
        })
        return jsonify({"success": True, "message": "iTunes Link reset to fresh phase"})
    except Exception as e:
        logger.error(f"Error resetting iTunes Link: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/itunes-link/delete/<url_hash>', methods=['POST'])
def delete_itunes_link_playlist(url_hash):
    try:
        if url_hash not in itunes_link_discovery_states:
            return jsonify({"error": "iTunes Link not found"}), 404
        state = itunes_link_discovery_states[url_hash]
        if state.get('discovery_future'):
            state['discovery_future'].cancel()
        del itunes_link_discovery_states[url_hash]
        return jsonify({"success": True, "message": "iTunes Link deleted"})
    except Exception as e:
        logger.error(f"Error deleting iTunes Link: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/itunes-link/update_phase/<url_hash>', methods=['POST'])
def update_itunes_link_playlist_phase(url_hash):
    try:
        if url_hash not in itunes_link_discovery_states:
            return jsonify({"error": "iTunes Link not found"}), 404
        data = request.get_json()
        new_phase = data.get('phase') if data else None
        valid_phases = ['fresh', 'discovering', 'discovered', 'syncing', 'sync_complete', 'downloading', 'download_complete']
        if new_phase not in valid_phases:
            return jsonify({"error": f"Invalid phase. Must be one of: {', '.join(valid_phases)}"}), 400
        state = itunes_link_discovery_states[url_hash]
        old_phase = state.get('phase', 'unknown')
        state['phase'] = new_phase
        state['last_accessed'] = time.time()
        if 'download_process_id' in data:
            state['download_process_id'] = data['download_process_id']
        if 'converted_spotify_playlist_id' in data:
            state['converted_spotify_playlist_id'] = data['converted_spotify_playlist_id']
        return jsonify({"success": True, "old_phase": old_phase, "new_phase": new_phase})
    except Exception as e:
        logger.error(f"Error updating iTunes Link phase: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/itunes-link/sync/start/<url_hash>', methods=['POST'])
def start_itunes_link_sync(url_hash):
    try:
        if url_hash not in itunes_link_discovery_states:
            return jsonify({"error": "iTunes Link not found"}), 404
        state = itunes_link_discovery_states[url_hash]
        state['last_accessed'] = time.time()
        if state['phase'] not in ['discovered', 'sync_complete', 'download_complete']:
            return jsonify({"error": "iTunes Link not ready for sync"}), 400

        spotify_tracks = _convert_link_results_to_spotify_tracks(state['discovery_results'], 'iTunes Link')
        if not spotify_tracks:
            return jsonify({"error": "No Spotify matches found for sync"}), 400

        sync_playlist_id = f"itunes_link_{url_hash}"
        playlist_name = state['playlist']['name']
        add_activity_item("", "iTunes Link Sync Started", f"'{playlist_name}' - {len(spotify_tracks)} tracks", "Now")
        state['phase'] = 'syncing'
        state['sync_playlist_id'] = sync_playlist_id
        state['sync_progress'] = {}

        with sync_lock:
            sync_states[sync_playlist_id] = {
                "status": "starting",
                "playlist_name": playlist_name,
                "progress": {
                    "playlist_name": playlist_name,
                    "total_tracks": len(spotify_tracks),
                    "progress": 0,
                }
            }

        playlist_image_url = state['playlist'].get('image_url', '')
        future = sync_executor.submit(_run_sync_task, sync_playlist_id, playlist_name, spotify_tracks, None, get_current_profile_id(), playlist_image_url)
        active_sync_workers[sync_playlist_id] = future
        return jsonify({"success": True, "sync_playlist_id": sync_playlist_id})

    except Exception as e:
        logger.error(f"Error starting iTunes Link sync: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/itunes-link/sync/status/<url_hash>', methods=['GET'])
def get_itunes_link_sync_status(url_hash):
    return _get_source_sync_status(itunes_link_discovery_states, url_hash, "iTunes Link not found", "iTunes Link", "iTunes Link", _pl_name_strict)


@bp.route('/api/itunes-link/sync/cancel/<url_hash>', methods=['POST'])
def cancel_itunes_link_sync(url_hash):
    return _cancel_source_sync(itunes_link_discovery_states, url_hash, "iTunes Link", "iTunes Link not found")


@bp.route('/api/itunes-link/discovery/unmatch', methods=['POST'])
@bp.route('/api/beatport/discovery/unmatch', methods=['POST'])
@bp.route('/api/listenbrainz/discovery/unmatch', methods=['POST'])
def unmatch_discovery_track():
    """Remove a discovery match — sets track back to Not Found"""
    try:
        data = request.get_json()
        identifier = data.get('identifier')
        track_index = data.get('track_index')

        if not identifier or track_index is None:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        # Find the state dict for this discovery
        state = (youtube_playlist_states.get(identifier)
                 or tidal_discovery_states.get(identifier)
                 or deezer_discovery_states.get(identifier)
                 or spotify_public_discovery_states.get(identifier)
                 or itunes_link_discovery_states.get(identifier)
                 or beatport_chart_states.get(identifier)
                 or listenbrainz_playlist_states.get(identifier))

        if not state:
            return jsonify({'success': False, 'error': 'Discovery state not found'}), 404

        results = state.get('discovery_results', [])
        if track_index >= len(results):
            return jsonify({'success': False, 'error': 'Invalid track index'}), 400

        result = results[track_index]
        old_status = result.get('status_class')

        # Clear the match
        result['status'] = 'Not Found'
        result['status_class'] = 'not-found'
        result['spotify_track'] = ''
        result['spotify_artist'] = ''
        result['spotify_album'] = ''
        result['spotify_data'] = None
        result['matched_data'] = None
        result['match_data'] = None
        result['confidence'] = 0
        result['wing_it_fallback'] = False
        result['manual_match'] = False

        # Update match count
        if old_status in ('found', 'wing-it'):
            state['spotify_matches'] = max(0, state.get('spotify_matches', 0) - 1)
        if old_status == 'wing-it':
            state['wing_it_count'] = max(0, state.get('wing_it_count', 0) - 1)

        # If mirrored playlist, also clear in DB
        if identifier.startswith('mirrored_'):
            try:
                db = get_database()
                tracks = state.get('tracks', [])
                if track_index < len(tracks):
                    db_track_id = tracks[track_index].get('db_track_id')
                    if db_track_id:
                        db.update_mirrored_track_extra_data(db_track_id, {
                            'discovered': False,
                            'discovery_attempted': True,
                            'provider': '',
                            'unmatched_by_user': True,
                        })
            except Exception as e:
                logger.error(f"Error clearing mirrored track match: {e}")

        logger.info(f"Unmatched discovery track {track_index}: {result.get('yt_track', result.get('lb_track', ''))}")
        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Error unmatching discovery track: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _update_itunes_link_discovery_result(identifier, track_index, spotify_track):
    state = itunes_link_discovery_states.get(identifier)
    if not state:
        return None, ('Discovery state not found', 404)
    if track_index >= len(state['discovery_results']):
        return None, ('Invalid track index', 400)

    result = state['discovery_results'][track_index]
    old_status = result.get('status')
    result['status'] = 'Found'
    result['status_class'] = 'found'
    result['spotify_track'] = spotify_track['name']
    result['spotify_artist'] = _join_artist_names(spotify_track['artists']) if isinstance(spotify_track['artists'], list) else _extract_artist_name(spotify_track['artists'])
    result['spotify_album'] = spotify_track['album']
    result['spotify_id'] = spotify_track['id']
    result['duration'] = '0:00'
    duration_ms = spotify_track.get('duration_ms', 0)
    if duration_ms:
        result['duration'] = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}"
    result['spotify_data'] = _build_fix_modal_spotify_data(spotify_track)
    result['wing_it_fallback'] = False
    result['manual_match'] = True
    if old_status not in ('found', 'Found'):
        state['spotify_matches'] = state.get('spotify_matches', 0) + 1
    return result, None


def _build_itunes_link_discovery_deps():
    return _discovery_spotify_public.SpotifyPublicDiscoveryDeps(
        spotify_public_discovery_states=itunes_link_discovery_states,
        spotify_client=_spotify_client(),
        pause_enrichment_workers=_pause_enrichment_workers,
        resume_enrichment_workers=_resume_enrichment_workers,
        get_active_discovery_source=_get_active_discovery_source,
        get_metadata_fallback_client=_get_metadata_fallback_client,
        get_discovery_cache_key=_get_discovery_cache_key,
        get_database=get_database,
        validate_discovery_cache_artist=_validate_discovery_cache_artist,
        search_spotify_for_tidal_track=_search_spotify_for_tidal_track,
        build_discovery_wing_it_stub=_build_discovery_wing_it_stub,
        add_activity_item=add_activity_item,
        source_label="iTunes Link",
        activity_label="iTunes Link",
        original_track_key="itunes_link_track",
    )


def _run_itunes_link_discovery_worker(url_hash):
    return _discovery_spotify_public.run_spotify_public_discovery_worker(
        url_hash, _build_itunes_link_discovery_deps()
    )


# ===================================================================
# YOUTUBE PLAYLIST API ENDPOINTS
# ===================================================================

# Global state for YouTube playlist management (persistent across page reloads)
youtube_playlist_states = {}  # Key: url_hash, Value: persistent playlist state
youtube_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="youtube_discovery")

# Global state for Beatport chart management (persistent across page reloads)
beatport_chart_states = {}  # Key: url_hash, Value: persistent chart state
beatport_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="beatport_discovery")

# Global state for ListenBrainz playlist management (persistent across page reloads)
listenbrainz_playlist_states = {}  # Key: playlist_mbid, Value: persistent playlist state
listenbrainz_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="listenbrainz_discovery")

@bp.route('/api/youtube/parse', methods=['POST'])
def parse_youtube_playlist_endpoint():
    """Parse a YouTube playlist URL and return structured track data"""
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({"error": "YouTube URL is required"}), 400
        
        # Validate URL
        if not ('youtube.com/playlist' in url or 'music.youtube.com/playlist' in url):
            return jsonify({"error": "Invalid YouTube playlist URL"}), 400
        
        logger.info(f"Parsing YouTube playlist: {url}")
        
        # Parse the playlist using our function
        playlist_data = parse_youtube_playlist(url)
        
        if not playlist_data:
            return jsonify({"error": "Failed to parse YouTube playlist"}), 500
        
        # Use deterministic hash for state tracking (built-in hash() is randomized per process restart)
        import hashlib
        yt_playlist_id = playlist_data.get('id', '')
        if yt_playlist_id and yt_playlist_id != 'unknown_id':
            # Use canonical URL with the stable YouTube playlist ID
            canonical_url = f"https://youtube.com/playlist?list={yt_playlist_id}"
        else:
            canonical_url = url
        url_hash = hashlib.md5(canonical_url.encode()).hexdigest()[:12]

        # Migrate existing mirrored playlists that used the old non-deterministic hash()
        # and deduplicate any copies created by the bug
        try:
            database = get_database()
            profile_id = get_current_profile_id()
            existing = database.get_mirrored_playlists(profile_id=profile_id)
            yt_dupes = [mp for mp in existing if mp['source'] == 'youtube' and mp['name'] == playlist_data['name']]
            if yt_dupes:
                # Keep the newest one, delete the rest
                keep = yt_dupes[0]  # Already sorted by updated_at DESC from get_mirrored_playlists
                for dupe in yt_dupes[1:]:
                    database.delete_mirrored_playlist(dupe['id'])
                    logger.info(f"Removed duplicate YouTube mirrored playlist '{dupe['name']}' (id={dupe['id']})")
                # Update the kept entry's source_playlist_id to the new deterministic hash
                if keep['source_playlist_id'] != url_hash:
                    with database._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE mirrored_playlists SET source_playlist_id = ? WHERE id = ?",
                            (url_hash, keep['id'])
                        )
                        conn.commit()
                    logger.info(f"Migrated YouTube mirrored playlist '{keep['name']}' source_playlist_id to deterministic hash {url_hash}")
        except Exception as e:
            logger.debug(f"YouTube mirror migration check: {e}")
        
        # Initialize persistent playlist state (similar to Spotify download_batches structure)
        youtube_playlist_states[url_hash] = {
            'playlist': playlist_data,
            'phase': 'fresh',  # fresh -> discovering -> discovered -> syncing -> sync_complete -> downloading -> download_complete
            'discovery_results': [],
            'discovery_progress': 0,
            'spotify_matches': 0,
            'spotify_total': len(playlist_data['tracks']),
            'status': 'parsed',
            'url': url,
            'sync_playlist_id': None,
            'converted_spotify_playlist_id': None,
            'download_process_id': None,  # Track associated download missing tracks process
            'created_at': time.time(),
            'last_accessed': time.time(),
            'discovery_future': None,
            'sync_progress': {}
        }
        
        playlist_data['url_hash'] = url_hash
        
        logger.info(f"YouTube playlist parsed successfully: {playlist_data['name']} ({len(playlist_data['tracks'])} tracks)")
        return jsonify(playlist_data)
        
    except Exception as e:
        logger.error(f"Error parsing YouTube playlist: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/youtube/discovery/start/<url_hash>', methods=['POST'])
def start_youtube_discovery(url_hash):
    """Start Spotify discovery process for a YouTube playlist"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        state = youtube_playlist_states[url_hash]
        state['last_accessed'] = time.time()  # Update access time
        
        if state['phase'] == 'discovering':
            return jsonify({"error": "Discovery already in progress"}), 400
        
        # Update phase to discovering
        state['phase'] = 'discovering'
        state['status'] = 'discovering'
        state['discovery_progress'] = 0
        state['spotify_matches'] = 0
        state['discovery_results'] = []

        # Clear skip_discovery flags on all tracks (in case of prior retry)
        for track in state['playlist']['tracks']:
            track.pop('skip_discovery', None)

        # Add activity for discovery start
        playlist_name = state['playlist']['name']
        track_count = len(state['playlist']['tracks'])
        add_activity_item("", "YouTube Discovery Started", f"'{playlist_name}' - {track_count} tracks", "Now")
        
        # Start discovery worker
        future = youtube_discovery_executor.submit(_run_youtube_discovery_worker, url_hash)
        state['discovery_future'] = future
        
        logger.info(f"Started Spotify discovery for YouTube playlist: {state['playlist']['name']}")
        return jsonify({"success": True, "message": "Discovery started"})
        
    except Exception as e:
        logger.error(f"Error starting YouTube discovery: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/youtube/discovery/status/<url_hash>', methods=['GET'])
def get_youtube_discovery_status(url_hash):
    """Get real-time discovery status for a YouTube playlist"""
    return _get_source_discovery_status(youtube_playlist_states, url_hash, "YouTube playlist not found", "YouTube")


@bp.route('/api/youtube/discovery/unmatch', methods=['POST'])
@bp.route('/api/tidal/discovery/unmatch', methods=['POST'])
@bp.route('/api/deezer/discovery/unmatch', methods=['POST'])
@bp.route('/api/spotify-public/discovery/unmatch', methods=['POST'])
@bp.route('/api/youtube/discovery/update_match', methods=['POST'])
def update_youtube_discovery_match():
    """Update a YouTube discovery result with manually selected Spotify track"""
    try:
        data = request.get_json()
        identifier = data.get('identifier')  # url_hash
        track_index = data.get('track_index')
        spotify_track = data.get('spotify_track')

        if not identifier or track_index is None or not spotify_track:
            return jsonify({'error': 'Missing required fields'}), 400

        # Get the state
        state = youtube_playlist_states.get(identifier)

        if not state:
            return jsonify({'error': 'Discovery state not found'}), 404

        if track_index >= len(state['discovery_results']):
            return jsonify({'error': 'Invalid track index'}), 400

        # Update the result
        result = state['discovery_results'][track_index]
        old_status = result.get('status')

        # Update with user-selected track
        result['status'] = 'Found'
        result['status_class'] = 'found'
        result['spotify_track'] = spotify_track['name']
        result['spotify_artist'] = _join_artist_names(spotify_track['artists']) if isinstance(spotify_track['artists'], list) else _extract_artist_name(spotify_track['artists'])
        result['spotify_album'] = spotify_track['album']
        result['spotify_id'] = spotify_track['id']

        # Format duration
        duration_ms = spotify_track.get('duration_ms', 0)
        if duration_ms:
            minutes = duration_ms // 60000
            seconds = (duration_ms % 60000) // 1000
            result['duration'] = f"{minutes}:{seconds:02d}"
        else:
            result['duration'] = '0:00'

        # IMPORTANT: Also set spotify_data for sync/download compatibility.
        # Manual match from the fix modal — build a rich spotify_data (album
        # as dict with image info) matching the normal discovery shape, and
        # explicitly clear any prior wing-it flag since the user picked a
        # real metadata match.
        result['spotify_data'] = _build_fix_modal_spotify_data(spotify_track)
        result['wing_it_fallback'] = False

        result['manual_match'] = True  # Flag for tracking

        # Update match count if status changed from not found/error
        if old_status != 'found' and old_status != 'Found':
            state['spotify_matches'] = state.get('spotify_matches', 0) + 1

        logger.info(f"Manual match updated: youtube - {identifier} - track {track_index}")
        logger.info(f"   → {result['spotify_artist']} - {result['spotify_track']}")

        # See core.discovery.manual_match — Fix-popup matches can come from
        # any metadata source (primary first, then Spotify / Deezer / iTunes
        # / MusicBrainz as fallbacks). Hardcoding 'spotify' here used to
        # make every non-Spotify manual match look provider-drifted on the
        # next prepare-discovery, which triggered automatic re-discovery
        # that overwrote the user's pick. Computed once before the try
        # block so both the cache-save path AND the mirrored-DB save below
        # (in the except fallback case) see the same value.
        from core.discovery.manual_match import derive_manual_match_provider
        match_source = derive_manual_match_provider(
            spotify_track, _get_active_discovery_source()
        )
        matched_data = None

        # Save manual fix to discovery cache so it appears in discovery pool
        try:
            # Get original track name from the YouTube/source track data
            original_track = result.get('youtube_track', result.get('tidal_track', result.get('deezer_track', {})))
            original_name = original_track.get('name', spotify_track['name'])
            original_artists = original_track.get('artists', [])
            if original_artists:
                original_artist = original_artists[0] if isinstance(original_artists[0], str) else original_artists[0].get('name', '')
            else:
                original_artist = ''

            cache_key = _get_discovery_cache_key(original_name, original_artist)
            # Normalize artists to plain strings for cache consistency
            artists_list = spotify_track['artists']
            if isinstance(artists_list, list):
                artists_list = [a if isinstance(a, str) else a.get('name', '') for a in artists_list]
            # Preserve cover image info so the download pipeline can find
            # artwork when this cached match is used later. The fix modal
            # sends image_url at the top level; search results often return
            # album as a bare string, which previously dropped the artwork.
            image_url = spotify_track.get('image_url') or ''
            album_raw = spotify_track.get('album', '')
            if isinstance(album_raw, dict):
                album_obj = dict(album_raw)
                if image_url and not album_obj.get('image_url'):
                    album_obj['image_url'] = image_url
                if image_url and not album_obj.get('images'):
                    album_obj['images'] = [{'url': image_url}]
            else:
                album_obj = {'name': album_raw or ''}
                if image_url:
                    album_obj['image_url'] = image_url
                    album_obj['images'] = [{'url': image_url}]

            matched_data = {
                'id': spotify_track['id'],
                'name': spotify_track['name'],
                'artists': artists_list,
                'album': album_obj,
                'duration_ms': spotify_track.get('duration_ms', 0),
                'image_url': image_url,
                'source': match_source,
            }
            cache_db = get_database()
            cache_db.save_discovery_cache_match(
                cache_key[0], cache_key[1], _get_active_discovery_source(), 1.0, matched_data,
                original_name, original_artist
            )
            logger.info(f"Manual fix saved to discovery cache: {original_name} by {original_artist}")
        except Exception as cache_err:
            logger.error(f"Error saving manual fix to discovery cache: {cache_err}")

        # Persist manual fix to DB for mirrored playlists. Skips when the
        # cache-save block raised before matched_data was constructed —
        # without the payload there's nothing to persist, and re-deriving
        # it here would duplicate the construction logic above.
        if matched_data is not None and identifier.startswith('mirrored_'):
            try:
                tracks = state['playlist']['tracks']
                if track_index < len(tracks):
                    db_track_id = tracks[track_index].get('db_track_id')
                    if db_track_id:
                        db = get_database()
                        extra_data = {
                            'discovered': True,
                            'provider': match_source,
                            'confidence': 1.0,
                            'matched_data': matched_data,
                            'manual_match': True,
                            # extra_data is MERGED on save, so explicitly clear
                            # any stale stub/removal flags from before this fix —
                            # otherwise a leftover wing_it_fallback would make the
                            # pipeline re-discover and revert this manual pick.
                            'wing_it_fallback': False,
                            'unmatched_by_user': False,
                        }
                        db.update_mirrored_track_extra_data(db_track_id, extra_data)
                        result['matched_data'] = matched_data
                        logger.info(f"Persisted manual fix to DB for track {db_track_id}")
            except Exception as wb_err:
                logger.error(f"Error persisting manual fix to DB: {wb_err}")

        return jsonify({'success': True, 'result': result})

    except Exception as e:
        logger.error(f"Error updating YouTube discovery match: {e}")
        return jsonify({'error': str(e)}), 500


def _build_discovery_wing_it_stub(track_name, artist_name, duration_ms=0, image_url=''):
    """Build stub matched_data for tracks that failed metadata discovery.
    Used as automatic Wing It fallback so tracks still flow through the download pipeline.

    The id comes from core.discovery.wing_it so it is stable — the previous
    `hash(...) % 100000` was salted per interpreter, so a stub written to
    mirrored_playlist_tracks.extra_data resolved to a different id after a restart."""
    from core.discovery.wing_it import stub_track_id
    return {
        'id': stub_track_id(artist_name, track_name),
        'name': track_name,
        'artists': [{'name': artist_name}] if isinstance(artist_name, str) else artist_name,
        'album': {'name': '', 'album_type': 'single', 'images': [], 'release_date': ''},
        'duration_ms': duration_ms,
        'image_url': image_url,
        'source': 'wing_it_fallback',
    }


def _build_fix_modal_spotify_data(spotify_track):
    """Build a rich spotify_data dict from the fix-modal POST payload so manual
    matches carry the same shape as normal discovery results.

    Key points:
    - album is always a dict (normal discovery has it this way; legacy fix-modal
      produced a bare string which broke cover art lookup downstream)
    - image_url is carried both at top level and inside album.images for parity
      with Spotify API responses
    - handles both legacy string albums (most search endpoints return this) and
      newer object albums
    """
    if not isinstance(spotify_track, dict):
        spotify_track = {}

    image_url = spotify_track.get('image_url') or ''
    album_raw = spotify_track.get('album', '')

    if isinstance(album_raw, dict):
        album_obj = dict(album_raw)
        if image_url and not album_obj.get('image_url'):
            album_obj['image_url'] = image_url
        if image_url and not album_obj.get('images'):
            album_obj['images'] = [{'url': image_url}]
    else:
        album_obj = {'name': album_raw or ''}
        if image_url:
            album_obj['image_url'] = image_url
            album_obj['images'] = [{'url': image_url}]

    return {
        'id': spotify_track.get('id', ''),
        'name': spotify_track.get('name', ''),
        'artists': spotify_track.get('artists', []),
        'album': album_obj,
        'duration_ms': spotify_track.get('duration_ms', 0),
        'image_url': image_url,
    }


# YouTube discovery worker logic lives in core/discovery/youtube.py.
from core.discovery import youtube as _discovery_youtube


def _build_youtube_discovery_deps():
    """Build the YoutubeDiscoveryDeps bundle from web_server.py globals on each call."""
    return _discovery_youtube.YoutubeDiscoveryDeps(
        youtube_playlist_states=youtube_playlist_states,
        spotify_client=_spotify_client(),
        matching_engine=_matching_engine(),
        pause_enrichment_workers=_pause_enrichment_workers,
        resume_enrichment_workers=_resume_enrichment_workers,
        get_active_discovery_source=_get_active_discovery_source,
        get_metadata_fallback_client=_get_metadata_fallback_client,
        get_discovery_cache_key=_get_discovery_cache_key,
        validate_discovery_cache_artist=_validate_discovery_cache_artist,
        extract_artist_name=_extract_artist_name,
        spotify_rate_limited=_spotify_rate_limited,
        discovery_score_candidates=_discovery_score_candidates,
        get_metadata_cache=get_metadata_cache,
        build_discovery_wing_it_stub=_build_discovery_wing_it_stub,
        get_database=get_database,
        add_activity_item=add_activity_item,
        recover_youtube_artist=_recover_youtube_artist_cleaned,
    )


def _run_youtube_discovery_worker(url_hash):
    return _discovery_youtube.run_youtube_discovery_worker(url_hash, _build_youtube_discovery_deps())


# ListenBrainz discovery worker logic lives in core/discovery/listenbrainz.py.
from core.discovery import listenbrainz as _discovery_listenbrainz


def _build_listenbrainz_discovery_deps():
    """Build the ListenbrainzDiscoveryDeps bundle from web_server.py globals on each call."""
    return _discovery_listenbrainz.ListenbrainzDiscoveryDeps(
        listenbrainz_playlist_states=listenbrainz_playlist_states,
        spotify_client=_spotify_client(),
        matching_engine=_matching_engine(),
        pause_enrichment_workers=_pause_enrichment_workers,
        resume_enrichment_workers=_resume_enrichment_workers,
        get_active_discovery_source=_get_active_discovery_source,
        get_metadata_fallback_client=_get_metadata_fallback_client,
        get_discovery_cache_key=_get_discovery_cache_key,
        get_database=get_database,
        validate_discovery_cache_artist=_validate_discovery_cache_artist,
        extract_artist_name=_extract_artist_name,
        spotify_rate_limited=_spotify_rate_limited,
        discovery_score_candidates=_discovery_score_candidates,
        get_metadata_cache=get_metadata_cache,
        build_discovery_wing_it_stub=_build_discovery_wing_it_stub,
        add_activity_item=add_activity_item,
    )


def _run_listenbrainz_discovery_worker(state_key):
    return _discovery_listenbrainz.run_listenbrainz_discovery_worker(
        state_key, _build_listenbrainz_discovery_deps()
    )


def _calculate_similarity(str1, str2):
    """Calculate string similarity using simple character overlap"""
    if not str1 or not str2:
        return 0
    
    # Convert to lowercase and remove extra spaces
    str1 = str1.lower().strip()
    str2 = str2.lower().strip()
    
    if str1 == str2:
        return 1.0
    
    # Calculate character overlap
    set1 = set(str1.replace(' ', ''))
    set2 = set(str2.replace(' ', ''))
    
    if not set1 or not set2:
        return 0
    
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))
    
    return intersection / union if union > 0 else 0

@bp.route('/api/youtube/sync/start/<url_hash>', methods=['POST'])
def start_youtube_sync(url_hash):
    """Start sync process for a YouTube playlist using discovered Spotify tracks"""
    # mirrored playlists ride this endpoint too (mirrored_<id> keys). their
    # card-level "Sync now" runs the pipeline behind a global
    # is_pipeline_running() gate - the discovery modal's "Sync This Playlist"
    # must respect the same gate or it schedules a second sync over the
    # running pipeline (user report, aug 25).
    if str(url_hash).startswith('mirrored_'):
        try:
            if _get_automation_deps().state.is_pipeline_running():
                return jsonify({"error": "A playlist pipeline is already running"}), 409
        except Exception as _pg_exc:
            logger.debug(f"pipeline-running check unavailable: {_pg_exc}")
    return _start_source_sync(
        youtube_playlist_states, url_hash, sync_id_prefix="youtube",
        not_found_message="YouTube playlist not found",
        not_ready_message="YouTube playlist not ready for sync",
        convert_fn=convert_youtube_results_to_spotify_tracks,
        name_getter=_pl_name_strict, image_getter=_pl_image_dict,
        activity_label="YouTube", error_label="YouTube")

@bp.route('/api/youtube/sync/status/<url_hash>', methods=['GET'])
def get_youtube_sync_status(url_hash):
    """Get sync status for a YouTube playlist"""
    return _get_source_sync_status(youtube_playlist_states, url_hash, "YouTube playlist not found", "YouTube", "YouTube playlist", _pl_name_safe)

@bp.route('/api/youtube/sync/cancel/<url_hash>', methods=['POST'])
def cancel_youtube_sync(url_hash):
    """Cancel sync for a YouTube playlist"""
    return _cancel_source_sync(youtube_playlist_states, url_hash, "YouTube", "YouTube playlist not found")

# New YouTube Playlist Management Endpoints (for persistent state)

@bp.route('/api/youtube/playlists', methods=['GET'])
def get_all_youtube_playlists():
    """Get all stored YouTube playlists for frontend hydration (similar to Spotify playlists)"""
    try:
        playlists = []
        current_time = time.time()
        
        for url_hash, state in youtube_playlist_states.items():
            # Skip mirrored playlist entries — they have their own hydration
            if url_hash.startswith('mirrored_'):
                continue
            # Update access time when requested
            state['last_accessed'] = current_time

            # Return essential data for card recreation
            playlist_info = {
                'url_hash': url_hash,
                'url': state['url'],
                'playlist': state['playlist'],
                'phase': state['phase'],
                'status': state['status'],
                'discovery_progress': state['discovery_progress'],
                'spotify_matches': state['spotify_matches'],
                'spotify_total': state['spotify_total'],
                'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
                'download_process_id': state.get('download_process_id'),
                'created_at': state['created_at'],
                'last_accessed': state['last_accessed']
            }
            playlists.append(playlist_info)
        
        logger.info(f"Returning {len(playlists)} stored YouTube playlists for hydration")
        return jsonify({"playlists": playlists})
        
    except Exception as e:
        logger.error(f"Error getting YouTube playlists: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/youtube/state/<url_hash>', methods=['GET'])
def get_youtube_playlist_state(url_hash):
    """Get specific YouTube playlist state (detailed version of status endpoint)"""
    try:
        if url_hash not in youtube_playlist_states:
            return jsonify({"error": "YouTube playlist not found"}), 404
        
        state = youtube_playlist_states[url_hash]
        state['last_accessed'] = time.time()
        
        # Return full state information (including results for modal hydration)
        response = {
            'url_hash': url_hash,
            'url': state['url'],
            'playlist': state['playlist'],
            'phase': state['phase'],
            'status': state['status'],
            'discovery_progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'discovery_results': state['discovery_results'],
            'sync_playlist_id': state['sync_playlist_id'],
            'converted_spotify_playlist_id': state['converted_spotify_playlist_id'],
            'sync_progress': state['sync_progress'],
            'created_at': state['created_at'],
            'last_accessed': state['last_accessed']
        }
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error getting YouTube playlist state: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/youtube/reset/<url_hash>', methods=['POST'])
def reset_youtube_playlist(url_hash):
    """Reset YouTube playlist to fresh phase (clear discovery/sync data)"""
    try:
        if url_hash not in youtube_playlist_states:
            # Idempotent: live state gone (restart/eviction) — already "fresh".
            # 404 here permanently wedges a mirrored playlist whose state vanished
            # (#702); treat a reset of nothing as a success so the UI recovers.
            return jsonify({"success": True, "message": "Playlist already reset"})
        
        state = youtube_playlist_states[url_hash]
        
        # Stop any active discovery
        if 'discovery_future' in state and state['discovery_future']:
            state['discovery_future'].cancel()
        
        # Reset state to fresh (preserve original playlist data)
        state['phase'] = 'fresh'
        state['status'] = 'parsed'
        state['discovery_results'] = []
        state['discovery_progress'] = 0
        state['spotify_matches'] = 0
        state['sync_playlist_id'] = None
        state['converted_spotify_playlist_id'] = None
        state['sync_progress'] = {}
        state['discovery_future'] = None
        state['last_accessed'] = time.time()
        
        logger.info(f"Reset YouTube playlist to fresh phase: {state['playlist']['name']}")
        return jsonify({"success": True, "message": "Playlist reset to fresh state"})
        
    except Exception as e:
        logger.error(f"Error resetting YouTube playlist: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/youtube/delete/<url_hash>', methods=['DELETE'])
def delete_youtube_playlist(url_hash):
    """Remove YouTube playlist from backend storage entirely"""
    try:
        if url_hash not in youtube_playlist_states:
            # Idempotent: already gone (restart/eviction) — deleting nothing is a
            # success, not a 404 that wedges the UI (#702).
            return jsonify({"success": True, "message": "Playlist already removed"})
        
        state = youtube_playlist_states[url_hash]
        
        # Stop any active discovery
        if 'discovery_future' in state and state['discovery_future']:
            state['discovery_future'].cancel()
        
        # Remove from storage
        playlist_name = state['playlist']['name']
        del youtube_playlist_states[url_hash]
        
        logger.info(f"Deleted YouTube playlist from backend: {playlist_name}")
        return jsonify({"success": True, "message": f"Playlist '{playlist_name}' deleted"})
        
    except Exception as e:
        logger.error(f"Error deleting YouTube playlist: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/youtube/update_phase/<url_hash>', methods=['POST'])
def update_youtube_playlist_phase(url_hash):
    """Update YouTube playlist phase (used when modal closes to reset from download_complete to discovered)"""
    return _update_source_playlist_phase(youtube_playlist_states, url_hash, "YouTube playlist not found", "YouTube", _PHASE_LIST_YT, False)

def convert_youtube_results_to_spotify_tracks(discovery_results):
    """Convert YouTube discovery results to Spotify tracks format for sync"""
    return convert_results_to_spotify_tracks(discovery_results, "YouTube")


# ===================================================================
# YOUTUBE MUSIC (ACCOUNT) API ENDPOINTS
# ===================================================================
#
# Distinct from the /api/youtube/* family above: that one parses a PASTED
# playlist URL. This one lists the SIGNED-IN account's own library playlists
# (core.ytmusic_library) and fetches individual playlists by id
# (core.youtube_music_meta.fetch_ytmusic_playlist)

# Global state for the YouTube Music management (persistent across page reloads)
ytmusic_discovery_states = {}  # Key: YT Music playlist id ('LM' for Liked Music), Value: persistent playlist state
ytmusic_discovery_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ytmusic_discovery")


@bp.route('/api/ytmusic/playlists', methods=['GET'])
def get_ytmusic_playlists():
    """List the signed-in YouTube Music account's own library playlists, Liked Music first."""
    from core.ytmusic_library import fetch_library_playlists, fetch_liked_music_row, library_playlists_to_rows
    auth = _ytmusic_auth_headers()
    if not auth:
        return jsonify({"error": "YouTube Music not authenticated. Settings → Downloads → Download Source: YouTube Only → Paste cookies.txt → Save."}), 401
    try:
        rows = library_playlists_to_rows(fetch_library_playlists(auth))
        liked_row = fetch_liked_music_row(auth)
        if liked_row:
            rows.insert(0, liked_row)

        playlist_data = [{
            "id": row["id"],
            "name": row["name"],
            "owner": row.get("owner") or "Unknown",
            "track_count": row.get("track_count", 0),
            "image_url": row.get("image_url"),
            "description": row.get("description") or "",
            "tracks": [],
        } for row in rows]

        logger.info(f"Loaded {len(playlist_data)} YouTube Music playlists")
        return jsonify(playlist_data)
    except Exception as e:
        logger.error(f"Error getting YouTube Music playlists: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/ytmusic/playlist/<playlist_id>', methods=['GET'])
def get_ytmusic_playlist_tracks(playlist_id):
    """Fetch full track details for a specific YouTube Music playlist."""
    from core.youtube_music_meta import fetch_ytmusic_playlist
    from core.ytmusic_library import ytmusic_playlist_url
    auth = _ytmusic_auth_headers()
    if not auth:
        return jsonify({"error": "YouTube Music not authenticated."}), 401
    try:
        data = fetch_ytmusic_playlist(ytmusic_playlist_url(playlist_id), auth)
        if not data:
            return jsonify({"error": "Playlist not found or unable to access. This may be due to privacy settings or account restrictions."}), 404
        if not data.get('tracks'):
            return jsonify({"error": "This playlist appears to have no tracks or they cannot be accessed"}), 403

        playlist_dict = {
            'id': data.get('id', playlist_id),
            'name': data.get('name', 'YouTube Music Playlist'),
            'description': '',
            'owner': 'You',
            'track_count': data.get('track_count', len(data['tracks'])),
            'image_url': data.get('image_url'),
            'tracks': data['tracks'],
        }
        logger.info(f"Loaded {len(data['tracks'])} tracks from YouTube Music playlist: {playlist_dict['name']}")
        return jsonify(playlist_dict)
    except Exception as e:
        logger.error(f"Error getting YouTube Music playlist tracks: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/ytmusic/discovery/start/<playlist_id>', methods=['POST'])
def start_ytmusic_discovery(playlist_id):
    """Start Spotify discovery process for a YouTube Music playlist"""
    from core.youtube_music_meta import fetch_ytmusic_playlist
    from core.ytmusic_library import ytmusic_playlist_url
    try:
        auth = _ytmusic_auth_headers()
        if not auth:
            return jsonify({"error": "YouTube Music not authenticated."}), 401

        # Fetch this single playlist fresh — no need to re-fetch the whole library.
        target_playlist = fetch_ytmusic_playlist(ytmusic_playlist_url(playlist_id), auth)

        if not target_playlist:
            return jsonify({"error": "YouTube Music playlist not found"}), 404

        if not target_playlist.get('tracks'):
            return jsonify({"error": "Playlist has no tracks"}), 400

        if playlist_id in ytmusic_discovery_states:
            state = ytmusic_discovery_states[playlist_id]
            if state['phase'] == 'discovering':
                return jsonify({"error": "Discovery already in progress"}), 400
            state['playlist'] = target_playlist
            state['phase'] = 'discovering'
            state['status'] = 'discovering'
            state['discovery_progress'] = 0
            state['spotify_matches'] = 0
            state['spotify_total'] = len(target_playlist['tracks'])
            state['discovery_results'] = []
            state['last_accessed'] = time.time()
        else:
            state = {
                'playlist': target_playlist,
                'phase': 'discovering',  # discovering -> discovered -> syncing -> sync_complete -> downloading -> download_complete
                'status': 'discovering',
                'discovery_progress': 0,
                'spotify_matches': 0,
                'spotify_total': len(target_playlist['tracks']),
                'discovery_results': [],
                'sync_playlist_id': None,
                'converted_spotify_playlist_id': None,
                'download_process_id': None,
                'created_at': time.time(),
                'last_accessed': time.time(),
                'discovery_future': None,
                'sync_progress': {}
            }
            ytmusic_discovery_states[playlist_id] = state

        add_activity_item("", "YouTube Music Discovery Started", f"'{target_playlist['name']}' - {len(target_playlist['tracks'])} tracks", "Now")

        future = ytmusic_discovery_executor.submit(_run_ytmusic_discovery_worker, playlist_id)
        state['discovery_future'] = future

        logger.info(f"Started Spotify discovery for YouTube Music playlist: {target_playlist['name']}")
        return jsonify({"success": True, "message": "Discovery started"})

    except Exception as e:
        logger.error(f"Error starting YouTube Music discovery: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/ytmusic/discovery/status/<playlist_id>', methods=['GET'])
def get_ytmusic_discovery_status(playlist_id):
    """Get real-time discovery status for a YouTube Music playlist"""
    return _get_source_discovery_status(ytmusic_discovery_states, playlist_id, "YouTube Music discovery not found", "YouTube Music")


@bp.route('/api/ytmusic/discovery/update_match', methods=['POST'])
def update_ytmusic_discovery_match():
    """Update a YouTube Music discovery result with manually selected Spotify track."""
    try:
        data = request.get_json()
        identifier = data.get('identifier')  # playlist_id (bare or mirrored_<id>)
        track_index = data.get('track_index')
        spotify_track = data.get('spotify_track')

        if not identifier or track_index is None or not spotify_track:
            return jsonify({'error': 'Missing required fields'}), 400

        state = ytmusic_discovery_states.get(identifier)

        if not state:
            return jsonify({'error': 'Discovery state not found'}), 404

        if track_index >= len(state['discovery_results']):
            return jsonify({'error': 'Invalid track index'}), 400

        result = state['discovery_results'][track_index]
        old_status = result.get('status')

        result['status'] = 'Found'
        result['status_class'] = 'found'
        result['spotify_track'] = spotify_track['name']
        result['spotify_artist'] = _join_artist_names(spotify_track['artists']) if isinstance(spotify_track['artists'], list) else _extract_artist_name(spotify_track['artists'])
        result['spotify_album'] = spotify_track['album']
        result['spotify_id'] = spotify_track['id']

        duration_ms = spotify_track.get('duration_ms', 0)
        if duration_ms:
            minutes = duration_ms // 60000
            seconds = (duration_ms % 60000) // 1000
            result['duration'] = f"{minutes}:{seconds:02d}"
        else:
            result['duration'] = '0:00'

        result['spotify_data'] = _build_fix_modal_spotify_data(spotify_track)
        result['wing_it_fallback'] = False
        result['manual_match'] = True

        if old_status != 'found' and old_status != 'Found':
            state['spotify_matches'] = state.get('spotify_matches', 0) + 1

        logger.info(f"Manual match updated: ytmusic - {identifier} - track {track_index}")
        logger.info(f"   → {result['spotify_artist']} - {result['spotify_track']}")

        from core.discovery.manual_match import derive_manual_match_provider
        match_source = derive_manual_match_provider(
            spotify_track, _get_active_discovery_source()
        )
        matched_data = None

        try:
            original_track = result.get('youtube_track', {})
            original_name = original_track.get('name', spotify_track['name'])
            original_artists = original_track.get('artists', [])
            if original_artists:
                original_artist = original_artists[0] if isinstance(original_artists[0], str) else original_artists[0].get('name', '')
            else:
                original_artist = ''

            cache_key = _get_discovery_cache_key(original_name, original_artist)
            artists_list = spotify_track['artists']
            if isinstance(artists_list, list):
                artists_list = [a if isinstance(a, str) else a.get('name', '') for a in artists_list]
            image_url = spotify_track.get('image_url') or ''
            album_raw = spotify_track.get('album', '')
            if isinstance(album_raw, dict):
                album_obj = dict(album_raw)
                if image_url and not album_obj.get('image_url'):
                    album_obj['image_url'] = image_url
                if image_url and not album_obj.get('images'):
                    album_obj['images'] = [{'url': image_url}]
            else:
                album_obj = {'name': album_raw or ''}
                if image_url:
                    album_obj['image_url'] = image_url
                    album_obj['images'] = [{'url': image_url}]

            matched_data = {
                'id': spotify_track['id'],
                'name': spotify_track['name'],
                'artists': artists_list,
                'album': album_obj,
                'duration_ms': spotify_track.get('duration_ms', 0),
                'image_url': image_url,
                'source': match_source,
            }
            cache_db = get_database()
            cache_db.save_discovery_cache_match(
                cache_key[0], cache_key[1], _get_active_discovery_source(), 1.0, matched_data,
                original_name, original_artist
            )
            logger.info(f"Manual fix saved to discovery cache: {original_name} by {original_artist}")
        except Exception as cache_err:
            logger.error(f"Error saving manual fix to discovery cache: {cache_err}")

        if matched_data is not None and identifier.startswith('mirrored_'):
            try:
                tracks = state['playlist']['tracks']
                if track_index < len(tracks):
                    db_track_id = tracks[track_index].get('db_track_id')
                    if db_track_id:
                        db = get_database()
                        extra_data = {
                            'discovered': True,
                            'provider': match_source,
                            'confidence': 1.0,
                            'matched_data': matched_data,
                            'manual_match': True,
                            'wing_it_fallback': False,
                            'unmatched_by_user': False,
                        }
                        db.update_mirrored_track_extra_data(db_track_id, extra_data)
                        result['matched_data'] = matched_data
                        logger.info(f"Persisted manual fix to DB for track {db_track_id}")
            except Exception as wb_err:
                logger.error(f"Error persisting manual fix to DB: {wb_err}")

        return jsonify({'success': True, 'result': result})

    except Exception as e:
        logger.error(f"Error updating YouTube Music discovery match: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/ytmusic/playlists/states', methods=['GET'])
def get_ytmusic_playlist_states():
    """Get all stored YouTube Music discovery states for frontend hydration"""
    return _get_source_playlist_states(ytmusic_discovery_states, "YouTube Music", "YouTube Music")


@bp.route('/api/ytmusic/state/<playlist_id>', methods=['GET'])
def get_ytmusic_playlist_state(playlist_id):
    """Get specific YouTube Music playlist state (detailed version of the status endpoint)"""
    try:
        if playlist_id not in ytmusic_discovery_states:
            return jsonify({"error": "YouTube Music playlist not found"}), 404

        state = ytmusic_discovery_states[playlist_id]
        state['last_accessed'] = time.time()

        response = {
            'playlist_id': playlist_id,
            'playlist': state['playlist'],
            'phase': state['phase'],
            'status': state['status'],
            'discovery_progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'discovery_results': state['discovery_results'],
            'sync_playlist_id': state.get('sync_playlist_id'),
            'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
            'download_process_id': state.get('download_process_id'),
            'sync_progress': state.get('sync_progress', {}),
            'created_at': state['created_at'],
            'last_accessed': state['last_accessed']
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting YouTube Music playlist state: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/ytmusic/reset/<playlist_id>', methods=['POST'])
def reset_ytmusic_playlist(playlist_id):
    """Reset YouTube Music playlist to fresh phase (clear discovery/sync data)"""
    return _reset_source_playlist(ytmusic_discovery_states, playlist_id, "YouTube Music", "YouTube Music playlist not found")


@bp.route('/api/ytmusic/delete/<playlist_id>', methods=['POST'])
def delete_ytmusic_playlist(playlist_id):
    """Delete YouTube Music playlist state completely"""
    return _delete_source_playlist(ytmusic_discovery_states, playlist_id, "YouTube Music", "YouTube Music playlist not found")


@bp.route('/api/ytmusic/update_phase/<playlist_id>', methods=['POST'])
def update_ytmusic_playlist_phase(playlist_id):
    """Update YouTube Music playlist phase (used when modal closes to reset from download_complete to discovered)"""
    return _update_source_playlist_phase(ytmusic_discovery_states, playlist_id, "YouTube Music playlist not found", "YouTube Music", _PHASE_LIST, False)


def _build_ytmusic_discovery_deps():
    """Build the YoutubeDiscoveryDeps bundle pointed at ytmusic_discovery_states."""
    return _discovery_youtube.YoutubeDiscoveryDeps(
        youtube_playlist_states=ytmusic_discovery_states,
        spotify_client=_spotify_client(),
        matching_engine=_matching_engine(),
        pause_enrichment_workers=_pause_enrichment_workers,
        resume_enrichment_workers=_resume_enrichment_workers,
        get_active_discovery_source=_get_active_discovery_source,
        get_metadata_fallback_client=_get_metadata_fallback_client,
        get_discovery_cache_key=_get_discovery_cache_key,
        validate_discovery_cache_artist=_validate_discovery_cache_artist,
        extract_artist_name=_extract_artist_name,
        spotify_rate_limited=_spotify_rate_limited,
        discovery_score_candidates=_discovery_score_candidates,
        get_metadata_cache=get_metadata_cache,
        build_discovery_wing_it_stub=_build_discovery_wing_it_stub,
        get_database=get_database,
        add_activity_item=add_activity_item,
        # No per-video artist recovery here: fetch_ytmusic_playlist already
        # gets a real artist from the catalog API, unlike yt-dlp's flat
        # extraction (which is what recover_youtube_artist works around).
        recover_youtube_artist=None,
    )


def _run_ytmusic_discovery_worker(playlist_id):
    return _discovery_youtube.run_youtube_discovery_worker(playlist_id, _build_ytmusic_discovery_deps())


def convert_ytmusic_results_to_spotify_tracks(discovery_results):
    """Convert YouTube Music discovery results to Spotify tracks format for sync"""
    return convert_results_to_spotify_tracks(discovery_results, "YouTube Music")


@bp.route('/api/ytmusic/sync/start/<playlist_id>', methods=['POST'])
def start_ytmusic_sync(playlist_id):
    """Start sync process for a YouTube Music playlist using discovered Spotify tracks"""
    # Unlike start_youtube_sync, mirrored playlists never reach this endpoint:
    # the frontend's 'mirrored' vertical routes every mirrored sync through
    # /api/youtube/* regardless of the original source, and
    # api/mirrored_playlists.py only ever registers into
    # youtube_playlist_states — so no mirrored_<id> pipeline-collision guard
    # is needed here.
    return _start_source_sync(
        ytmusic_discovery_states, playlist_id, sync_id_prefix="ytmusic",
        not_found_message="YouTube Music playlist not found",
        not_ready_message="YouTube Music playlist not ready for sync",
        convert_fn=convert_ytmusic_results_to_spotify_tracks,
        name_getter=_pl_name_strict, image_getter=_pl_image_dict,
        activity_label="YouTube Music", error_label="YouTube Music")


@bp.route('/api/ytmusic/sync/status/<playlist_id>', methods=['GET'])
def get_ytmusic_sync_status(playlist_id):
    """Get sync status for a YouTube Music playlist"""
    return _get_source_sync_status(ytmusic_discovery_states, playlist_id, "YouTube Music playlist not found", "YouTube Music", "YouTube Music playlist", _pl_name_safe)


@bp.route('/api/ytmusic/sync/cancel/<playlist_id>', methods=['POST'])
def cancel_ytmusic_sync(playlist_id):
    """Cancel sync for a YouTube Music playlist"""
    return _cancel_source_sync(ytmusic_discovery_states, playlist_id, "YouTube Music", "YouTube Music playlist not found")


# Add these new endpoints to the end of web_server.py

# Sync background worker logic lives in core/discovery/sync.py.
from core.discovery import sync as _discovery_sync


def _build_sync_deps():
    """Build the SyncDeps bundle from web_server.py globals on each call."""
    return _discovery_sync.SyncDeps(
        config_manager=config_manager,
        sync_service=sync_service,
        media_server_engine=media_server_engine,
        automation_engine=automation_engine,
        run_async=run_async,
        record_sync_history_start=_record_sync_history_start,
        update_automation_progress=_update_automation_progress,
        update_and_save_sync_status=_update_and_save_sync_status,
        sync_states=sync_states,
        sync_lock=sync_lock,
        process_wishlist_automatically=_process_wishlist_automatically,
        run_playlist_organize_download=_run_playlist_organize_download,
        is_wishlist_actually_processing=is_wishlist_actually_processing,
    )


def _run_sync_task(
    playlist_id,
    playlist_name,
    tracks_json,
    automation_id=None,
    profile_id=1,
    playlist_image_url='',
    sync_mode=None,
    skip_wishlist_add=False,
):
    # When a caller doesn't specify a mode — the mirrored auto-sync + Playlist
    # Pipeline (auto_sync_playlist), iTunes-link sync, Wing It — honor the user's
    # configured global "Playlist sync mode" instead of hardcoding 'replace'.
    # Hardcoding replace meant every AUTOMATED sync recreated the server
    # playlist, wiping its custom image + description even when the user chose
    # Append/Reconcile (#823 carlosjfcasero). The global default is still
    # 'replace', so default users are unaffected; only users who set
    # Append/Reconcile get the change. (Mirrors _submit_sync_task.)
    if sync_mode is None:
        from core.sync.playlist_edit import normalize_sync_mode
        sync_mode = normalize_sync_mode(None, config_manager.get('playlist_sync.mode', 'replace'))
    tracks_json, _quality_profile_id = _tracks_with_mirrored_quality_profile(
        playlist_id,
        playlist_name,
        tracks_json,
        profile_id=profile_id,
    )
    return _discovery_sync.run_sync_task(
        playlist_id, playlist_name, tracks_json, automation_id, profile_id, playlist_image_url,
        _build_sync_deps(),
        sync_mode=sync_mode,
        skip_wishlist_add=skip_wishlist_add,
    )


def _run_playlist_organize_download(mirrored_playlist_id, automation_id=None, profile_id=None):
    """Start a playlist-folder missing-tracks batch for automation / pipeline."""
    from core.playlists.organize_download import run_playlist_organize_download

    if profile_id is None:
        profile_id = get_current_profile_id()
    return run_playlist_organize_download(
        _get_automation_deps(),
        mirrored_playlist_id=int(mirrored_playlist_id),
        profile_id=profile_id,
        get_batch_max_concurrent=_get_batch_max_concurrent,
        run_full_missing_tracks_process=_run_full_missing_tracks_process,
        record_sync_history_start=_record_sync_history_start,
        detect_sync_source=_downloads_history.detect_sync_source,
    )



@bp.route('/api/sync/start', methods=['POST'])
def start_playlist_sync():
    """Starts a new sync process for a given playlist."""
    request_start_time = time.time()
    logger.info(f"⏱️ [TIMING] Sync request received at {time.strftime('%H:%M:%S')}")
    
    data = request.get_json()
    playlist_id = data.get('playlist_id')
    playlist_name = data.get('playlist_name')
    tracks_json = data.get('tracks') # Pass the full track list
    playlist_image_url = data.get('image_url', '')
    # 'replace' (default) deletes the server playlist and recreates it from
    # the source. 'append' preserves user-added tracks already on the server
    # playlist — only adds tracks that aren't there yet. Per-server clients
    # implement append via native add APIs (Plex addItems, Jellyfin POST
    # /Playlists/<id>/Items, Navidrome updatePlaylist?songIdToAdd=...).
    # Per-request sync_mode wins; otherwise use the configured default
    # (Settings > Playlist sync mode). Default 'replace' keeps today's behavior.
    from core.sync.playlist_edit import normalize_sync_mode
    sync_mode = normalize_sync_mode(data.get('sync_mode'),
                                    config_manager.get('playlist_sync.mode', 'replace'))

    if not all([playlist_id, playlist_name, tracks_json]):
        return jsonify({"success": False, "error": "Missing playlist_id, name, or tracks."}), 400

    # Add activity for sync start
    add_activity_item("", "Spotify Sync Started", f"'{playlist_name}' - {len(tracks_json)} tracks ({sync_mode})", "Now")

    logger.info(f"Starting playlist sync for '{playlist_name}' with {len(tracks_json)} tracks (mode: {sync_mode})")
    logger.debug(f"Request parsed at {time.strftime('%H:%M:%S')} (took {(time.time()-request_start_time)*1000:.1f}ms)")

    with sync_lock:
        if playlist_id in active_sync_workers and not active_sync_workers[playlist_id].done():
            return jsonify({"success": False, "error": "Sync is already in progress for this playlist."}), 409

        # Initial state
        sync_states[playlist_id] = {
            "status": "starting",
            "playlist_name": playlist_name,
            "progress": {
                "playlist_name": playlist_name,
                "total_tracks": len(tracks_json),
                "progress": 0,
            }
        }

        # Submit the task to the thread pool (capture profile_id while still in request context)
        _sync_profile_id = get_current_profile_id()
        thread_submit_time = time.time()
        future = sync_executor.submit(_run_sync_task, playlist_id, playlist_name, tracks_json, None, _sync_profile_id, playlist_image_url, sync_mode)
        active_sync_workers[playlist_id] = future
        thread_submit_duration = (time.time() - thread_submit_time) * 1000
        logger.info(f"⏱️ [TIMING] Thread submitted at {time.strftime('%H:%M:%S')} (took {thread_submit_duration:.1f}ms)")

    total_request_time = (time.time() - request_start_time) * 1000
    logger.info(f"⏱️ [TIMING] Request completed at {time.strftime('%H:%M:%S')} (total: {total_request_time:.1f}ms)")
    return jsonify({"success": True, "message": "Sync started."})


@bp.route('/api/sync/status/<playlist_id>', methods=['GET'])
def get_sync_status(playlist_id):
    """Polls for the status of an ongoing sync."""
    with sync_lock:
        state = sync_states.get(playlist_id)
        if not state:
            return jsonify({"status": "not_found"}), 404

        # If the task is finished but the state hasn't been updated, check the future
        if state['status'] not in ['finished', 'error'] and playlist_id in active_sync_workers:
            if active_sync_workers[playlist_id].done():
                # The task might have finished between polls, trigger final state update
                # This is handled by the _run_sync_task itself
                pass

        return jsonify(state)


@bp.route('/api/sync/cancel', methods=['POST'])
def cancel_playlist_sync():
    """Cancels an ongoing sync process."""
    data = request.get_json()
    playlist_id = data.get('playlist_id')

    if not playlist_id:
        return jsonify({"success": False, "error": "Missing playlist_id."}), 400

    with sync_lock:
        future = active_sync_workers.get(playlist_id)
        if not future or future.done():
            return jsonify({"success": False, "error": "Sync not running or already complete."}), 404

        # The GUI's sync_service has a cancel_sync method. We'll replicate that idea.
        # Since we can't easily stop the thread, we'll set a flag.
        # The elegant solution is to have the sync_service check for a cancellation flag.
        # Your `sync_service.py` already has this logic with `self._cancelled`.
        sync_service.cancel_sync()

        # We can't guarantee immediate stop, but we can update the state
        sync_states[playlist_id] = {"status": "cancelled"}

        # It's best practice to let the task finish and clean itself up.
        # We don't use future.cancel() as it may not work if the task is already running.

    return jsonify({"success": True, "message": "Sync cancellation requested."})

@bp.route('/api/sync/test-database', methods=['GET'])
def test_database_access():
    """Test endpoint to verify database connectivity for sync operations"""
    try:
        logger.debug("Testing database access for sync operations...")
        
        # Test database initialization
        from database.music_database import MusicDatabase
        db = MusicDatabase()
        logger.debug(f"   Database initialized: {db is not None}")
        
        # Test basic database query
        stats = db.get_database_info_for_server()
        logger.debug(f"   Database stats retrieved: {stats}")
        
        # Test track existence check (like sync service does)
        db_track, confidence = db.check_track_exists("test track", "test artist", confidence_threshold=0.7)
        logger.info(f"   Track existence check works: found={db_track is not None}, confidence={confidence}")
        
        # Test config manager
        from core.settings import config_manager
        active_server = config_manager.get_active_media_server()
        logger.info(f"   Active media server: {active_server}")
        
        # Test media clients 
        logger.info("   Media clients status:")
        logger.info(f"     media_server_engine.client('plex'): {media_server_engine.client('plex') is not None}")
        if media_server_engine.client('plex'):
            logger.info(f"     media_server_engine.client('plex').is_connected(): {media_server_engine.client('plex').is_connected()}")
        logger.info(f"     media_server_engine.client('jellyfin'): {media_server_engine.client('jellyfin') is not None}")
        if media_server_engine.client('jellyfin'):
            logger.info(f"     media_server_engine.client('jellyfin').is_connected(): {media_server_engine.client('jellyfin').is_connected()}")
        
        return jsonify({
            "success": True, 
            "message": "Database access test successful",
            "details": {
                "database_initialized": db is not None,
                "database_stats": stats,
                "active_server": active_server,
                "plex_connected": media_server_engine.client('plex').is_connected() if media_server_engine.client('plex') else False,
                "jellyfin_connected": media_server_engine.client('jellyfin').is_connected() if media_server_engine.client('jellyfin') else False,
            }
        })
        
    except Exception as e:
        logger.error(f"   Database test failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False, 
            "error": str(e),
            "message": "Database access test failed"
        }), 500

