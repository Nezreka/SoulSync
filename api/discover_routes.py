"""Discover endpoints - lifted from web_server.py.

the discover page's backend mass: shelves, daily mixes, stations,
resolve-playable, genre explorer + deep-dive, seasonal, release radar,
discovery weekly, hidden gems, curated storage reads, the recs family,
and the personalized manager builder. the image-cache/image-proxy and
music blocklist families ride along - they sat inside the band and are
thin and self-contained.

bodies byte-identical; only the decorator changed and the rebindable
clients/workers became getters.
"""

import functools
import hashlib
import json
import os
import random
import threading
import time
from datetime import datetime, timedelta

import re
import types

import requests
from flask import Blueprint, Response, jsonify, make_response, request, send_file

from api.source_playlists import (
    _get_deezer_client,
    _get_metadata_fallback_client,
    _get_metadata_fallback_source,
    _save_source_bubble_snapshot,
)
from core.discovery.hero import get_discover_hero as _discover_hero_get
from core.library.service_search import _search_service
from core.metadata import normalize_image_url as fix_artist_image_url
from core.metadata.cache import get_metadata_cache
from core.profile_context import admin_only, get_current_profile_id
from core.runtime_state import download_batches, tasks_lock
from core.spotify_client import _is_globally_rate_limited as _spotify_rate_limited
from utils.logging_config import get_logger

logger = get_logger("web_server")

# ── the shelf cache: applied at import time, so it lives HERE, not injected ──
# Cache the discover shelf payloads per (path+query, profile) for 30 minutes;
# the warmer recomputes in the background so users only hit warm answers.
_DISCOVER_SHELF_CACHE = {}
_DISCOVER_SHELF_TTL_S = 1800


def _discover_shelf_cache(key_extra=None):
    def deco(fn):

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            import time as _t
            extra = key_extra() if key_extra else None
            key = (fn.__name__, request.full_path, get_current_profile_id(), extra)
            hit = _DISCOVER_SHELF_CACHE.get(key)
            now = _t.time()
            if hit and now < hit[0]:
                body, status, ctype = hit[1]
                return Response(body, status=status, content_type=ctype)
            out = fn(*args, **kwargs)
            resp = make_response(out)
            if resp.status_code == 200:
                _DISCOVER_SHELF_CACHE[key] = (
                    now + _DISCOVER_SHELF_TTL_S,
                    (resp.get_data(), resp.status_code, resp.content_type),
                )
            return resp
        return wrapper
    return deco


def _discover_dial_key():
    # The dial re-ranks similar-artists live; committing it must bust the key.
    try:
        from core.settings import config_manager
        return str(config_manager.get('discover.adventurousness', 0.3))
    except Exception:
        return '0.3'


# injected by configure()
get_database = None
config_manager = None
download_orchestrator = None
_get_active_discovery_source = None


def _catalogue_name_key(name):
    """The catalogue's folded artist key. SQLite's LOWER() is ASCII-only, so a
    stored "Björk" never answered a searched "björk" (iss29-D13)."""
    from core.library2.importer import normalize_name

    return normalize_name(str(name or ""))

_spotify_client = None
_tidal_client = None
_hydrabase_client = None
_hydrabase_worker = None
_lastfm_worker = None
_dev_mode_enabled = None
_is_hydrabase_active = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"discover_routes.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('discover_routes', __name__)


def create_blueprint():
    return bp

@bp.route('/api/discover/stations', methods=['GET'])
def get_recommended_stations():
    """Recommended Stations - the user's heaviest recent artists as one-click
    artist radio (startArtistRadioById plays the library's own tracks)."""
    try:
        from core.discovery.stations import build_stations
        stations = build_stations(get_database(), get_current_profile_id())
        return jsonify({"success": True, "stations": stations})
    except Exception as e:
        logger.error(f"[Discover] stations failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/resolve-playable', methods=['POST'])
def resolve_playable_endpoint():
    """Match a mix's artist/title list against owned tracks so the player can
    play what the user already has (window.playTrackList wants file_path
    rows). The missing remainder stays with the download button."""
    try:
        from core.discovery.playable import resolve_playable_tracks
        payload = request.get_json(silent=True) or {}
        wanted = payload.get('tracks')
        if not isinstance(wanted, list):
            return jsonify({"success": False, "error": "tracks list required"}), 400
        result = resolve_playable_tracks(get_database(), wanted)
        result["success"] = "error" not in result
        return jsonify(result)
    except Exception as e:
        logger.error(f"[Discover] resolve-playable failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover_downloads/snapshot', methods=['POST'])
def save_discover_download_snapshot():
    """
    Saves a snapshot of current discover download state for persistence across page refreshes.
    """
    return _save_source_bubble_snapshot("downloads", "No download data provided", "discover_downloads", "downloads", "discover download snapshot", "downloads")

@bp.route('/api/discover_downloads/hydrate', methods=['GET'])
def hydrate_discover_downloads():
    """
    Loads discover downloads with live status by cross-referencing snapshots with active processes.
    """
    try:
        from datetime import datetime, timedelta

        db = get_database()
        snapshot = db.get_bubble_snapshot('discover_downloads', profile_id=get_current_profile_id())

        # Load snapshot if it exists
        if not snapshot:
            return jsonify({
                'success': True,
                'downloads': {},
                'message': 'No snapshots found'
            })

        saved_downloads = snapshot['data']
        snapshot_time = snapshot['timestamp']

        # Clean up old snapshots (older than 48 hours)
        try:
            if snapshot_time:
                snapshot_dt = datetime.fromisoformat(snapshot_time.replace('Z', '+00:00'))
                cutoff = datetime.now() - timedelta(hours=48)
                if snapshot_dt < cutoff:
                    logger.info(f"Cleaning up old discover download snapshot from {snapshot_time}")
                    db.delete_bubble_snapshot('discover_downloads', profile_id=get_current_profile_id())
                    return jsonify({
                        'success': True,
                        'downloads': {},
                        'message': 'Old snapshot cleaned up'
                    })
        except ValueError as e:
            logger.error(f"Error checking discover snapshot age: {e}")

        # Get current active download processes for live status
        current_processes = {}
        try:
            with tasks_lock:
                for batch_id, batch_data in download_batches.items():
                    if batch_data.get('phase') not in ['complete', 'error', 'cancelled']:
                        playlist_id = batch_data.get('playlist_id')
                        if playlist_id:
                            current_processes[playlist_id] = {
                                'status': 'in_progress' if batch_data.get('phase') == 'downloading' else 'analyzing',
                                'batch_id': batch_id,
                                'phase': batch_data.get('phase')
                            }
        except Exception as e:
            logger.error(f"Error fetching active processes for discover download hydration: {e}")

        # If no active processes exist, the app likely restarted - clean up snapshots
        if not current_processes:
            logger.warning("No active processes found - app likely restarted, cleaning up discover download snapshot")
            db.delete_bubble_snapshot('discover_downloads', profile_id=get_current_profile_id())
            return jsonify({
                'success': True,
                'downloads': {},
                'message': 'No active processes - returning empty downloads'
            })

        # Update download statuses with live data
        hydrated_downloads = {}
        for playlist_id, download_data in saved_downloads.items():
            # Determine current live status
            if playlist_id in current_processes:
                process_info = current_processes[playlist_id]
                live_status = 'in_progress'
                logger.info(f"Found active process for discover download {playlist_id}: {process_info['phase']}")
            else:
                # No active process - likely completed
                live_status = 'completed'
                logger.warning(f"No active process for discover download {playlist_id} - marking as completed")

            # Create updated download entry
            hydrated_downloads[playlist_id] = {
                'name': download_data.get('name'),
                'type': download_data.get('type'),
                'status': live_status,
                'virtualPlaylistId': playlist_id,
                'imageUrl': download_data.get('imageUrl'),
                'startTime': download_data.get('startTime', datetime.now().isoformat())
            }

        download_count = len(hydrated_downloads)
        active_count = sum(1 for d in hydrated_downloads.values() if d['status'] == 'in_progress')
        completed_count = sum(1 for d in hydrated_downloads.values() if d['status'] == 'completed')

        logger.info(f"Hydrated {download_count} discover downloads: {active_count} active, {completed_count} completed")

        return jsonify({
            'success': True,
            'downloads': hydrated_downloads,
            'stats': {
                'total_downloads': download_count,
                'active_downloads': active_count,
                'completed_downloads': completed_count
            }
        })

    except Exception as e:
        logger.error(f"Error hydrating discover downloads: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@bp.route('/api/discover/hero', methods=['GET'])
def get_discover_hero():
    return _discover_hero_get()


_discover_taste_cache = {}            # profile_id -> (expiry_ts, {genre: 0..1})
# Genre/novelty weights now come from adventurousness_weights(dial) (core.discovery) — the dial blends
# them, single source of truth — instead of the old fixed _DISCOVER_GENRE_WEIGHT / _NOVELTY_PENALTY.


def _discover_genre_taste(database, profile_id):
    """Cached genre-taste profile for a profile — ``{genre_lower: 0..1}`` from the genres of your
    top-played artists, weighted by plays. Cached 5 min (it changes slowly) so the live dial-drag
    re-fetch doesn't recompute it. Fail-soft -> ``{}`` (no genre data -> no boost, never a penalty)."""
    now = time.time()
    cached = _discover_taste_cache.get(profile_id)
    if cached and now < cached[0]:
        return cached[1]
    profile = {}
    try:
        from core.discovery.listening_recommendations import build_genre_taste_profile
        top = database.get_top_artists('all', 300) or []
        genres_by_name = database.get_artist_genres_by_name([t.get('name') for t in top if t.get('name')])
        weighted = [(genres_by_name.get((t.get('name') or '').strip().lower(), []),
                     t.get('play_count', 1) or 1) for t in top]
        profile = build_genre_taste_profile(weighted)
    except Exception as e:
        logger.debug(f"discover genre taste profile failed: {e}")
    _discover_taste_cache[profile_id] = (now + 300, profile)
    return profile


def _discover_primary_genre(item):
    """First genre of a discover candidate for diversity grouping — its ``genres`` may be a JSON
    string, an already-parsed list, or missing. Returns a normalized genre string or None."""
    g = item.get('genres')
    if isinstance(g, str):
        try:
            g = json.loads(g)
        except (ValueError, TypeError):
            g = None
    if isinstance(g, list) and g and isinstance(g[0], str) and g[0].strip():
        return g[0].strip().lower()
    return None


@bp.route('/api/discover/similar-artists', methods=['GET'])
@_discover_shelf_cache(key_extra=_discover_dial_key)
def get_discover_similar_artists():
    """Get all recommended similar artists (basic data, no enrichment for speed)"""
    try:
        database = get_database()
        active_source = _get_active_discovery_source()
        from core.settings import config_manager
        active_server = config_manager.get_active_media_server()
        try:
            _adv_level = float(config_manager.get('discover.adventurousness', 0.3) or 0)
        except (TypeError, ValueError):
            _adv_level = 0.0

        # The dial drives candidate SELECTION here (not just the re-rank below): the pool shifts from
        # consensus picks toward obscure long-tail deep cuts as you turn it up.
        similar_artists = database.get_top_similar_artists(
            limit=200,
            profile_id=get_current_profile_id(),
            require_source=active_source,
            exclude_library_server=active_server,
            adventurousness=_adv_level,
        )

        if not similar_artists:
            return jsonify({"success": True, "artists": [], "source": active_source, "count": 0})

        # Explainability: resolve which of the user's OWN artists point to each
        # recommendation, so the UI can show "because you have X, Y, Z".
        try:
            sources_by_name = database.get_recommendation_sources(
                [a.similar_artist_name for a in similar_artists],
                profile_id=get_current_profile_id(),
            )
        except Exception as e:
            logger.debug("recommendation-sources lookup failed: %s", e)
            sources_by_name = {}

        # Artists already filtered by source in SQL
        result_artists = []
        for artist in similar_artists:

            if active_source == 'spotify':
                artist_id = artist.similar_artist_spotify_id
            elif active_source == 'deezer':
                artist_id = getattr(artist, 'similar_artist_deezer_id', None) or artist.similar_artist_itunes_id
            elif active_source == 'musicbrainz':
                artist_id = getattr(artist, 'similar_artist_musicbrainz_id', None) or artist.similar_artist_itunes_id
            else:
                artist_id = artist.similar_artist_itunes_id

            artist_data = {
                "artist_id": artist_id,
                "spotify_artist_id": artist.similar_artist_spotify_id,
                "itunes_artist_id": artist.similar_artist_itunes_id,
                "musicbrainz_artist_id": getattr(artist, 'similar_artist_musicbrainz_id', None),
                "artist_name": artist.similar_artist_name,
                "occurrence_count": artist.occurrence_count,
                "similarity_rank": artist.similarity_rank,
                "source": active_source,
            }
            # Include cached metadata if available
            if artist.image_url:
                artist_data["image_url"] = artist.image_url
            if artist.genres:
                artist_data["genres"] = artist.genres[:3]
            if artist.popularity:
                artist_data["popularity"] = artist.popularity
            # "because you have X, Y, Z" — the artists of yours that point here
            because = sources_by_name.get(artist.similar_artist_name)
            if because:
                artist_data["because"] = because
            result_artists.append(artist_data)

        # Re-rank: genre/tag affinity (always-on) + the adventurousness popularity penalty (dial).
        # Score from the SQL signals (occurrence primary, similarity a minor tiebreak), boosted by how
        # well the candidate's genres match your taste, then popularity-penalised by the dial. We only
        # re-rank when there's a reason (genre data OR dial > 0) — with neither, the fetch order is
        # left untouched (no regression). Fail-soft. (_adv_level was read above for the fetch.)
        _pid = get_current_profile_id()
        _taste = _discover_genre_taste(database, _pid)
        _plays = database.get_play_counts_by_name(
            [a.get('artist_name') for a in result_artists], _pid) if result_artists else {}
        if result_artists and (_adv_level > 0 or _taste or _plays):
            try:
                from core.discovery.listening_recommendations import (
                    apply_adventurous_blend, genre_affinity, novelty_score)
                for a in result_artists:
                    _oc = float(a.get('occurrence_count') or 0)
                    _rank = min(float(a.get('similarity_rank') or 10), 10.0)
                    a['_base'] = _oc + (10.0 - _rank) * 0.1              # consensus base
                    _aff = genre_affinity(a.get('genres') or [], _taste) if _taste else 0.0
                    a['_aff'] = a['_why_genre'] = _aff                    # _why_genre feeds the "why" chips
                    a['_nov'] = novelty_score(_plays.get((a.get('artist_name') or '').strip().lower(), 0))
                # Blend the sort axis consensus<->obscurity by the dial (scaled by genre/novelty
                # quality). dial 0 = most-recommended first; dial 1 = least-popular (deep cuts) first.
                result_artists = apply_adventurous_blend(
                    result_artists, _adv_level, base_key='_base', pop_key='popularity',
                    tiebreak_key='occurrence_count')
                for a in result_artists:
                    a.pop('_base', None); a.pop('_aff', None); a.pop('_nov', None)
            except Exception as _adv_err:
                logger.debug(f"similar-artists re-rank skipped: {_adv_err}")

        # "Why this rec" chips — built unconditionally so they show even when the re-rank block above
        # was skipped (no genre data, no plays, dial 0). Deep-cut/consensus tags don't need taste.
        try:
            from core.discovery.listening_recommendations import why_chips
            for a in result_artists:
                _w = why_chips(genre_affinity=a.get('_why_genre', 0.0), popularity=a.get('popularity'),
                               seed_count=len(a.get('because') or []) or int(a.get('occurrence_count') or 0),
                               level=_adv_level)   # adaptive: "Off your usual path" on the adventurous end
                if _w:
                    a['why'] = _w
                a.pop('_why_genre', None)
        except Exception as _why_err:
            logger.debug(f"similar-artists why chips skipped: {_why_err}")

        # Diversity: spread the shown picks across genres so one genre can't hog the row (broader
        # discovery). No-ops on small lists. Then mark the shown set featured so the next load
        # rotates in different deep cuts (freshness).
        try:
            from core.discovery.listening_recommendations import diversify_by_genre
            result_artists = diversify_by_genre(result_artists, _discover_primary_genre, cap=3)
            _shown = [a.get('artist_name') for a in result_artists[:18] if a.get('artist_name')]
            if _shown:
                database.mark_artists_featured(_shown)
        except Exception as _div_err:
            logger.debug(f"similar-artists diversify/rotate skipped: {_div_err}")

        logger.info(
            f"[Similar Artists] {len(similar_artists)} from DB, {len(result_artists)} valid for "
            f"{active_source} after excluding {active_server} library artists"
        )

        return jsonify({
            "success": True,
            "artists": result_artists,
            "source": active_source,
            "count": len(result_artists)
        })

    except Exception as e:
        logger.error(f"Error getting similar artists: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/adventurousness', methods=['GET', 'POST'])
def discover_adventurousness():
    """Get/set the global Discover adventurousness dial (0..1). Shares the config key
    ``discover.adventurousness`` with the Settings -> Discovery slider, so the two controls stay in
    sync (change one, the other reflects it on next load). Read-only-safe; clamps to [0, 1]."""
    try:
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            try:
                v = float(data.get('value'))
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "value must be a number"}), 400
            v = max(0.0, min(1.0, v))
            config_manager.set('discover.adventurousness', v)
            return jsonify({"success": True, "value": v})
        try:
            v = float(config_manager.get('discover.adventurousness', 0.3) or 0)
        except (TypeError, ValueError):
            v = 0.3
        return jsonify({"success": True, "value": max(0.0, min(1.0, v))})
    except Exception as e:
        logger.error(f"adventurousness endpoint error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _resolve_popularity_sources():
    """Best-effort handles for the popularity cascade — each may be None (then it's skipped)."""
    spotify_free = lastfm = deezer = None
    try:
        from core.spotify_free_metadata import SpotifyFreeMetadataClient, spotify_free_installed
        if spotify_free_installed():
            spotify_free = SpotifyFreeMetadataClient()
    except Exception as e:
        logger.debug(f"spotify-free unavailable for backfill: {e}")
    try:
        from core.lastfm_client import LastFMClient
        _key = config_manager.get('lastfm.api_key', '')
        if _key:
            lastfm = LastFMClient(api_key=_key)
    except Exception as e:
        logger.debug(f"lastfm unavailable for backfill: {e}")
    try:
        deezer = _get_deezer_client()
    except Exception as e:
        logger.debug(f"deezer unavailable for backfill: {e}")
    return spotify_free, lastfm, deezer


@bp.route('/api/discover/popularity-backfill/start', methods=['POST'])
@admin_only
def start_popularity_backfill():
    """Kick off the background popularity backfill (fills similar_artists.popularity via the Spotify
    Free -> Last.fm -> Deezer cascade). Rate-limited + resumable; safe to call repeatedly."""
    try:
        from core.discovery import popularity_backfill as pb
        if pb.is_running():
            return jsonify({"success": False, "error": "Backfill already running", "state": pb.get_state()})
        spotify_free, lastfm, deezer = _resolve_popularity_sources()
        if not any([spotify_free, lastfm, deezer]):
            return jsonify({"success": False,
                            "error": "No popularity source available — configure Last.fm, Spotify Free, or Deezer."})
        pb.start_background(get_database(), spotify_free=spotify_free, lastfm=lastfm, deezer=deezer,
                            profile_id=get_current_profile_id())
        return jsonify({"success": True, "state": pb.get_state()})
    except Exception as e:
        logger.error(f"start popularity backfill error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/popularity-backfill/status', methods=['GET'])
def popularity_backfill_status():
    from core.discovery import popularity_backfill as pb
    return jsonify({"success": True, "state": pb.get_state()})


@bp.route('/api/discover/popularity-backfill/cancel', methods=['POST'])
@admin_only
def cancel_popularity_backfill():
    from core.discovery import popularity_backfill as pb
    pb.cancel()
    return jsonify({"success": True, "state": pb.get_state()})


def _autostart_popularity_backfill():
    """Self-maintaining popularity fill — no button, no restart, no cost to scans.

    Sweeps ~90s after boot, then re-checks hourly so new similar-artist data (added by watchlist scans)
    tops up on its own. It's deliberately DECOUPLED from the scan worker — the worker stays fast and
    never makes popularity lookups mid-scan; this loop fills the gaps afterwards, rate-limited inside
    the sweep. Each tick: if there's nothing missing, or no source configured, it just sleeps again."""
    import time as _t
    _t.sleep(90)  # let the server finish its own startup work first
    while True:
        try:
            from core.discovery import popularity_backfill as pb
            if not pb.is_running():
                database = get_database()
                from core.library2.migration_gate import migration_required
                if migration_required(database):
                    _t.sleep(30)
                    continue
                missing = database.count_similar_artists_missing_popularity(1)
                if missing > 0:
                    spotify_free, lastfm, deezer = _resolve_popularity_sources()
                    if any([spotify_free, lastfm, deezer]):
                        logger.info("Popularity backfill: filling %d artist(s) in the background", missing)
                        # run synchronously — this thread IS the background worker
                        pb.run_backfill(database, spotify_free=spotify_free, lastfm=lastfm,
                                        deezer=deezer, profile_id=1)
                    else:
                        logger.debug("Popularity backfill: %d missing but no source configured", missing)
        except Exception as e:
            logger.debug(f"popularity backfill tick skipped: {e}")
        _t.sleep(3600)  # re-check hourly; new artists fill within the hour


@bp.route('/api/discover/listening-recommendations', methods=['GET'])
@_discover_shelf_cache(key_extra=_discover_dial_key)
def get_discover_listening_recommendations():
    """#913: artists you'd love based on what you actually LISTEN to (play-weighted).

    Distinct from /api/discover/similar-artists (which is driven by your whole library /
    watchlist): this is seeded by your most-PLAYED artists, consensus-ranked across the
    similar-artist graph, and recency-boosted. The heavy lifting + storage happen during the
    watchlist scan (core.watchlist_scanner._build_listening_recommendations -> the
    'listening_recs_artists' metadata key); this endpoint just reshapes the stored list to the
    same card shape the recommended-artists row already renders. Read-only, fail-soft.
    """
    try:
        database = get_database()
        active_source = _get_active_discovery_source()
        raw = database.get_metadata('listening_recs_artists')
        if not raw:
            return jsonify({"success": True, "artists": [], "source": active_source, "count": 0})
        try:
            stored = json.loads(raw) or []
        except (ValueError, TypeError):
            stored = []

        try:
            level = float(config_manager.get('discover.adventurousness', 0.3) or 0)
        except (TypeError, ValueError):
            level = 0.0

        # Quality re-rank (aurral parity, always-on): the adventurousness dial BLENDS the weights —
        # genre/tag affinity boosts on-taste candidates (leash loosens as you get adventurous); novelty
        # penalises recs you've already heard (pull tightens). Additive — no genre match / no plays
        # leaves the score untouched, and at the default dial the weights equal the old constants.
        try:
            from core.discovery.listening_recommendations import (
                apply_adventurous_blend, genre_affinity, novelty_score)
            _pid = get_current_profile_id()
            taste = _discover_genre_taste(database, _pid)
            _names = [a.get('name') for a in stored]
            plays = database.get_play_counts_by_name(_names, _pid) if stored else {}
            pops = database.get_similar_artist_popularities(_names) if stored else {}  # for the "why" chips + dial
            for a in stored:
                if a.get('popularity') is None:
                    a['popularity'] = pops.get((a.get('name') or '').strip().lower())
                aff = genre_affinity(a.get('genres') or [], taste) if taste else 0.0
                a['_aff'] = a['_why_genre'] = aff   # _why_genre also feeds the "why this rec" chips
                a['_nov'] = novelty_score(plays.get((a.get('name') or '').strip().lower(), 0))
            # Blend the sort axis consensus<->obscurity by the dial (base = the recommendation score),
            # so the adventurous end genuinely surfaces the least-popular on-taste picks.
            stored = apply_adventurous_blend(
                stored, level, base_key='score', pop_key='popularity', tiebreak_key='seed_count')
            for a in stored:
                a.pop('_aff', None); a.pop('_nov', None)
        except Exception as _qual_err:
            logger.debug(f"genre/novelty re-rank skipped: {_qual_err}")

        result_artists = []
        for a in stored:
            name = a.get('name')
            if not name:
                continue
            if active_source == 'spotify':
                artist_id = a.get('spotify_artist_id')
            elif active_source == 'deezer':
                artist_id = a.get('deezer_artist_id') or a.get('itunes_artist_id')
            else:
                artist_id = a.get('itunes_artist_id')
            entry = {
                "artist_id": artist_id,
                "spotify_artist_id": a.get('spotify_artist_id'),
                "itunes_artist_id": a.get('itunes_artist_id'),
                "deezer_artist_id": a.get('deezer_artist_id'),
                "artist_name": name,
                "seed_count": a.get('seed_count'),
                "source": active_source,
            }
            try:
                from core.discovery.listening_recommendations import why_chips
                _why = why_chips(genre_affinity=a.get('_why_genre', 0.0),
                                 popularity=a.get('popularity'), seed_count=a.get('seed_count'),
                                 level=level)   # adaptive: "Off your usual path" on the adventurous end
                if _why:
                    entry["why"] = _why
            except Exception as _why_err:
                logger.debug(f"why chips skipped: {_why_err}")
            img = a.get('image_url')
            if img:
                entry["image_url"] = fix_artist_image_url(img)
            if a.get('genres'):
                entry["genres"] = a['genres'][:3]
            # "because you listen to X, Y, Z" — the most-played artists that point here.
            if a.get('seeds'):
                entry["because"] = a['seeds']
            result_artists.append(entry)

        # Spread the shown picks across genres (broader discovery). No-ops on small lists.
        try:
            from core.discovery.listening_recommendations import diversify_by_genre
            result_artists = diversify_by_genre(result_artists, _discover_primary_genre, cap=3)
        except Exception as _div_err:
            logger.debug(f"listening-recs diversify skipped: {_div_err}")

        return jsonify({
            "success": True,
            "artists": result_artists,
            "source": active_source,
            "count": len(result_artists),
        })
    except Exception as e:
        logger.error(f"Error getting listening recommendations: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/personalized/listening-mix', methods=['GET'])
def get_discover_listening_mix():
    """#913: the "Listening Mix" playlist row — a playable track mix from the artists you'd
    love based on what you actually listen to.

    The tracks are built during the watchlist scan (core.watchlist_scanner
    ._build_listening_recommendations -> the 'listening_recs_tracks_full' metadata key) as full
    render-ready dicts, so this endpoint just hands them back — no discovery-pool re-hydration,
    which means it can't shrink when the pool rotates (the failure mode Fresh Tape/Archives hit).
    Same {success, tracks} shape renderCompactPlaylist + the sync/download chains expect.
    """
    try:
        database = get_database()
        active_source = _get_active_discovery_source()
        raw = database.get_metadata('listening_recs_tracks_full')
        tracks = []
        if raw:
            try:
                tracks = json.loads(raw) or []
            except (ValueError, TypeError):
                tracks = []
        return jsonify({"success": True, "tracks": tracks, "source": active_source})
    except Exception as e:
        logger.error(f"Error getting listening mix: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/similar-artists/enrich', methods=['POST'])
def enrich_similar_artists():
    """Enrich a batch of artist IDs with images/genres from Spotify or iTunes.
    Uses cached metadata from DB when available, only makes API calls for uncached artists,
    and saves new results back to DB for future use."""
    try:
        data = request.get_json()
        artist_ids = data.get('artist_ids', [])
        source = data.get('source', 'spotify')

        if not artist_ids:
            return jsonify({"success": True, "artists": {}})

        database = get_database()
        enriched = {}
        uncached_ids = []

        # Check DB cache first — get all similar artists and index by external ID
        cached_artists = database.get_top_similar_artists(limit=500, profile_id=get_current_profile_id())
        cache_map = {}
        for artist in cached_artists:
            if source == 'spotify':
                ext_id = artist.similar_artist_spotify_id
            elif source == 'deezer':
                ext_id = getattr(artist, 'similar_artist_deezer_id', None) or artist.similar_artist_itunes_id
            elif source == 'musicbrainz':
                ext_id = getattr(artist, 'similar_artist_musicbrainz_id', None) or artist.similar_artist_itunes_id
            else:
                ext_id = artist.similar_artist_itunes_id
            if ext_id and ext_id not in cache_map:
                cache_map[ext_id] = artist

        for aid in artist_ids[:50]:
            cached = cache_map.get(aid)
            if cached and cached.image_url:
                # Use cached metadata
                enriched[aid] = {
                    "artist_name": cached.similar_artist_name,
                    "image_url": cached.image_url,
                    "genres": cached.genres[:3] if cached.genres else [],
                    "popularity": cached.popularity or 0
                }
            else:
                uncached_ids.append(aid)

        # Only make API calls for uncached artists
        if uncached_ids:
            if source == 'spotify' and _spotify_client() and _spotify_client().is_authenticated() and not _spotify_rate_limited():
                try:
                    from core.api_call_tracker import api_call_tracker
                    api_call_tracker.record_call('spotify', endpoint='artists_batch')
                    batch_result = _spotify_client().sp.artists(uncached_ids[:50])
                    if batch_result and 'artists' in batch_result:
                        for sp_artist in batch_result['artists']:
                            if sp_artist:
                                img_url = sp_artist['images'][0].get('url') if sp_artist.get('images') else None
                                genres = sp_artist.get('genres', [])[:3]
                                pop = sp_artist.get('popularity', 0)
                                enriched[sp_artist['id']] = {
                                    "artist_name": sp_artist.get('name'),
                                    "image_url": img_url,
                                    "genres": genres,
                                    "popularity": pop
                                }
                                # Cache to DB for future use
                                database.update_similar_artist_metadata_by_external_id(
                                    sp_artist['id'], 'spotify',
                                    image_url=img_url, genres=genres, popularity=pop
                                )
                except Exception as e:
                    from core.spotify_client import _detect_and_set_rate_limit
                    _detect_and_set_rate_limit(e, 'enrich_similar_artists')
                    logger.error(f"Error enriching Spotify batch: {e}")
            else:
                fallback_client = _get_metadata_fallback_client()
                fallback_source = _get_metadata_fallback_source()
                for aid in uncached_ids[:50]:
                    try:
                        fb_artist = fallback_client.get_artist(aid)
                        if fb_artist:
                            img_url = fb_artist.get('images', [{}])[0].get('url') if fb_artist.get('images') else None
                            genres = fb_artist.get('genres', [])[:3]
                            enriched[aid] = {
                                "artist_name": fb_artist.get('name'),
                                "image_url": img_url,
                                "genres": genres,
                                "popularity": 0
                            }
                            # Cache to DB for future use
                            database.update_similar_artist_metadata_by_external_id(
                                aid, fallback_source,
                                image_url=img_url, genres=genres, popularity=0
                            )
                    except Exception as e:
                        logger.debug("similar artist enrichment failed: %s", e)

        cached_count = len(enriched) - len([aid for aid in uncached_ids if aid in enriched])
        api_count = len([aid for aid in uncached_ids if aid in enriched])
        if uncached_ids:
            logger.warning(f"[Enrich] {cached_count} from cache, {api_count} from API ({len(uncached_ids) - api_count} missed)")

        return jsonify({"success": True, "artists": enriched})

    except Exception as e:
        logger.error(f"Error enriching similar artists: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/spotify-library', methods=['GET'])
def get_spotify_library():
    """Get cached Spotify library albums with ownership status. Only available when Spotify is authenticated."""
    try:
        # Skip entirely if Spotify is not the active source
        if not _spotify_client() or not _spotify_client().is_spotify_authenticated():
            return jsonify({
                "success": True, "albums": [], "total": 0,
                "offset": 0, "limit": 0,
                "stats": {"total": 0, "owned": 0, "missing": 0}
            })

        database = get_database()
        profile_id = get_current_profile_id()

        offset = request.args.get('offset', 0, type=int)
        limit = request.args.get('limit', 48, type=int)
        search = request.args.get('search', '', type=str)
        status_filter = request.args.get('status', 'all', type=str)
        sort = request.args.get('sort', 'date_saved', type=str)
        sort_dir = request.args.get('sort_dir', 'desc', type=str)

        # Fetch all matching albums (ownership requires post-query computation)
        all_albums, total = database.get_spotify_library_albums(
            offset=0, limit=10000,
            search=search, sort=sort, sort_dir=sort_dir, profile_id=profile_id
        )

        if not all_albums:
            return jsonify({
                "success": True, "albums": [], "total": 0,
                "offset": offset, "limit": limit,
                "stats": {"total": 0, "owned": 0, "missing": 0}
            })

        # Cross-reference with local library for ownership status
        library_spotify_ids = database.get_library_spotify_album_ids(profile_id)
        library_album_names = database.get_library_album_names()

        owned_count = 0
        for album in all_albums:
            # Check by Spotify album ID first, then fuzzy match by name
            if album['spotify_album_id'] in library_spotify_ids:
                album['in_library'] = True
            elif (album['artist_name'].lower(), album['album_name'].lower()) in library_album_names:
                album['in_library'] = True
            else:
                album['in_library'] = False

            if album['in_library']:
                owned_count += 1

        # Apply status filter then paginate
        if status_filter == 'missing':
            filtered = [a for a in all_albums if not a['in_library']]
        elif status_filter == 'owned':
            filtered = [a for a in all_albums if a['in_library']]
        else:
            filtered = all_albums

        filtered_total = len(filtered)
        albums = filtered[offset:offset + limit]

        stats = {
            'total': total,
            'owned': owned_count,
            'missing': total - owned_count,
        }

        return jsonify({
            "success": True,
            "albums": albums,
            "total": filtered_total,
            "offset": offset,
            "limit": limit,
            "stats": stats,
        })

    except Exception as e:
        logger.error(f"Error getting Spotify library: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/spotify-library/refresh', methods=['POST'])
def refresh_spotify_library():
    """Manually trigger a re-sync of the Spotify library cache"""
    try:
        def _run_sync():
            try:
                from core.watchlist_scanner import get_watchlist_scanner
                scanner = get_watchlist_scanner(_spotify_client())
                if scanner:
                    # Force full sync by clearing last_sync timestamp
                    database = get_database()
                    database.set_metadata('spotify_library_last_sync', '')
                    database.set_metadata('spotify_library_last_full_sync', '')
                    scanner.sync_spotify_library_cache(profile_id=get_current_profile_id())
                    logger.info("Manual Spotify library refresh complete")
            except Exception as e:
                logger.error(f"Error in manual Spotify library refresh: {e}")

        import threading
        thread = threading.Thread(target=_run_sync, daemon=True)
        thread.start()

        return jsonify({"success": True, "message": "Spotify library refresh started"})

    except Exception as e:
        logger.error(f"Error starting Spotify library refresh: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/recent-releases', methods=['GET'])
@_discover_shelf_cache()
def get_discover_recent_releases():
    """Get cached recent albums from watchlist and similar artists"""
    try:
        database = get_database()

        # Determine active source
        active_source = _get_active_discovery_source()

        # Get cached recent albums filtered by source (max 20)
        albums = database.get_discovery_recent_albums(limit=20, source=active_source, profile_id=get_current_profile_id())

        # Backfill missing cover art from metadata source
        for album in albums:
            if not album.get('album_cover_url'):
                cover = None
                album_id = album.get('album_deezer_id') or album.get('album_itunes_id') or album.get('album_spotify_id')
                try:
                    # Try direct ID lookup first
                    if album_id:
                        fallback = _get_metadata_fallback_client()
                        if fallback:
                            album_data = fallback.get_album(str(album_id))
                            if album_data:
                                imgs = album_data.get('images', [])
                                cover = album_data.get('image_url') or (imgs[0].get('url') if imgs else None)

                    # Fallback: search by name
                    if not cover and album.get('album_name') and album.get('artist_name'):
                        fallback = _get_metadata_fallback_client()
                        if fallback:
                            results = fallback.search_albums(f"{album['artist_name']} {album['album_name']}", limit=1)
                            if results and hasattr(results[0], 'image_url') and results[0].image_url:
                                cover = results[0].image_url
                                album_id = str(results[0].id)

                    if cover:
                        album['album_cover_url'] = cover
                        if album_id:
                            try:
                                database.update_discovery_recent_album_cover(album_id, cover)
                            except Exception as e:
                                logger.debug("recent album cover update failed: %s", e)
                except Exception as e:
                    logger.debug("recent album cover fetch failed: %s", e)

        # Filter out blacklisted artists
        blacklisted = database.get_discovery_blacklist_names()
        if blacklisted:
            albums = [a for a in albums if a.get('artist_name', '').lower() not in blacklisted]

        # Ownership: which of these new releases are ALREADY in the library.
        # The fuzzy matcher the download pipeline itself uses, so the badge
        # agrees with what a download would decide. ~20 checks per 30-min
        # shelf-cache fill — free at request time. Fail-soft per album.
        for a in albums:
            try:
                match, _conf = database.check_album_exists(
                    a.get('album_name') or '', a.get('artist_name') or '')
                a['in_library'] = match is not None
            except Exception as own_err:
                logger.debug("recent-release ownership check failed: %s", own_err)

        return jsonify({"success": True, "albums": albums, "source": active_source})

    except Exception as e:
        logger.error(f"Error getting recent releases: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/release-radar', methods=['GET'])
def get_discover_release_radar():
    """Get release radar playlist - curated selection that stays consistent until next update"""
    try:
        database = get_database()

        # Determine active source - release radar works with any source now
        active_source = _get_active_discovery_source()

        # Try source-specific playlist first, then fall back to generic
        pid = get_current_profile_id()
        # full-row snapshot first: immune to pool rotation (the silent-shrink
        # bug). the id path below stays for pre-snapshot curated rows.
        from core.discovery.curated_full import read_curated_full
        full_rows = read_curated_full(database, 'release_radar', active_source, pid)
        if full_rows:
            return jsonify({"success": True, "tracks": full_rows, "source": active_source})
        curated_track_ids = database.get_curated_playlist(f'release_radar_{active_source}', profile_id=pid)
        if not curated_track_ids:
            curated_track_ids = database.get_curated_playlist('release_radar', profile_id=pid)

        if curated_track_ids:
            # Use curated selection - fetch track data from discovery pool filtered by source
            discovery_tracks = database.get_discovery_pool_tracks(limit=5000, new_releases_only=False, source=active_source, profile_id=pid)

            # Build lookup dict with source-appropriate IDs
            tracks_by_id = {}
            for track in discovery_tracks:
                if active_source == 'spotify' and track.spotify_track_id:
                    tracks_by_id[track.spotify_track_id] = track
                elif active_source == 'deezer' and getattr(track, 'deezer_track_id', None):
                    tracks_by_id[track.deezer_track_id] = track
                elif active_source == 'itunes' and track.itunes_track_id:
                    tracks_by_id[track.itunes_track_id] = track

            selected_tracks = []
            for track_id in curated_track_ids:
                if track_id in tracks_by_id:
                    track = tracks_by_id[track_id]

                    # Parse track_data_json if it's a string
                    track_data = track.track_data_json
                    if isinstance(track_data, str):
                        try:
                            track_data = json.loads(track_data)
                        except:
                            track_data = None

                    selected_tracks.append({
                        "track_id": track.spotify_track_id or getattr(track, 'deezer_track_id', None) or track.itunes_track_id,
                        "spotify_track_id": track.spotify_track_id,
                        "itunes_track_id": track.itunes_track_id,
                        "deezer_track_id": getattr(track, 'deezer_track_id', None),
                        "track_name": track.track_name,
                        "artist_name": track.artist_name,
                        "album_name": track.album_name,
                        "album_cover_url": track.album_cover_url,
                        "duration_ms": track.duration_ms,
                        "track_data_json": track_data,
                        "source": track.source
                    })

            return jsonify({"success": True, "tracks": selected_tracks, "source": active_source})

        # Fallback: no curated playlist exists (shouldn't happen after first scan)
        return jsonify({"success": True, "tracks": [], "source": active_source})

    except Exception as e:
        logger.error(f"Error getting release radar: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/because-you-listen-to', methods=['GET'])
@_discover_shelf_cache()
def get_discover_because_you_listen_to():
    """Get 'Because You Listen To' sections — personalized by top played artists."""
    try:
        database = get_database()
        active_source = _get_active_discovery_source()
        pid = get_current_profile_id()

        # Fetch pool tracks once for all sections
        pool_tracks = database.get_discovery_pool_tracks(limit=5000, new_releases_only=False, source=active_source, profile_id=pid)
        tracks_by_id = {}
        for t in pool_tracks:
            if active_source == 'spotify' and t.spotify_track_id:
                tracks_by_id[t.spotify_track_id] = t
            elif active_source == 'itunes' and t.itunes_track_id:
                tracks_by_id[t.itunes_track_id] = t
            elif active_source == 'deezer' and getattr(t, 'deezer_track_id', None):
                tracks_by_id[t.deezer_track_id] = t

        sections = []
        for i in range(3):
            artist_name = database.get_metadata(f'bylt_artist_{i}')
            if not artist_name:
                continue
            track_ids = database.get_curated_playlist(f'because_you_listen_to_{i}', profile_id=pid)
            if not track_ids:
                continue

            tracks = []
            for tid in track_ids:
                t = tracks_by_id.get(tid)
                if t:
                    tracks.append({
                        'id': tid,
                        'name': t.track_name,
                        'artist': t.artist_name,
                        'album': t.album_name,
                        'image_url': t.album_cover_url,
                        'duration_ms': t.duration_ms,
                        'popularity': t.popularity,
                    })

            if tracks:
                # Get artist image
                artist_image = None
                try:
                    conn = database._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("SELECT image_url AS thumb_url FROM lib2_artists WHERE name_key = ? LIMIT 1",
                                   (_catalogue_name_key(artist_name),))
                    row = cursor.fetchone()
                    if row and row[0]:
                        artist_image = fix_artist_image_url(row[0])
                    conn.close()
                except Exception as e:
                    logger.debug("artist image lookup failed: %s", e)

                sections.append({
                    'artist_name': artist_name,
                    'artist_image': artist_image,
                    'tracks': tracks,
                })

        return jsonify({'success': True, 'sections': sections})
    except Exception as e:
        logger.error(f"Error getting BYLT: {e}")
        return jsonify({'success': True, 'sections': []})

@bp.route('/api/discover/undiscovered-albums', methods=['GET'])
@_discover_shelf_cache()
def get_discover_undiscovered_albums():
    """Albums by artists you listen to that aren't in your library — from cache."""
    try:
        database = get_database()
        cache = get_metadata_cache()
        active_source = _get_active_discovery_source()

        # Get top played artists
        top = database.get_top_artists('all', 25)
        artist_names = [a['name'] for a in top if a.get('name')]
        if not artist_names:
            return jsonify({'success': True, 'albums': []})

        # Build library album keys for exclusion
        with database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT LOWER(al.title), LOWER(ar.name)
                FROM lib2_albums al
                JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                WHERE EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f
                              ON f.track_id=t.id WHERE t.album_id=al.id
                              AND f.file_state='active' AND TRIM(f.path)<>'')
            """)
            library_keys = {(r[0].strip(), r[1].strip()) for r in cursor.fetchall()}

        albums = cache.get_undiscovered_albums(artist_names, library_keys, source=active_source, limit=20)
        return jsonify({'success': True, 'albums': albums})
    except Exception as e:
        logger.error(f"Undiscovered albums endpoint error: {e}")
        return jsonify({'success': True, 'albums': []})

@bp.route('/api/discover/genre-new-releases', methods=['GET'])
@_discover_shelf_cache()
def get_discover_genre_new_releases():
    """Recent releases matching your top genres — from cache."""
    try:
        database = get_database()
        cache = get_metadata_cache()
        genres = database.get_genre_breakdown('all')
        genre_names = [g['genre'] for g in (genres or [])[:10] if g.get('genre')]
        if not genre_names:
            return jsonify({'success': True, 'albums': []})
        allowed = _get_genre_allowed_sources()
        albums = cache.get_genre_new_releases(genre_names, sources=allowed, limit=20)
        return jsonify({'success': True, 'albums': albums})
    except Exception as e:
        logger.error(f"Genre new releases endpoint error: {e}")
        return jsonify({'success': True, 'albums': []})

@bp.route('/api/discover/label-explorer', methods=['GET'])
@_discover_shelf_cache()
def get_discover_label_explorer():
    """Popular albums from labels in your library — from cache."""
    try:
        database = get_database()
        cache = get_metadata_cache()
        with database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT al.label FROM lib2_albums al
                WHERE al.label IS NOT NULL AND al.label != ''
                  AND EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f
                              ON f.track_id=t.id WHERE t.album_id=al.id
                              AND f.file_state='active' AND TRIM(f.path)<>'')
                LIMIT 30
            """)
            labels = {r[0] for r in cursor.fetchall()}
        active_source = _get_active_discovery_source()
        if not labels:
            return jsonify({'success': True, 'albums': [], 'labels': []})
        albums = cache.get_label_explorer(labels, source=active_source, limit=20)
        return jsonify({'success': True, 'albums': albums, 'labels': sorted(labels)})
    except Exception as e:
        logger.error(f"Label explorer endpoint error: {e}")
        return jsonify({'success': True, 'albums': [], 'labels': []})

@bp.route('/api/discover/deep-cuts', methods=['GET'])
@_discover_shelf_cache()
def get_discover_deep_cuts():
    """Low-popularity tracks by the artists you listen to — from cache."""
    try:
        database = get_database()
        cache = get_metadata_cache()
        top = database.get_top_artists('all', 15)
        artist_names = [a['name'] for a in top if a.get('name')]
        active_source = _get_active_discovery_source()
        if not artist_names:
            return jsonify({'success': True, 'tracks': []})
        tracks = cache.get_deep_cuts(artist_names, source=active_source, popularity_cap=30, limit=20)
        return jsonify({'success': True, 'tracks': tracks})
    except Exception as e:
        logger.error(f"Deep cuts endpoint error: {e}")
        return jsonify({'success': True, 'tracks': []})

def _get_genre_allowed_sources():
    """Get allowed metadata sources for genre features.
    Spotify authed → ['spotify', 'itunes', 'deezer']
    Not authed → ['itunes', 'deezer']"""
    sources = ['itunes', 'deezer']
    if _spotify_client() and _spotify_client().is_spotify_authenticated():
        sources.append('spotify')
    return sources

@bp.route('/api/discover/genre-explorer', methods=['GET'])
@_discover_shelf_cache()
def get_discover_genre_explorer():
    """Genre landscape from cached artists — highlights unexplored genres."""
    try:
        database = get_database()
        cache = get_metadata_cache()
        genres = database.get_genre_breakdown('all')
        user_genres = {g['genre'] for g in (genres or []) if g.get('genre')}
        allowed = _get_genre_allowed_sources()
        data = cache.get_genre_explorer(user_genres, sources=allowed)
        return jsonify({'success': True, 'genres': data})
    except Exception as e:
        logger.error(f"Genre explorer endpoint error: {e}")
        return jsonify({'success': True, 'genres': []})

@bp.route('/api/discover/genre-deep-dive', methods=['GET'])
def get_discover_genre_deep_dive():
    """Get artists + albums for a genre — from cache."""
    try:
        genre = request.args.get('genre', '').strip()
        if not genre:
            return jsonify({'success': False, 'error': 'genre required'}), 400
        cache = get_metadata_cache()
        allowed = _get_genre_allowed_sources()
        data = cache.get_genre_deep_dive(genre, sources=allowed)
        return jsonify({'success': True, **data})
    except Exception as e:
        logger.error(f"Genre albums endpoint error: {e}")
        return jsonify({'success': True, 'albums': []})

@bp.route('/api/discover/resolve-cache-album', methods=['GET'])
def resolve_cache_album():
    """Look up a real album entity in the cache by name+artist (avoids playlist ID confusion)."""
    try:
        name = request.args.get('name', '').strip()
        artist = request.args.get('artist', '').strip()
        if not name or not artist:
            return jsonify({'success': False, 'error': 'name and artist required'}), 400

        active_source = _get_active_discovery_source()
        database = get_database()
        with database._get_connection() as conn:
            cursor = conn.cursor()
            # Strategy 1: exact match, prefer active source
            cursor.execute("""
                SELECT entity_id, source FROM metadata_cache_entities
                WHERE entity_type = 'album'
                  AND name COLLATE NOCASE = ? COLLATE NOCASE
                  AND artist_name COLLATE NOCASE = ? COLLATE NOCASE
                ORDER BY CASE WHEN source = ? THEN 0 ELSE 1 END
                LIMIT 1
            """, (name, artist, active_source))
            row = cursor.fetchone()
            if row:
                return jsonify({'success': True, 'entity_id': row['entity_id'], 'source': row['source']})

            # Strategy 2: partial match (handles "Album - Single" vs "Album" naming)
            cursor.execute("""
                SELECT entity_id, source FROM metadata_cache_entities
                WHERE entity_type = 'album'
                  AND name COLLATE NOCASE LIKE ? COLLATE NOCASE
                  AND artist_name COLLATE NOCASE LIKE ? COLLATE NOCASE
                ORDER BY CASE WHEN source = ? THEN 0 ELSE 1 END
                LIMIT 1
            """, (f'%{name}%', f'%{artist}%', active_source))
            row = cursor.fetchone()
            if row:
                return jsonify({'success': True, 'entity_id': row['entity_id'], 'source': row['source']})

            # Strategy 3: not in cache — try searching the fallback client directly
            fallback = _get_metadata_fallback_client()
            if fallback:
                try:
                    results = fallback.search_albums(f"{artist} {name}", limit=3)
                    if results:
                        r = results[0]
                        return jsonify({'success': True, 'entity_id': str(r.id), 'source': _get_metadata_fallback_source()})
                except Exception as e:
                    logger.debug("fallback album search failed: %s", e)

            return jsonify({'success': False, 'error': 'Album not found in cache'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/discover/weekly', methods=['GET'])
def get_discover_weekly():
    """Get discovery weekly playlist - curated selection that stays consistent until next update"""
    try:
        database = get_database()

        # Determine active source
        active_source = _get_active_discovery_source()

        # Try source-specific playlist first, then fall back to generic
        pid = get_current_profile_id()
        # full-row snapshot first - see release-radar above
        from core.discovery.curated_full import read_curated_full
        full_rows = read_curated_full(database, 'discovery_weekly', active_source, pid)
        if full_rows:
            return jsonify({"success": True, "tracks": full_rows, "source": active_source})
        curated_track_ids = database.get_curated_playlist(f'discovery_weekly_{active_source}', profile_id=pid)
        if not curated_track_ids:
            curated_track_ids = database.get_curated_playlist('discovery_weekly', profile_id=pid)

        if curated_track_ids:
            # Use curated selection - fetch track data from discovery pool filtered by source
            discovery_tracks = database.get_discovery_pool_tracks(limit=5000, new_releases_only=False, source=active_source, profile_id=pid)

            # Build lookup dict with source-appropriate IDs
            tracks_by_id = {}
            for track in discovery_tracks:
                if active_source == 'spotify' and track.spotify_track_id:
                    tracks_by_id[track.spotify_track_id] = track
                elif active_source == 'deezer' and getattr(track, 'deezer_track_id', None):
                    tracks_by_id[track.deezer_track_id] = track
                elif active_source == 'itunes' and track.itunes_track_id:
                    tracks_by_id[track.itunes_track_id] = track

            selected_tracks = []
            for track_id in curated_track_ids:
                if track_id in tracks_by_id:
                    track = tracks_by_id[track_id]

                    # Parse track_data_json if it's a string
                    track_data = track.track_data_json
                    if isinstance(track_data, str):
                        try:
                            track_data = json.loads(track_data)
                        except:
                            track_data = None

                    selected_tracks.append({
                        "track_id": track.spotify_track_id or getattr(track, 'deezer_track_id', None) or track.itunes_track_id,
                        "spotify_track_id": track.spotify_track_id,
                        "itunes_track_id": track.itunes_track_id,
                        "deezer_track_id": getattr(track, 'deezer_track_id', None),
                        "track_name": track.track_name,
                        "artist_name": track.artist_name,
                        "album_name": track.album_name,
                        "album_cover_url": track.album_cover_url,
                        "duration_ms": track.duration_ms,
                        "track_data_json": track_data,
                        "source": track.source
                    })

            return jsonify({"success": True, "tracks": selected_tracks, "source": active_source})

        # Fallback: no curated playlist exists (shouldn't happen after first scan)
        return jsonify({"success": True, "tracks": [], "source": active_source})

    except Exception as e:
        logger.error(f"Error getting discovery weekly: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/refresh', methods=['POST'])
def refresh_discover_data():
    """
    Force refresh discover page data (recent albums cache and curated playlists).
    Useful for initial setup or when data appears stale.
    """
    try:
        from core.watchlist_scanner import WatchlistScanner

        database = get_database()
        scanner = WatchlistScanner(_spotify_client(), database)

        logger.info("[Discover Refresh] Starting forced refresh of discover data...")

        refresh_pid = get_current_profile_id()

        # Cache recent albums from watchlist and similar artists
        logger.info("[Discover Refresh] Caching recent albums...")
        scanner.cache_discovery_recent_albums(profile_id=refresh_pid)

        # Curate playlists
        logger.info("[Discover Refresh] Curating discovery playlists...")
        scanner.curate_discovery_playlists(profile_id=refresh_pid)

        # Get counts for response
        active_source = _get_active_discovery_source()
        pid = get_current_profile_id()
        recent_albums = database.get_discovery_recent_albums(limit=100, source=active_source, profile_id=pid)
        release_radar = database.get_curated_playlist(f'release_radar_{active_source}', profile_id=pid) or []
        discovery_weekly = database.get_curated_playlist(f'discovery_weekly_{active_source}', profile_id=pid) or []

        logger.info(f"[Discover Refresh] Complete! Recent albums: {len(recent_albums)}, Release Radar: {len(release_radar)} tracks, Discovery Weekly: {len(discovery_weekly)} tracks")

        return jsonify({
            "success": True,
            "message": "Discover data refreshed",
            "source": active_source,
            "recent_albums_count": len(recent_albums),
            "release_radar_tracks": len(release_radar),
            "discovery_weekly_tracks": len(discovery_weekly)
        })

    except Exception as e:
        logger.error(f"Error refreshing discover data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/diagnose', methods=['GET'])
def diagnose_discover_data():
    """
    Diagnostic endpoint to check the state of discover data.
    Returns counts of similar artists, discovery pool, recent albums, etc.
    """
    try:
        database = get_database()
        active_source = _get_active_discovery_source()
        pid = get_current_profile_id()

        with database._get_connection() as conn:
            cursor = conn.cursor()

            # Similar artists stats
            cursor.execute("SELECT COUNT(*) as total FROM similar_artists WHERE profile_id = ?", (pid,))
            total_similar = cursor.fetchone()['total']

            cursor.execute("SELECT COUNT(*) as count FROM similar_artists WHERE similar_artist_itunes_id IS NOT NULL AND profile_id = ?", (pid,))
            similar_with_itunes = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM similar_artists WHERE similar_artist_spotify_id IS NOT NULL AND profile_id = ?", (pid,))
            similar_with_spotify = cursor.fetchone()['count']

            # Discovery pool stats
            cursor.execute("SELECT source, COUNT(*) as count FROM discovery_pool WHERE profile_id = ? GROUP BY source", (pid,))
            pool_by_source = {row['source']: row['count'] for row in cursor.fetchall()}

            # Recent albums stats
            cursor.execute("SELECT source, COUNT(*) as count FROM discovery_recent_albums WHERE profile_id = ? GROUP BY source", (pid,))
            albums_by_source = {row['source']: row['count'] for row in cursor.fetchall()}

            # Curated playlists
            cursor.execute("SELECT playlist_type, track_ids_json FROM discovery_curated_playlists WHERE profile_id = ?", (pid,))
            playlists = {}
            for row in cursor.fetchall():
                import json
                track_ids = json.loads(row['track_ids_json']) if row['track_ids_json'] else []
                playlists[row['playlist_type']] = len(track_ids)

            # Watchlist artists
            cursor.execute("SELECT COUNT(*) as total FROM watchlist_artists WHERE profile_id = ?", (pid,))
            total_watchlist = cursor.fetchone()['total']

            cursor.execute("SELECT COUNT(*) as count FROM watchlist_artists WHERE itunes_artist_id IS NOT NULL AND profile_id = ?", (pid,))
            watchlist_with_itunes = cursor.fetchone()['count']

        return jsonify({
            "success": True,
            "active_source": active_source,
            "similar_artists": {
                "total": total_similar,
                "with_itunes_id": similar_with_itunes,
                "with_spotify_id": similar_with_spotify
            },
            "discovery_pool": pool_by_source,
            "recent_albums": albums_by_source,
            "curated_playlists": playlists,
            "watchlist_artists": {
                "total": total_watchlist,
                "with_itunes_id": watchlist_with_itunes
            }
        })

    except Exception as e:
        logger.error(f"Error diagnosing discover data: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================
# SEASONAL DISCOVERY ENDPOINTS
# ========================================

@bp.route('/api/discover/seasonal/current', methods=['GET'])
@_discover_shelf_cache()
def get_current_seasonal_content():
    """Auto-detect and return current season's content"""
    try:
        from core.seasonal_discovery import get_seasonal_discovery_service

        database = get_database()
        seasonal_service = get_seasonal_discovery_service(_spotify_client(), database)

        # Get current season
        current_season = seasonal_service.get_current_season()

        if not current_season:
            return jsonify({"success": True, "season": None, "albums": [], "playlist_available": False})

        # Get seasonal config
        from core.seasonal_discovery import SEASONAL_CONFIG
        config = SEASONAL_CONFIG[current_season]

        # Get albums for active source (increased limit for more variety)
        active_source = _get_active_discovery_source()
        albums = seasonal_service.get_seasonal_albums(current_season, limit=40, source=active_source)

        # Check if playlist is curated for active source
        playlist_track_ids = seasonal_service.get_curated_seasonal_playlist(current_season, source=active_source)

        return jsonify({
            "success": True,
            "season": current_season,
            "name": config['name'],
            "description": config['description'],
            "icon": config['icon'],
            "albums": albums,
            "playlist_available": len(playlist_track_ids) > 0
        })

    except Exception as e:
        logger.error(f"Error getting current seasonal content: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/seasonal/<season_key>/albums', methods=['GET'])
def get_seasonal_albums(season_key):
    """Get albums for a specific season"""
    try:
        from core.seasonal_discovery import get_seasonal_discovery_service, SEASONAL_CONFIG

        if season_key not in SEASONAL_CONFIG:
            return jsonify({"success": False, "error": "Invalid season"}), 400

        database = get_database()
        seasonal_service = get_seasonal_discovery_service(_spotify_client(), database)

        active_source = _get_active_discovery_source()
        albums = seasonal_service.get_seasonal_albums(season_key, limit=40, source=active_source)
        config = SEASONAL_CONFIG[season_key]

        return jsonify({
            "success": True,
            "season": season_key,
            "name": config['name'],
            "description": config['description'],
            "icon": config['icon'],
            "albums": albums
        })

    except Exception as e:
        logger.error(f"Error getting seasonal albums: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/seasonal/<season_key>/playlist', methods=['GET'])
def get_seasonal_playlist(season_key):
    """Get curated playlist for a specific season"""
    try:
        from core.seasonal_discovery import get_seasonal_discovery_service, SEASONAL_CONFIG

        if season_key not in SEASONAL_CONFIG:
            return jsonify({"success": False, "error": "Invalid season"}), 400

        database = get_database()
        seasonal_service = get_seasonal_discovery_service(_spotify_client(), database)

        # Get curated track IDs for active source
        active_source = _get_active_discovery_source()
        track_ids = seasonal_service.get_curated_seasonal_playlist(season_key, source=active_source)

        if not track_ids:
            return jsonify({"success": True, "tracks": []})

        # Use source-appropriate ID column for lookups
        track_id_col = 'spotify_track_id' if active_source == 'spotify' else 'itunes_track_id'

        # Fetch track details from seasonal tracks or discovery pool (filtered by source)
        tracks = []
        with database._get_connection() as conn:
            cursor = conn.cursor()

            for track_id in track_ids:
                # Try seasonal_tracks first (filtered by source)
                cursor.execute("""
                    SELECT
                        spotify_track_id,
                        track_name,
                        artist_name,
                        album_name,
                        album_cover_url,
                        duration_ms,
                        popularity,
                        track_data_json
                    FROM seasonal_tracks
                    WHERE spotify_track_id = ? AND source = ?
                """, (track_id, active_source))

                result = cursor.fetchone()

                if result:
                    track_dict = dict(result)
                    # Parse track_data_json if available
                    if track_dict.get('track_data_json'):
                        try:
                            import json
                            track_dict['track_data_json'] = json.loads(track_dict['track_data_json'])
                        except Exception as e:
                            logger.debug("track_data_json parse: %s", e)
                    tracks.append(track_dict)
                else:
                    # Try discovery_pool as fallback (filtered by source)
                    cursor.execute(f"""
                        SELECT
                            {track_id_col} as spotify_track_id,
                            track_name,
                            artist_name,
                            album_name,
                            album_cover_url,
                            duration_ms,
                            popularity,
                            track_data_json
                        FROM discovery_pool
                        WHERE {track_id_col} = ? AND source = ?
                    """, (track_id, active_source))

                    result = cursor.fetchone()
                    if result:
                        track_dict = dict(result)
                        # Parse track_data_json if available
                        if track_dict.get('track_data_json'):
                            try:
                                import json
                                track_dict['track_data_json'] = json.loads(track_dict['track_data_json'])
                            except Exception as e:
                                logger.debug("discovery track_data_json parse: %s", e)
                        tracks.append(track_dict)

        config = SEASONAL_CONFIG[season_key]

        return jsonify({
            "success": True,
            "season": season_key,
            "name": config['name'],
            "description": config['description'],
            "icon": config['icon'],
            "tracks": tracks
        })

    except Exception as e:
        logger.error(f"Error getting seasonal playlist: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/seasonal/refresh', methods=['POST'])
def refresh_seasonal_content():
    """Manually trigger seasonal content refresh (admin function)"""
    try:
        from core.seasonal_discovery import get_seasonal_discovery_service

        database = get_database()
        seasonal_service = get_seasonal_discovery_service(_spotify_client(), database)

        # Force populate current season in background thread (bypass 7-day threshold)
        import threading
        def populate_all():
            try:
                current_season = seasonal_service.get_current_season()
                if current_season:
                    logger.info(f"Force-refreshing seasonal content for: {current_season}")
                    seasonal_service.populate_seasonal_content(current_season)
                    seasonal_service.curate_seasonal_playlist(current_season)
                    logger.info(f"Seasonal content refreshed for: {current_season}")
                else:
                    logger.warning("ℹ️ No active season to refresh")
            except Exception as e:
                logger.error(f"Error in background seasonal population: {e}")

        thread = threading.Thread(target=populate_all, daemon=True)
        thread.start()

        return jsonify({"success": True, "message": "Seasonal content refresh started"})

    except Exception as e:
        logger.error(f"Error refreshing seasonal content: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ========================================
# PERSONALIZED PLAYLISTS ENDPOINTS
# ========================================

@bp.route('/api/discover/personalized/decade/<int:decade>', methods=['GET'])
def get_decade_playlist(decade):
    """Get tracks from a specific decade"""
    try:
        from core.personalized_playlists import get_personalized_playlists_service

        database = get_database()
        service = get_personalized_playlists_service(database, _spotify_client())

        tracks = service.get_decade_playlist(decade, limit=100)

        return jsonify({
            "success": True,
            "decade": decade,
            "tracks": tracks
        })

    except Exception as e:
        logger.error(f"Error getting decade playlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/personalized/popular-picks', methods=['GET'])
def get_popular_picks_playlist():
    """Get high popularity tracks from discovery pool"""
    try:
        from core.personalized_playlists import get_personalized_playlists_service

        database = get_database()
        service = get_personalized_playlists_service(database, _spotify_client())

        tracks = service.get_popular_picks(limit=50)

        return jsonify({
            "success": True,
            "tracks": tracks
        })

    except Exception as e:
        logger.error(f"Error getting popular picks playlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/personalized/hidden-gems', methods=['GET'])
def get_hidden_gems_playlist():
    """Get hidden gems (low popularity) from discovery pool"""
    try:
        from core.personalized_playlists import get_personalized_playlists_service

        database = get_database()
        service = get_personalized_playlists_service(database, _spotify_client())

        # "best obscure, not random obscure" - the cached genre-taste profile
        # ranks candidates before the diversity cut
        tracks = service.get_hidden_gems(
            limit=50, taste_profile=_discover_genre_taste(database, get_current_profile_id()))

        return jsonify({
            "success": True,
            "tracks": tracks
        })

    except Exception as e:
        logger.error(f"Error getting hidden gems playlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/personalized/daily-mixes', methods=['GET'])
def get_daily_mixes():
    """Daily Mixes - taste-clustered blends of owned + discovery tracks.

    Rebuilt aug 25 on core/personalized/daily_mixes.py: the legacy service's
    '50% your library' half permanently returned nothing (library tracks
    carry no source ids) so every mix degraded to a relabeled genre playlist,
    and the shelf feeder was rightly marked dead. These are the real thing -
    clustered from listening_history, mostly owned (playable now), flavored
    with similar-artist discovery, regenerated daily via TTL."""
    try:
        from core.personalized.daily_mixes import get_or_build_daily_mixes
        force = request.args.get('refresh') in ('1', 'true')
        payload = get_or_build_daily_mixes(
            get_database(), get_current_profile_id(), force=force)
        return jsonify({
            "success": True,
            "mixes": payload.get("mixes", []),
            "generated_at": payload.get("generated_at"),
        })

    except Exception as e:
        logger.error(f"Error getting daily mixes: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/personalized/discovery-shuffle', methods=['GET'])
def get_discovery_shuffle():
    """Get Discovery Shuffle playlist - random tracks from discovery pool"""
    try:
        from core.personalized_playlists import get_personalized_playlists_service

        database = get_database()
        service = get_personalized_playlists_service(database, _spotify_client())

        limit = int(request.args.get('limit', 50))
        tracks = service.get_discovery_shuffle(limit=limit)

        return jsonify({
            "success": True,
            "tracks": tracks
        })

    except Exception as e:
        logger.error(f"Error getting discovery shuffle playlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================================
# Personalized Playlists v2 — unified storage + manager-backed routes.
# Wraps every personalized playlist (Group A + Group B) behind one API
# surface. Generators in `core/personalized/generators/` register at
# import time; this set of routes exposes the manager for the UI.
# Legacy `/api/discover/personalized/...` endpoints stay alive for
# backward compat during the UI migration window.
# ========================================================================

# Trigger registration of every generator (side-effect import).
from core.personalized import generators as _personalized_generators  # noqa: F401
from core.personalized import api as _personalized_api
from core.personalized.manager import PersonalizedPlaylistManager as _PersonalizedManager


def _build_personalized_manager():
    """Construct a manager wired with whatever each generator needs.

    Per-request construction: the underlying services are cheap
    accessors, so we don't bother caching. If profiling shows
    overhead, this becomes a module-level lazy singleton."""
    from core.personalized_playlists import get_personalized_playlists_service
    from core.seasonal_discovery import get_seasonal_discovery_service
    database = get_database()
    deps = types.SimpleNamespace(
        database=database,
        service=get_personalized_playlists_service(database, _spotify_client()),
        seasonal_service=get_seasonal_discovery_service(_spotify_client(), database),
        get_current_profile_id=get_current_profile_id,
        get_active_discovery_source=_get_active_discovery_source,
    )
    return _PersonalizedManager(database=database, deps=deps)


# ── personalized playlist endpoints live in api/personalized.py now ─────────


# ─── Unified blocklist (artist/album/track) — Phase 1 ───
# Distinct from /api/library/blacklist (download source skipping). Profile-
# scoped. On add, the other metadata sources' IDs are resolved synchronously
# (best-effort) so a ban survives a source switch immediately.

@bp.route('/api/blocklist', methods=['GET'])
def get_blocklist():
    try:
        entity_type = request.args.get('entity_type')
        if entity_type and entity_type not in ('artist', 'album', 'track'):
            return jsonify({"success": False, "error": "invalid entity_type"}), 400
        entries = get_database().get_blocklist(get_current_profile_id(), entity_type=entity_type)
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        logger.error(f"Error getting blocklist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/blocklist', methods=['POST'])
def add_blocklist():
    try:
        data = request.get_json() or {}
        entity_type = (data.get('entity_type') or '').strip().lower()
        name = (data.get('name') or '').strip()
        if entity_type not in ('artist', 'album', 'track') or not name:
            return jsonify({"success": False, "error": "entity_type and name are required"}), 400

        # The source the user searched + its id for this item.
        source = (data.get('source') or '').strip().lower()
        source_id = (data.get('source_id') or '').strip() or None
        ids = {'spotify_id': None, 'itunes_id': None, 'deezer_id': None, 'musicbrainz_id': None}
        col = {'spotify': 'spotify_id', 'itunes': 'itunes_id',
               'deezer': 'deezer_id', 'musicbrainz': 'musicbrainz_id'}.get(source)
        if col and source_id:
            ids[col] = source_id

        # Resolve the OTHER sources now (best-effort) so the ban is cross-source
        # from the first scan. Failures just leave a source unmatched.
        try:
            from core.blocklist.backfill import resolve_missing_ids
            from core.blocklist.runtime import build_resolvers
            probe = {'entity_type': entity_type, 'name': name,
                     'parent_name': data.get('parent_name'), **ids}
            ids.update(resolve_missing_ids(probe, build_resolvers()))
        except Exception as e:
            logger.debug("blocklist add backfill skipped: %s", e)

        new_id = get_database().add_blocklist_entry(
            get_current_profile_id(), entity_type, name,
            spotify_id=ids['spotify_id'], itunes_id=ids['itunes_id'],
            deezer_id=ids['deezer_id'], musicbrainz_id=ids['musicbrainz_id'],
            parent_name=data.get('parent_name'))
        if not new_id:
            return jsonify({"success": False, "error": "Could not add entry"}), 500
        logger.info("Blocklisted %s '%s'", entity_type, name)
        return jsonify({"success": True, "id": new_id})
    except Exception as e:
        logger.error(f"Error adding blocklist entry: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/blocklist/search', methods=['GET'])
def search_blocklist_candidates():
    """Search the active metadata source for an artist/album/track to block.
    Thin wrapper over the manual-match service search so the modal doesn't need
    to know which source is active."""
    try:
        entity_type = (request.args.get('type') or 'artist').strip().lower()
        if entity_type not in ('artist', 'album', 'track'):
            return jsonify({"success": False, "error": "invalid type"}), 400
        query = (request.args.get('q') or '').strip()
        if not query:
            return jsonify({"success": True, "results": []})
        from core.metadata.registry import get_primary_source
        source = get_primary_source() or 'spotify'
        results = _search_service(source, entity_type, query)
        return jsonify({"success": True, "source": source, "results": results})
    except Exception as e:
        logger.error(f"Error searching blocklist candidates: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/blocklist/<int:entry_id>', methods=['DELETE'])
def remove_blocklist(entry_id):
    try:
        ok = get_database().remove_blocklist_entry(get_current_profile_id(), entry_id)
        return jsonify({"success": ok})
    except Exception as e:
        logger.error(f"Error removing blocklist entry: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/artist-blacklist', methods=['GET'])
def get_discovery_artist_blacklist():
    """Get all blacklisted discovery artists."""
    try:
        database = get_database()
        entries = database.get_discovery_blacklist()
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/artist-blacklist', methods=['POST'])
def add_discovery_artist_blacklist():
    """Block an artist from appearing in discovery results."""
    try:
        data = request.get_json() or {}
        artist_name = data.get('artist_name', '').strip()
        if not artist_name:
            return jsonify({"success": False, "error": "artist_name is required"}), 400

        database = get_database()
        success = database.add_to_discovery_blacklist(
            artist_name=artist_name,
            spotify_id=data.get('spotify_artist_id'),
            itunes_id=data.get('itunes_artist_id'),
            deezer_id=data.get('deezer_artist_id'),
        )
        if success:
            logger.info(f"Blocked artist from discovery: {artist_name}")
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/artist-blacklist/<int:blacklist_id>', methods=['DELETE'])
def remove_discovery_artist_blacklist(blacklist_id):
    """Unblock an artist from discovery."""
    try:
        database = get_database()
        success = database.remove_from_discovery_blacklist(blacklist_id)
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── Your Artists (Liked Artists Pool) ──

@bp.route('/api/discover/your-artists', methods=['GET'])
def get_your_artists():
    """Get liked artists for the Discover carousel (20 random matched on active source)."""
    try:
        database = get_database()
        profile_id = get_current_profile_id()

        # Determine active source column — only show artists with THIS source's ID
        active_source = 'spotify'
        if _spotify_client() and _spotify_client().is_spotify_authenticated():
            active_source = 'spotify'
        else:
            fb = _get_metadata_fallback_source()
            if fb:
                active_source = fb
        active_col = {'spotify': 'spotify_artist_id', 'itunes': 'itunes_artist_id',
                      'deezer': 'deezer_artist_id', 'discogs': 'discogs_artist_id'}.get(active_source, 'spotify_artist_id')

        # Check if refresh needed (>24h stale or empty)
        last_fetch = database.get_liked_artists_last_fetch(profile_id)
        stale = True
        if last_fetch:
            from datetime import datetime, timedelta
            try:
                if isinstance(last_fetch, str):
                    last_dt = datetime.fromisoformat(last_fetch.replace('Z', '+00:00'))
                else:
                    last_dt = last_fetch
                stale = (datetime.now() - last_dt.replace(tzinfo=None)) > timedelta(hours=24)
            except Exception:
                stale = True

        if stale:
            _trigger_your_artists_refresh(profile_id)

        database.sync_liked_artists_watchlist_flags(profile_id)

        # Only return artists matched to the active source
        result = database.get_liked_artists(
            profile_id=profile_id, limit=20, random=True, matched_only=True,
            require_source_id=active_col
        )
        result['stale'] = stale
        result['success'] = True
        result['active_source'] = active_source
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting your artists: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/your-artists/all', methods=['GET'])
def get_your_artists_all():
    """Get all liked artists for the View All modal (paginated)."""
    try:
        database = get_database()
        profile_id = get_current_profile_id()
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        search = request.args.get('search', '').strip()
        source_filter = request.args.get('source', '').strip()
        sort = request.args.get('sort', 'name')

        # Same active source filtering as carousel
        active_source = 'spotify'
        if _spotify_client() and _spotify_client().is_spotify_authenticated():
            active_source = 'spotify'
        else:
            fb = _get_metadata_fallback_source()
            if fb:
                active_source = fb
        active_col = {'spotify': 'spotify_artist_id', 'itunes': 'itunes_artist_id',
                      'deezer': 'deezer_artist_id', 'discogs': 'discogs_artist_id'}.get(active_source, 'spotify_artist_id')

        database.sync_liked_artists_watchlist_flags(profile_id)
        result = database.get_liked_artists(
            profile_id=profile_id, matched_only=True,
            page=page, per_page=per_page,
            search=search, source_filter=source_filter or None,
            sort=sort, require_source_id=active_col
        )
        result['success'] = True
        result['active_source'] = active_source
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting all your artists: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/your-artists/refresh', methods=['POST'])
def refresh_your_artists():
    """Force-trigger a fetch + match cycle for liked artists. ?clear=true wipes pool first."""
    try:
        profile_id = get_current_profile_id()
        if request.args.get('clear', '').lower() == 'true':
            database = get_database()
            cleared = database.clear_liked_artists(profile_id)
            logger.info(f"[Your Artists] Cleared {cleared} entries before refresh")
        _trigger_your_artists_refresh(profile_id)
        return jsonify({"success": True, "message": "Refresh started"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/your-artists/sources', methods=['GET'])
def get_your_artists_sources():
    """Return current source config + which services are connected."""
    try:
        enabled_raw = config_manager.get('discover.your_artists_sources', 'spotify,tidal,lastfm,deezer')
        enabled = [s.strip() for s in enabled_raw.split(',') if s.strip()]

        connected = []
        # Spotify
        if _spotify_client() and _spotify_client().is_spotify_authenticated():
            connected.append('spotify')
        # Tidal
        try:
            if _tidal_client() and hasattr(_tidal_client(), '_ensure_valid_token') and _tidal_client()._ensure_valid_token():
                connected.append('tidal')
        except Exception as e:
            logger.debug("tidal auth check failed: %s", e)
        # Last.fm
        if config_manager.get('lastfm.api_key', '') and config_manager.get('lastfm.session_key', ''):
            connected.append('lastfm')
        # Deezer — OAuth token OR ARL token both count as connected
        try:
            deezer_cl = _get_deezer_client()
            deezer_oauth = deezer_cl and hasattr(deezer_cl, 'is_user_authenticated') and deezer_cl.is_user_authenticated()
            deezer_arl = (hasattr(download_orchestrator, 'client') and download_orchestrator.client("deezer_dl")
                          and download_orchestrator.client("deezer_dl").is_authenticated())
            if deezer_oauth or deezer_arl:
                connected.append('deezer')
        except Exception as e:
            logger.debug("deezer auth check failed: %s", e)

        return jsonify({"success": True, "enabled": enabled, "connected": connected})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


_your_artists_refresh_lock = threading.Lock()
_your_artists_refreshing = False

def _trigger_your_artists_refresh(profile_id: int):
    """Start background fetch + match if not already running."""
    global _your_artists_refreshing
    if _your_artists_refreshing:
        return
    with _your_artists_refresh_lock:
        if _your_artists_refreshing:
            return
        _your_artists_refreshing = True

    def _run():
        global _your_artists_refreshing
        try:
            _fetch_and_match_liked_artists(profile_id)
        except Exception as e:
            logger.error(f"Your artists refresh failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            _your_artists_refreshing = False

    threading.Thread(target=_run, daemon=True, name="YourArtistsRefresh").start()


def _fetch_and_match_liked_artists(profile_id: int):
    """Background worker: fetch from services, deduplicate, match to active source."""
    database = get_database()
    fetched = 0

    enabled_raw = config_manager.get('discover.your_artists_sources', 'spotify,tidal,lastfm,deezer')
    enabled_sources = {s.strip() for s in enabled_raw.split(',') if s.strip()}

    # 1. Fetch from Spotify (followed artists)
    try:
        if 'spotify' not in enabled_sources:
            logger.warning("[Your Artists] Spotify skipped (disabled in sources config)")
        elif _spotify_client() and _spotify_client().is_spotify_authenticated():
            logger.info("[Your Artists] Fetching followed artists from Spotify...")
            artists = _spotify_client().get_followed_artists()
            for a in artists:
                database.upsert_liked_artist(
                    artist_name=a['name'], source_service='spotify',
                    source_id=a['spotify_id'], source_id_type='spotify',
                    image_url=a.get('image_url'), genres=a.get('genres'),
                    profile_id=profile_id
                )
            fetched += len(artists)
            logger.info(f"[Your Artists] Fetched {len(artists)} from Spotify")
    except Exception as e:
        logger.error(f"[Your Artists] Spotify fetch error: {e}")

    # 2. Fetch from Tidal (favorite artists)
    try:
        if 'tidal' not in enabled_sources:
            logger.warning("[Your Artists] Tidal skipped (disabled in sources config)")
        elif _tidal_client() and hasattr(_tidal_client(), 'get_favorite_artists'):
            tidal_auth = _tidal_client()._ensure_valid_token() if hasattr(_tidal_client(), '_ensure_valid_token') else False
            if tidal_auth:
                logger.info("[Your Artists] Fetching favorite artists from Tidal...")
                artists = _tidal_client().get_favorite_artists(limit=200)
                for a in artists:
                    database.upsert_liked_artist(
                        artist_name=a['name'], source_service='tidal',
                        image_url=a.get('image_url'), profile_id=profile_id
                    )
                fetched += len(artists)
                logger.info(f"[Your Artists] Fetched {len(artists)} from Tidal")
    except Exception as e:
        logger.error(f"[Your Artists] Tidal fetch error: {e}")

    # 3. Fetch from Last.fm (top artists)
    try:
        if 'lastfm' not in enabled_sources:
            logger.warning("[Your Artists] Last.fm skipped (disabled in sources config)")
        else:
            lastfm_key = config_manager.get('lastfm.api_key', '')
            lastfm_secret = config_manager.get('lastfm.api_secret', '')
            lastfm_session = config_manager.get('lastfm.session_key', '')
            logger.info(f"[Your Artists] Last.fm credentials: key={'yes' if lastfm_key else 'NO'}, secret={'yes' if lastfm_secret else 'NO'}, session={'yes' if lastfm_session else 'NO'}")
            if lastfm_key and lastfm_secret and lastfm_session:
                from core.lastfm_client import LastFMClient
                lfm = LastFMClient(api_key=lastfm_key, api_secret=lastfm_secret, session_key=lastfm_session)
                username = lfm.get_authenticated_username()
                logger.info(f"[Your Artists] Last.fm username resolved: {username or 'NONE'}")
                if username:
                    logger.info(f"[Your Artists] Fetching top artists from Last.fm ({username})...")
                    artists = lfm.get_user_top_artists(username, period='overall', limit=200)
                    for a in artists:
                        database.upsert_liked_artist(
                            artist_name=a['name'], source_service='lastfm',
                            image_url=a.get('image_url'), profile_id=profile_id
                        )
                    fetched += len(artists)
                    logger.info(f"[Your Artists] Fetched {len(artists)} from Last.fm")
    except Exception as e:
        logger.error(f"[Your Artists] Last.fm fetch error: {e}")

    # 4. Fetch from Deezer (favorite artists — OAuth or ARL)
    try:
        if 'deezer' not in enabled_sources:
            logger.warning("[Your Artists] Deezer skipped (disabled in sources config)")
        else:
            deezer_cl = _get_deezer_client()
            artists = []
            if deezer_cl and hasattr(deezer_cl, 'is_user_authenticated') and deezer_cl.is_user_authenticated():
                logger.info("[Your Artists] Fetching favorite artists from Deezer (OAuth)...")
                artists = deezer_cl.get_user_favorite_artists(limit=200)
            elif (hasattr(download_orchestrator, 'client') and download_orchestrator.client("deezer_dl")
                  and download_orchestrator.client("deezer_dl").is_authenticated()):
                logger.info("[Your Artists] Fetching favorite artists from Deezer (ARL)...")
                artists = download_orchestrator.client("deezer_dl").get_user_favorite_artists(limit=200)
            for a in artists:
                database.upsert_liked_artist(
                    artist_name=a['name'], source_service='deezer',
                    source_id=a.get('deezer_id'), source_id_type='deezer',
                    image_url=a.get('image_url'), profile_id=profile_id
                )
            fetched += len(artists)
            if artists:
                logger.info(f"[Your Artists] Fetched {len(artists)} from Deezer")
    except Exception as e:
        logger.error(f"[Your Artists] Deezer fetch error: {e}")

    logger.info(f"[Your Artists] Total fetched: {fetched}")

    # 5. Match pending artists to active source
    _match_liked_artists_to_all_sources(database, profile_id)


from core.artists.liked_match import (
    _backfill_liked_artist_images,
    _match_liked_artists_to_all_sources,
)


# ── Your Albums (Liked Albums Pool) ──

@bp.route('/api/discover/your-albums', methods=['GET'])
def get_your_albums():
    """Get liked albums with library ownership status, paginated."""
    try:
        database = get_database()
        profile_id = get_current_profile_id()

        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 48, type=int)
        search = request.args.get('search', '', type=str).strip()
        status_filter = request.args.get('status', 'all', type=str)
        source_filter = request.args.get('source', '', type=str).strip()
        sort = request.args.get('sort', 'artist_name', type=str)

        # Auto-trigger refresh if stale (>24h or empty)
        last_fetch = database.get_liked_albums_last_fetch(profile_id)
        stale = True
        if last_fetch:
            from datetime import datetime, timedelta
            try:
                if isinstance(last_fetch, str):
                    last_dt = datetime.fromisoformat(last_fetch.replace('Z', '+00:00'))
                else:
                    last_dt = last_fetch
                stale = (datetime.now() - last_dt.replace(tzinfo=None)) > timedelta(hours=24)
            except Exception:
                stale = True
        if stale:
            _trigger_your_albums_refresh(profile_id)

        # Fetch all (ownership check requires full set)
        all_result = database.get_liked_albums(
            profile_id=profile_id, page=1, per_page=100000,
            search=search, source_filter=source_filter or None, sort=sort
        )
        all_albums = all_result['albums']

        if not all_albums:
            return jsonify({
                "success": True, "albums": [], "total": 0,
                "page": page, "per_page": per_page, "stale": stale,
                "stats": {"total": 0, "owned": 0, "missing": 0}
            })

        # Ownership check — same strategy as Spotify library endpoint
        library_spotify_ids = database.get_library_spotify_album_ids(profile_id)
        library_album_names = database.get_library_album_names()

        owned_count = 0
        for album in all_albums:
            if album.get('spotify_album_id') and album['spotify_album_id'] in library_spotify_ids:
                album['in_library'] = True
            elif (album['artist_name'].lower(), album['album_name'].lower()) in library_album_names:
                album['in_library'] = True
            else:
                album['in_library'] = False
            if album['in_library']:
                owned_count += 1

        # Apply status filter
        if status_filter == 'missing':
            filtered = [a for a in all_albums if not a['in_library']]
        elif status_filter == 'owned':
            filtered = [a for a in all_albums if a['in_library']]
        else:
            filtered = all_albums

        filtered_total = len(filtered)
        offset = (page - 1) * per_page
        albums = filtered[offset:offset + per_page]

        stats = {
            'total': all_result['total'],
            'owned': owned_count,
            'missing': all_result['total'] - owned_count,
        }

        return jsonify({
            "success": True, "albums": albums,
            "total": filtered_total, "page": page, "per_page": per_page,
            "stale": stale, "stats": stats,
        })
    except Exception as e:
        logger.error(f"Error getting your albums: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/your-albums/refresh', methods=['POST'])
def refresh_your_albums():
    """Force-trigger a fetch cycle for liked albums. ?clear=true wipes pool first."""
    try:
        profile_id = get_current_profile_id()
        if request.args.get('clear', '').lower() == 'true':
            database = get_database()
            cleared = database.clear_liked_albums(profile_id)
            logger.info(f"[Your Albums] Cleared {cleared} entries before refresh")
        _trigger_your_albums_refresh(profile_id)
        return jsonify({"success": True, "message": "Refresh started"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/discover/your-albums/sources', methods=['GET'])
def get_your_albums_sources():
    """Return current source config + which services are connected (albums)."""
    try:
        enabled_raw = config_manager.get('discover.your_albums_sources', 'spotify,tidal,deezer')
        enabled = [s.strip() for s in enabled_raw.split(',') if s.strip()]

        connected = []
        if _spotify_client() and _spotify_client().is_spotify_authenticated():
            connected.append('spotify')
        try:
            if _tidal_client() and hasattr(_tidal_client(), '_ensure_valid_token') and _tidal_client()._ensure_valid_token():
                connected.append('tidal')
        except Exception as e:
            logger.debug("tidal auth check failed: %s", e)
        try:
            deezer_cl = _get_deezer_client()
            deezer_oauth = deezer_cl and hasattr(deezer_cl, 'is_user_authenticated') and deezer_cl.is_user_authenticated()
            deezer_arl = (hasattr(download_orchestrator, 'client') and download_orchestrator.client("deezer_dl")
                          and download_orchestrator.client("deezer_dl").is_authenticated())
            if deezer_oauth or deezer_arl:
                connected.append('deezer')
        except Exception as e:
            logger.debug("deezer auth check failed: %s", e)

        # Discogs: counts as "connected" when a personal access token is
        # configured. Username comes from /oauth/identity at fetch time;
        # not required up front.
        try:
            if config_manager.get('discogs.token', ''):
                connected.append('discogs')
        except Exception as e:
            logger.debug("discogs token check failed: %s", e)

        return jsonify({"success": True, "enabled": enabled, "connected": connected})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


_your_albums_refresh_lock = threading.Lock()
_your_albums_refreshing = False

def _trigger_your_albums_refresh(profile_id: int):
    """Start background album fetch if not already running."""
    global _your_albums_refreshing
    if _your_albums_refreshing:
        return
    with _your_albums_refresh_lock:
        if _your_albums_refreshing:
            return
        _your_albums_refreshing = True

    def _run():
        global _your_albums_refreshing
        try:
            _fetch_liked_albums(profile_id)
        except Exception as e:
            logger.error(f"Your albums refresh failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            _your_albums_refreshing = False

    threading.Thread(target=_run, daemon=True, name="YourAlbumsRefresh").start()


def _fetch_liked_albums(profile_id: int):
    """Background worker: fetch liked/saved albums from all connected services."""
    database = get_database()
    fetched = 0

    enabled_raw = config_manager.get('discover.your_albums_sources', 'spotify,tidal,deezer')
    enabled_sources = {s.strip() for s in enabled_raw.split(',') if s.strip()}

    # 1. Fetch from Spotify (saved albums)
    try:
        if 'spotify' not in enabled_sources:
            logger.warning("[Your Albums] Spotify skipped (disabled in sources config)")
        elif _spotify_client() and _spotify_client().is_spotify_authenticated():
            logger.info("[Your Albums] Fetching saved albums from Spotify...")
            albums = _spotify_client().get_saved_albums()
            for a in albums:
                database.upsert_liked_album(
                    album_name=a['album_name'], artist_name=a['artist_name'],
                    source_service='spotify',
                    source_id=a['spotify_album_id'], source_id_type='spotify',
                    image_url=a.get('image_url'), release_date=a.get('release_date'),
                    total_tracks=a.get('total_tracks', 0), profile_id=profile_id
                )
            fetched += len(albums)
            logger.info(f"[Your Albums] Fetched {len(albums)} from Spotify")
    except Exception as e:
        logger.error(f"[Your Albums] Spotify fetch error: {e}")

    # 2. Fetch from Tidal (favorite albums)
    try:
        if 'tidal' not in enabled_sources:
            logger.warning("[Your Albums] Tidal skipped (disabled in sources config)")
        elif _tidal_client() and hasattr(_tidal_client(), 'get_favorite_albums'):
            tidal_auth = _tidal_client()._ensure_valid_token() if hasattr(_tidal_client(), '_ensure_valid_token') else False
            if tidal_auth:
                logger.info("[Your Albums] Fetching favorite albums from Tidal...")
                albums = _tidal_client().get_favorite_albums(limit=500)
                for a in albums:
                    database.upsert_liked_album(
                        album_name=a['album_name'], artist_name=a['artist_name'],
                        source_service='tidal',
                        source_id=a.get('tidal_id'), source_id_type='tidal',
                        image_url=a.get('image_url'), release_date=a.get('release_date'),
                        total_tracks=a.get('total_tracks', 0), profile_id=profile_id
                    )
                fetched += len(albums)
                logger.info(f"[Your Albums] Fetched {len(albums)} from Tidal")
    except Exception as e:
        logger.error(f"[Your Albums] Tidal fetch error: {e}")

    # 3. Fetch from Deezer (favorite albums — OAuth or ARL)
    try:
        if 'deezer' not in enabled_sources:
            logger.warning("[Your Albums] Deezer skipped (disabled in sources config)")
        else:
            deezer_cl = _get_deezer_client()
            albums = []
            if deezer_cl and hasattr(deezer_cl, 'is_user_authenticated') and deezer_cl.is_user_authenticated():
                logger.info("[Your Albums] Fetching favorite albums from Deezer (OAuth)...")
                albums = deezer_cl.get_user_favorite_albums(limit=500)
            elif (hasattr(download_orchestrator, 'client') and download_orchestrator.client("deezer_dl")
                  and download_orchestrator.client("deezer_dl").is_authenticated()):
                logger.info("[Your Albums] Fetching favorite albums from Deezer (ARL)...")
                albums = download_orchestrator.client("deezer_dl").get_user_favorite_albums(limit=500)
            for a in albums:
                database.upsert_liked_album(
                    album_name=a['album_name'], artist_name=a['artist_name'],
                    source_service='deezer',
                    source_id=a.get('deezer_id'), source_id_type='deezer',
                    image_url=a.get('image_url'), release_date=a.get('release_date'),
                    total_tracks=a.get('total_tracks', 0), profile_id=profile_id
                )
            fetched += len(albums)
            if albums:
                logger.info(f"[Your Albums] Fetched {len(albums)} from Deezer")
    except Exception as e:
        logger.error(f"[Your Albums] Deezer fetch error: {e}")

    # 4. Fetch from Discogs (user's collection) — uses personal access
    # token from `discogs.token` config. Username resolved via the
    # `/oauth/identity` endpoint at fetch time. Discogs is physical-
    # media-first so many releases won't have streaming equivalents,
    # but the click-context dispatch in the frontend opens the Discogs
    # release detail and the user can manually trigger a download
    # search if a digital match exists.
    try:
        if 'discogs' not in enabled_sources:
            logger.warning("[Your Albums] Discogs skipped (disabled in sources config)")
        elif not config_manager.get('discogs.token', ''):
            logger.info("[Your Albums] Discogs skipped (no token configured)")
        else:
            from core.discogs_client import DiscogsClient
            discogs_cl = DiscogsClient()
            if discogs_cl.is_authenticated():
                logger.info("[Your Albums] Fetching collection from Discogs...")
                releases = discogs_cl.get_user_collection()
                from core.discogs_client import _tag_discogs_album_id
                for r in releases:
                    database.upsert_liked_album(
                        album_name=r['album_name'], artist_name=r['artist_name'],
                        source_service='discogs',
                        # Collection items are always releases — store the ID tagged
                        # ('r<id>') to match search/discography (#848), so every stored
                        # Discogs album ID is uniform and re-fetches route correctly.
                        source_id=_tag_discogs_album_id(r['release_id'], 'release'), source_id_type='discogs',
                        image_url=r.get('image_url'), release_date=r.get('release_date', ''),
                        total_tracks=r.get('total_tracks', 0), profile_id=profile_id
                    )
                fetched += len(releases)
                if releases:
                    logger.info(f"[Your Albums] Fetched {len(releases)} from Discogs")
    except Exception as e:
        logger.error(f"[Your Albums] Discogs fetch error: {e}")

    logger.info(f"[Your Albums] Total fetched: {fetched}")


@bp.route('/api/discover/your-artists/info/<artist_id>', methods=['GET'])
def get_your_artist_info(artist_id):
    """Get artist info for the Your Artists info modal. Checks library, cache, then API."""
    try:
        artist_name = request.args.get('name', '')
        result = {'name': artist_name, 'success': True}

        # 1. Try library DB (has enrichment data)
        try:
            database = get_database()
            conn = database._get_connection()
            cursor = conn.cursor()
            # Check by various ID columns
            cursor.execute("""
                SELECT * FROM lib2_artists WHERE id = ? OR spotify_id = ?
                   OR json_extract(external_ids, '$.itunes') = ?
                   OR json_extract(external_ids, '$.deezer') = ?
                   OR json_extract(external_ids, '$.discogs') = ? LIMIT 1
            """, (artist_id, artist_id, artist_id, artist_id, artist_id))
            row = cursor.fetchone()
            if row:
                r = dict(row)
                from core.library2.provider_ids import parse_external_ids
                ids = parse_external_ids(r.get('external_ids'))
                try:
                    lastfm = (json.loads(r.get('enrichment') or '{}').get('lastfm') or {})
                except (TypeError, ValueError):
                    lastfm = {}
                result.update({
                    'name': r.get('name', artist_name),
                    'genres': json.loads(r['genres']) if r.get('genres') else [],
                    'summary': r.get('summary', ''),
                    'image_url': r.get('image_url', ''),
                    'spotify_artist_id': r.get('spotify_id'),
                    'musicbrainz_id': r.get('musicbrainz_id'),
                    'deezer_id': ids.get('deezer'),
                    'itunes_artist_id': ids.get('itunes'),
                    'discogs_id': ids.get('discogs'),
                    'lastfm_url': ids.get('lastfm') or lastfm.get('url'),
                    'tidal_id': ids.get('tidal'),
                    'lastfm_listeners': lastfm.get('listeners', 0),
                    'lastfm_playcount': lastfm.get('playcount', 0),
                })
                return jsonify(result)
        except Exception as e:
            logger.debug("library artist lookup failed: %s", e)

        # 2. Try metadata cache
        try:
            conn = database._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT raw_json, image_url FROM metadata_cache_entities
                WHERE entity_type = 'artist' AND entity_id = ? LIMIT 1
            """, (artist_id,))
            row = cursor.fetchone()
            if row and row['raw_json']:
                cached = json.loads(row['raw_json'])
                result.update({
                    'name': cached.get('name', artist_name),
                    'genres': cached.get('genres', []),
                    'image_url': row['image_url'] or cached.get('image_url', ''),
                    'popularity': cached.get('popularity', 0),
                    'followers': cached.get('followers', {}).get('total', 0) if isinstance(cached.get('followers'), dict) else cached.get('followers', 0),
                })
                return jsonify(result)
        except Exception as e:
            logger.debug("metadata cache lookup failed: %s", e)

        # 3. Try Spotify API directly (genres, image, followers)
        try:
            if _spotify_client() and _spotify_client().is_spotify_authenticated() and not artist_id.isdigit():
                from core.api_call_tracker import api_call_tracker
                api_call_tracker.record_call('spotify', endpoint='artist')
                artist_data = _spotify_client().sp.artist(artist_id)
                if artist_data:
                    result.update({
                        'name': artist_data.get('name', artist_name),
                        'genres': artist_data.get('genres', []),
                        'image_url': artist_data['images'][0]['url'] if artist_data.get('images') else '',
                        'spotify_artist_id': artist_data.get('id'),
                        'popularity': artist_data.get('popularity', 0),
                        'followers': artist_data.get('followers', {}).get('total', 0),
                    })
        except Exception as e:
            logger.debug(f"Spotify artist lookup failed for {artist_id}: {e}")

        # 4. Last.fm: bio, listeners, playcount (skip if name is too short/generic)
        try:
            _lfm_name = result.get('name') or artist_name
            if _lfm_name and len(_lfm_name) > 1 and _lastfm_worker() and _lastfm_worker().client:
                lfm_info = _lastfm_worker().client.get_artist_info(_lfm_name)
                if lfm_info:
                    bio = lfm_info.get('bio', {})
                    if isinstance(bio, dict):
                        summary = bio.get('summary', '')
                    else:
                        summary = str(bio) if bio else ''
                    if summary and not result.get('summary'):
                        result['summary'] = summary
                    stats = lfm_info.get('stats', {})
                    if stats:
                        result['lastfm_listeners'] = int(stats.get('listeners', 0))
                        result['lastfm_playcount'] = int(stats.get('playcount', 0))
                    if not result.get('genres'):
                        tags = lfm_info.get('tags', {}).get('tag', [])
                        if tags:
                            result['genres'] = [t.get('name', '') for t in tags[:5] if isinstance(t, dict)]
                    lfm_url = lfm_info.get('url')
                    if lfm_url:
                        result['lastfm_url'] = lfm_url
        except Exception as e:
            logger.debug(f"Last.fm artist info failed for {artist_name}: {e}")

        # 5. Return combined info
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/image-proxy', methods=['GET'])
def image_proxy():
    """Proxy external images to avoid CORS issues for canvas rendering.

    Kept for backwards compatibility; new normalized artwork URLs use
    /api/image-cache/<key>, but older browser sessions may still hold this
    query-string form.
    """
    url = request.args.get('url', '')
    if not url or not url.startswith('http'):
        return '', 400

    try:
        from core.image_cache import get_image_cache

        cached = get_image_cache().get_url(url)
        response = send_file(cached.path, mimetype=cached.mime_type, conditional=True)
        max_age = int(config_manager.get("image_cache.ttl_seconds", 2592000))
        response.headers['Cache-Control'] = f'private, max-age={max_age}'
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['X-SoulSync-Image-Cache'] = cached.status
        return response
    except Exception as exc:
        logger.debug("image proxy failed: %s", exc)
        return '', 502


@bp.route('/api/image-cache/status', methods=['GET'])
def image_cache_status():
    """What the artwork cache is holding, for Settings -> Advanced."""
    try:
        from core.image_cache import get_image_cache, thumbnails_enabled

        stats = get_image_cache().stats()
        stats['enabled'] = config_manager.get('image_cache.enabled', True) is not False
        stats['thumbnails'] = thumbnails_enabled()
        return jsonify({'success': True, **stats})
    except Exception as e:
        logger.error("image cache status failed: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/image-cache/clear', methods=['POST'])
def image_cache_clear():
    """Empty the artwork cache. Everything in it is re-fetchable, so this is
    only ever a temporary cost — nothing user-created lives here."""
    try:
        from core.image_cache import get_image_cache

        return jsonify({'success': True, **get_image_cache().clear()})
    except Exception as e:
        logger.error("image cache clear failed: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/image-cache/prune', methods=['POST'])
def image_cache_prune():
    """Apply the TTL and size cap now, rather than waiting for the next store."""
    try:
        from core.image_cache import get_image_cache

        return jsonify({'success': True, **get_image_cache().prune()})
    except Exception as e:
        logger.error("image cache prune failed: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/image-cache/<cache_key>', methods=['GET'])
def serve_cached_image(cache_key):
    """Serve a registered image URL from SoulSync's disk cache."""
    if not re.fullmatch(r'[a-f0-9]{64}', cache_key or ''):
        return '', 404

    try:
        from core.image_cache import get_image_cache, thumbnails_enabled

        # ?v=grid|card|hero asks for a resized copy. The BROWSER picks the size,
        # so a page adopts thumbnails by adding one query param — no need to
        # rewrite every URL-producing call site, and a page that asks for
        # nothing keeps getting the original.
        variant = (request.args.get('v') or '').strip()
        cache = get_image_cache()
        if variant and thumbnails_enabled():
            cached = cache.get_variant_of(cache_key, variant)
        else:
            cached = cache.get(cache_key)
        response = send_file(cached.path, mimetype=cached.mime_type, conditional=True)
        max_age = int(config_manager.get("image_cache.ttl_seconds", 2592000))
        response.headers['Cache-Control'] = f'private, max-age={max_age}'
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['X-SoulSync-Image-Cache'] = cached.status
        return response
    except Exception as exc:
        # An empty 404 made every distinct failure look identical from the
        # browser — "key not found", "upstream refused", "host unreachable" and
        # "not an image" were indistinguishable, so a production report could
        # only say "218 images 404" without saying why. The reason travels in a
        # header (secrets redacted) rather than a body so nothing about the
        # response contract changes for the <img> that requested it.
        from core.metadata.artwork import _redact_url_secrets
        reason = ' '.join(_redact_url_secrets(str(exc)).split())[:200] \
            or exc.__class__.__name__
        logger.debug("cached image serve failed for %s: %s", cache_key, reason)
        response = Response('', status=404)
        response.headers['X-SoulSync-Image-Error'] = reason
        response.headers['Cache-Control'] = 'no-store'
        return response


from core.artists.map import (
    _artmap_cache_invalidate,
    _artmap_cache_get,
    _artmap_cache_set,
    get_artist_map_data as _artists_map_get_artist_map_data,
    get_artist_map_genre_list as _artists_map_get_artist_map_genre_list,
    get_artist_map_genres as _artists_map_get_artist_map_genres,
    get_artist_map_explore as _artists_map_get_artist_map_explore,
)


@bp.route('/api/discover/artist-map', methods=['GET'])
def get_artist_map_data():
    return _artists_map_get_artist_map_data()


@bp.route('/api/discover/artist-map/genre-list', methods=['GET'])
def get_artist_map_genre_list():
    return _artists_map_get_artist_map_genre_list()


@bp.route('/api/discover/artist-map/genres', methods=['GET'])
def get_artist_map_genres():
    return _artists_map_get_artist_map_genres()


@bp.route('/api/discover/artist-map/explore', methods=['GET'])
def get_artist_map_explore():
    return _artists_map_get_artist_map_explore()


@bp.route('/api/discover/artist-map/perf', methods=['POST'])
def log_artist_map_perf():
    """Debug sink: the artist-map frontend POSTs its render timings here (toggled
    with 'd' on the map) so they land in app.log — the on-canvas overlay text
    can't be copied. Used to find the real drag/zoom bottleneck."""
    try:
        data = request.get_json(silent=True) or {}
        logger.info("[ARTMAP-PERF] %s", json.dumps(data, ensure_ascii=False))
    except Exception as e:
        logger.debug("artist-map perf log failed: %s", e)
    return ('', 204)


@bp.route('/api/discover/build-playlist/search-artists', methods=['GET'])
def search_artists_for_playlist():
    """Search for artists to use as seeds for custom playlist building"""
    try:
        query = request.args.get('query', '').strip()
        if not query:
            return jsonify({"success": False, "error": "Query required"}), 400

        artists = []
        if _is_hydrabase_active():
            artist_objs = _hydrabase_client().search_artists(query, limit=10)
            for artist in artist_objs:
                artists.append({
                    'id': artist.id,
                    'name': artist.name,
                    'image_url': artist.image_url
                })
        else:
            if _hydrabase_worker() and _dev_mode_enabled():
                _hydrabase_worker().enqueue(query, 'artists')

            # Try Spotify first, fall back to iTunes
            if _spotify_client().sp and not _spotify_rate_limited():
                try:
                    artist_results = _spotify_client().search_artists(query, limit=10)
                    for artist in artist_results:
                        artists.append({
                            'id': artist.id,
                            'name': artist.name,
                            'image_url': artist.image_url
                        })
                except Exception as e:
                    logger.warning(f"Spotify artist search failed, falling back to iTunes: {e}")

            if not artists:
                fallback = _get_metadata_fallback_client()
                artist_objs = fallback.search_artists(query, limit=10)
                for artist in artist_objs:
                    # Fallback artist search may not return images — grab from album art
                    image = artist.image_url
                    if not image:
                        image = fallback._get_artist_image_from_albums(artist.id)
                    artists.append({
                        'id': artist.id,
                        'name': artist.name,
                        'image_url': image
                    })

            if artists:
                # Re-rank: boost exact name matches to the top
                query_lower = query.lower().strip()
                artists.sort(key=lambda a: (0 if a['name'].lower().strip() == query_lower else 1))

        return jsonify({
            "success": True,
            "artists": artists
        })

    except Exception as e:
        logger.error(f"Error searching for artists: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/build-playlist/generate', methods=['POST'])
def generate_custom_playlist():
    """Generate custom playlist from seed artists"""
    try:
        from core.personalized_playlists import get_personalized_playlists_service

        data = request.get_json()
        seed_artist_ids = data.get('seed_artist_ids', [])

        if not seed_artist_ids or len(seed_artist_ids) < 1 or len(seed_artist_ids) > 5:
            return jsonify({
                "success": False,
                "error": "Please provide between 1 and 5 seed artists"
            }), 400

        database = get_database()
        service = get_personalized_playlists_service(database, _spotify_client())

        playlist_size = int(data.get('playlist_size', 50))
        result = service.build_custom_playlist(seed_artist_ids, playlist_size=playlist_size)

        if result.get('error') and not result.get('tracks'):
            return jsonify({"success": False, "error": result['error']}), 400

        return jsonify({
            "success": True,
            "playlist": result
        })

    except Exception as e:
        logger.error(f"Error generating custom playlist: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/decades/available', methods=['GET'])
@_discover_shelf_cache()
def get_available_decades():
    """Get list of decades that have content in discovery pool"""
    try:
        database = get_database()

        with database._get_connection() as conn:
            cursor = conn.cursor()

            # Get distinct decades from discovery pool
            cursor.execute("""
                SELECT DISTINCT
                    (CAST(SUBSTR(release_date, 1, 4) AS INTEGER) / 10) * 10 as decade,
                    COUNT(*) as track_count
                FROM discovery_pool
                WHERE release_date IS NOT NULL
                  AND CAST(SUBSTR(release_date, 1, 4) AS INTEGER) >= 1950
                  AND CAST(SUBSTR(release_date, 1, 4) AS INTEGER) <= 2029
                GROUP BY decade
                HAVING track_count >= 10
                ORDER BY decade ASC
            """)

            rows = cursor.fetchall()
            decades = []
            for row in rows:
                decades.append({
                    'year': row[0],
                    'track_count': row[1]
                })

            return jsonify({
                "success": True,
                "decades": decades
            })

    except Exception as e:
        logger.error(f"Error getting available decades: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/decade/<int:decade>', methods=['GET'])
def get_discover_decade_playlist(decade):
    """Get tracks from a specific decade for discovery page"""
    try:
        from core.personalized_playlists import get_personalized_playlists_service

        database = get_database()
        service = get_personalized_playlists_service(database, _spotify_client())

        tracks = service.get_decade_playlist(decade, limit=50)

        if not tracks:
            return jsonify({
                "success": True,
                "tracks": [],
                "decade": decade,
                "message": f"No tracks found for the {decade}s"
            }), 200

        # Convert to Spotify format for modal compatibility
        spotify_tracks = []
        for track in tracks:
            spotify_tracks.append({
                'id': track.get('spotify_track_id', track.get('id')),
                'name': track.get('track_name', track.get('name')),
                'artists': [track.get('artist_name', 'Unknown')],
                'album': {
                    'name': track.get('album_name', 'Unknown'),
                    'images': [{'url': track.get('album_cover_url')}] if track.get('album_cover_url') else []
                },
                'duration_ms': track.get('duration_ms', 0)
            })

        return jsonify({
            "success": True,
            "tracks": spotify_tracks,
            "decade": decade
        })

    except Exception as e:
        logger.error(f"Error getting decade playlist: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/genres/available', methods=['GET'])
def get_available_genres():
    """Get list of genres that have content in discovery pool"""
    try:
        from core.personalized_playlists import get_personalized_playlists_service

        database = get_database()
        service = get_personalized_playlists_service(database, _spotify_client())

        genres = service.get_available_genres()

        return jsonify({
            "success": True,
            "genres": genres
        })

    except Exception as e:
        logger.error(f"Error getting available genres: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/discover/genre/<path:genre_name>', methods=['GET'])
def get_discover_genre_playlist(genre_name):
    """Get tracks from a specific genre for discovery page"""
    try:
        from core.personalized_playlists import get_personalized_playlists_service

        database = get_database()
        service = get_personalized_playlists_service(database, _spotify_client())

        tracks = service.get_genre_playlist(genre_name, limit=50)

        if not tracks:
            return jsonify({
                "success": True,
                "tracks": [],
                "genre": genre_name,
                "message": f"No tracks found for {genre_name}"
            }), 200

        # Convert to Spotify format for modal compatibility
        spotify_tracks = []
        for track in tracks:
            spotify_tracks.append({
                'id': track.get('spotify_track_id', track.get('id')),
                'name': track.get('track_name', track.get('name')),
                'artists': [track.get('artist_name', 'Unknown')],
                'album': {
                    'name': track.get('album_name', 'Unknown'),
                    'images': [{'url': track.get('album_cover_url')}] if track.get('album_cover_url') else []
                },
                'duration_ms': track.get('duration_ms', 0)
            })

        return jsonify({
            "success": True,
            "tracks": spotify_tracks,
            "genre": genre_name
        })

    except Exception as e:
        logger.error(f"Error getting genre playlist: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
