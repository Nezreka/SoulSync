"""Artist-watchlist endpoints, lifted out of web_server.py.

The full /api/watchlist surface: listing, add/remove, the scan machinery's
start/stop/status, per-artist scan results and the settings that drive the
automatic timer. The scanner itself comes from core.watchlist_scanner (local
imports inside the handlers, as before); what web_server injects here are its
boot singletons - config/db/socketio, the timer lock, and getters for the two
objects that can be rebound after boot (the Spotify client on reconnect, the
automation engine).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from flask import Blueprint, Response, jsonify, request

from core.api_validation import parse_strict_bool, parse_strict_id, parse_strict_int
from core.artist_source_lookup import (
    sources_resolvable_in_library as _core_sources_resolvable_in_library,
)
from core.artists.map import _artmap_cache_invalidate
from core.library2.provider_ids import ARTIST_IDS_SQL as _ARTIST_IDS_SQL
from core.metadata.registry import get_spotify_client
from utils.logging_config import get_logger

logger = get_logger("api.artist_watchlist")

bp = Blueprint("artist_watchlist", __name__)

# Injected by configure() at boot.
config_manager = None
get_database = None
socketio = None
watchlist_timer_lock = None
_get_deezer_client = None
_spotify_client = lambda: None      # noqa: E731 - getter; rebound on reconnect
_automation_engine = lambda: None   # noqa: E731 - getter


# ...web_server-owned helpers the handlers call (injected; importing web_server
# from here would be circular). All are module-level defs bound once at boot.
get_current_profile_id = None
_get_discogs_client = None
_get_metadata_fallback_client = None
_get_metadata_fallback_source = None
_get_active_discovery_source = None
_pause_enrichment_workers = None
_resume_enrichment_workers = None
is_watchlist_actually_scanning = None
_owned_mirrored_playlist = None
_build_watchlist_count_payload = None


def configure(*, config, database_getter, socketio_obj, timer_lock,
              deezer_client_getter, spotify_client_getter, automation_engine_getter,
              current_profile_id, discogs_client, metadata_fallback_client,
              metadata_fallback_source, active_discovery_source,
              pause_enrichment, resume_enrichment, watchlist_scanning,
              owned_mirrored_playlist, watchlist_count_payload):
    global config_manager, get_database, socketio, watchlist_timer_lock
    global _get_deezer_client, _spotify_client, _automation_engine
    global get_current_profile_id, _get_discogs_client, _get_metadata_fallback_client
    global _get_metadata_fallback_source, _get_active_discovery_source
    global _pause_enrichment_workers, _resume_enrichment_workers
    global is_watchlist_actually_scanning, _owned_mirrored_playlist, _build_watchlist_count_payload
    config_manager = config
    get_database = database_getter
    socketio = socketio_obj
    watchlist_timer_lock = timer_lock
    _get_deezer_client = deezer_client_getter
    _spotify_client = spotify_client_getter
    _automation_engine = automation_engine_getter
    get_current_profile_id = current_profile_id
    _get_discogs_client = discogs_client
    _get_metadata_fallback_client = metadata_fallback_client
    _get_metadata_fallback_source = metadata_fallback_source
    _get_active_discovery_source = active_discovery_source
    _pause_enrichment_workers = pause_enrichment
    _resume_enrichment_workers = resume_enrichment
    is_watchlist_actually_scanning = watchlist_scanning
    _owned_mirrored_playlist = owned_mirrored_playlist
    _build_watchlist_count_payload = watchlist_count_payload


def create_blueprint():
    return bp


# --- Watchlist API Endpoints ---

@bp.route('/api/watchlist/count', methods=['GET'])
def get_watchlist_count():
    """Get the number of artists in the watchlist"""
    try:
        database = get_database()
        count = database.get_watchlist_count(profile_id=get_current_profile_id())

        # Calculate time until next auto-scanning
        next_run_in_seconds = _automation_engine().get_system_automation_next_run_seconds('scan_watchlist') if _automation_engine() else 0

        return jsonify({
            "success": True,
            "count": count,
            "next_run_in_seconds": next_run_in_seconds
        })
    except Exception as e:
        logger.error(f"Error getting watchlist count: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/recent-releases', methods=['GET'])
def get_watchlist_recent_releases():
    """Newest releases across the whole watchlist, flat (the dashboard rail).

    recent_releases was only ever queried per-artist before this; the dashboard
    wants one newest-first list. Rows come straight off the table the watchlist
    scan already fills — no provider calls, so this is cheap enough for a
    dashboard mount fetch.
    """
    try:
        limit = min(50, max(1, int(request.args.get('limit', 20))))
        database = get_database()
        releases = database.get_watchlist_recent_releases(
            limit=limit, profile_id=get_current_profile_id())
        return jsonify({"success": True, "releases": releases})
    except Exception as e:
        logger.error(f"Error getting watchlist recent releases: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/artists', methods=['GET'])
def get_watchlist_artists():
    """Get all artists in the watchlist with cached images"""
    try:
        database = get_database()
        database.backfill_watchlist_musicbrainz_ids_from_library(profile_id=get_current_profile_id())
        watchlist_artists = database.get_watchlist_artists(profile_id=get_current_profile_id())

        # Convert to JSON serializable format (images are cached from watchlist scans)
        artists_data = []
        for artist in watchlist_artists:
            artists_data.append({
                "id": artist.id,
                "spotify_artist_id": artist.spotify_artist_id,
                "artist_name": artist.artist_name,
                "date_added": artist.date_added.isoformat() if artist.date_added else None,
                "last_scan_timestamp": artist.last_scan_timestamp.isoformat() if artist.last_scan_timestamp else None,
                "created_at": artist.created_at.isoformat() if artist.created_at else None,
                "updated_at": artist.updated_at.isoformat() if artist.updated_at else None,
                "image_url": artist.image_url,  # Cached during watchlist scans
                "itunes_artist_id": artist.itunes_artist_id,  # For iTunes-only artists
                "deezer_artist_id": getattr(artist, 'deezer_artist_id', None),
                "discogs_artist_id": getattr(artist, 'discogs_artist_id', None),
                "musicbrainz_artist_id": getattr(artist, 'musicbrainz_artist_id', None),
                "amazon_artist_id": getattr(artist, 'amazon_artist_id', None),
                "include_albums": artist.include_albums,
                "include_eps": artist.include_eps,
                "include_singles": artist.include_singles,
                "include_live": artist.include_live,
                "include_remixes": artist.include_remixes,
                "include_acoustic": artist.include_acoustic,
                "include_compilations": artist.include_compilations,
            })

        return jsonify({"success": True, "artists": artists_data})
    except Exception as e:
        logger.error(f"Error getting watchlist artists: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/watchlist/export', methods=['GET'])
def export_watchlist():
    """Export the watchlist roster (name + source IDs, optionally external links)
    as json / csv / txt. Returns the content for the export modal to preview +
    download (X-Export-Count / X-Export-Ext headers carry the metadata)."""
    try:
        from core.exports.artist_export import build_artist_export, export_mime_and_ext
        fmt = (request.args.get('format', 'json') or 'json').lower()
        include_links = request.args.get('links', '') in ('1', 'true', 'yes')
        database = get_database()
        artists = [{
            'artist_name': a.artist_name,
            'spotify_artist_id': a.spotify_artist_id,
            'itunes_artist_id': a.itunes_artist_id,
            'deezer_artist_id': getattr(a, 'deezer_artist_id', None),
            'discogs_artist_id': getattr(a, 'discogs_artist_id', None),
            'musicbrainz_artist_id': getattr(a, 'musicbrainz_artist_id', None),
            'amazon_artist_id': getattr(a, 'amazon_artist_id', None),
        } for a in database.get_watchlist_artists(profile_id=get_current_profile_id())]
        content = build_artist_export(artists, fmt=fmt, include_links=include_links)
        mime, ext = export_mime_and_ext(fmt)
        return Response(content, mimetype=mime,
                        headers={'X-Export-Count': str(len(artists)), 'X-Export-Ext': ext})
    except Exception as e:
        logger.error(f"Watchlist export failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ── Playlist export to ListenBrainz / JSPF (#903) ────────────────────────────
# Resolve each mirrored-playlist track to a MusicBrainz recording MBID (cache -> DB ->
# file tag -> live MB), build a JSPF, and either hand it back as a download or create the
# playlist directly on ListenBrainz. Runs in a background thread with live status the
# mirrored-playlist card polls. Entirely additive — new routes + an in-memory job registry.
_playlist_export_jobs = {}
_playlist_export_jobs_lock = threading.Lock()


def _build_service_search_id_fn(service):
    """Bind a confident-search ID resolver to the service's metadata search client, for the
    opt-in export backfill (#945). Returns None when the search client isn't available — the
    backfill then simply finds nothing for that track rather than crashing the export."""
    from core.exports.export_sources import search_service_track_id
    try:
        if service == 'spotify':
            client = get_spotify_client()
            if not client:
                return None
            # allow_fallback=False is CRITICAL: Spotify's search falls back to iTunes/Deezer
            # when rate-limited/free, returning tracks whose .id is NOT a Spotify id — backfill
            # would then push wrong ids into the Spotify playlist. Disable it: real Spotify hits
            # or nothing.
            search_fn = lambda q: client.search_tracks(q, limit=8, allow_fallback=False)  # noqa: E731
        else:
            from core.metadata.registry import get_deezer_client
            client = get_deezer_client()
            if not client:
                return None
            # Deezer's search stays within Deezer (query-only fallback), so its .id is always a
            # Deezer id — no cross-service guard needed.
            search_fn = lambda q: client.search_tracks(q, limit=8)  # noqa: E731
        return lambda artist, title: search_service_track_id(artist, title, search_fn=search_fn)
    except Exception:
        return None


def _run_service_export(job, db, playlist_id, title, service, client, resolve_ids_fn=None):
    """Export a mirrored playlist back to a streaming service (Spotify/Deezer, #945).

    Resolves each track to a target-service ID via the discovery cache (the track's
    extra_data — free + already matched) → the library's stored id → (when job['backfill']
    is set) a confident live-search match, pushes the matched set via the service write
    client, and stores the returned playlist id so a re-export updates in place (idempotent,
    like the LB #903 fix). Deps injected so the orchestration is unit-testable without a DB
    or live service.
    """
    from core.exports.export_sources import resolve_service_track_ids
    resolve_ids_fn = resolve_ids_fn or resolve_service_track_ids

    tracks = db.get_mirrored_playlist_tracks(int(playlist_id))
    job['total'] = len(tracks)
    job['phase'] = 'resolving'

    def on_progress(done, total, stats):
        job['done'] = done
        job['stats'] = dict(stats)

    # Opt-in backfill: confident live-search for tracks the cache + library couldn't resolve.
    search_id_fn = _build_service_search_id_fn(service) if job.get('backfill') else None
    out = resolve_ids_fn(tracks, service, search_id_fn=search_id_fn, on_progress=on_progress)
    job['stats'] = out['stats']
    ids = [r['service_track_id'] for r in out['resolved'] if r.get('service_track_id')]
    if not ids:
        job['phase'] = 'error'
        job['error'] = f"No tracks matched a {service.title()} ID — nothing to export"
        return
    if client is None:
        job['phase'] = 'error'
        job['error'] = f"{service.title()} is not connected"
        return

    job['phase'] = 'pushing'
    # Re-export updates the same service playlist in place (idempotent), mirroring the
    # ListenBrainz #903 fix — the target ID is stored per (playlist, service).
    existing = db.get_playlist_export_target(int(playlist_id), service)
    res = client.create_or_update_playlist(title, ids, existing_id=existing)
    job['push'] = res
    if res.get('success'):
        if res.get('playlist_id'):
            db.set_playlist_export_target(int(playlist_id), service, str(res['playlist_id']))
        job['phase'] = 'done'
    else:
        job['phase'] = 'error'
        job['error'] = res.get('error') or f"{service.title()} push failed"


def _run_playlist_export(job_id, playlist_id, title, mode):
    job = _playlist_export_jobs[job_id]
    try:
        # Service export (#945) — resolve to Spotify/Deezer track IDs and push.
        if mode in ('spotify', 'deezer'):
            db = get_database()
            if mode == 'spotify':
                client = get_spotify_client()
            else:
                from core.deezer_download_client import DeezerDownloadClient
                client = DeezerDownloadClient()
            _run_service_export(job, db, playlist_id, title, mode, client)
            return

        from core.exports.export_sources import build_resolve_fn
        from core.exports.playlist_export import resolve_playlist_tracks
        from core.exports.jspf_export import build_jspf

        db = get_database()
        tracks = db.get_mirrored_playlist_tracks(int(playlist_id))
        job['total'] = len(tracks)
        job['phase'] = 'resolving'

        resolve_fn = build_resolve_fn()

        def on_progress(done, total, stats):
            job['done'] = done
            job['stats'] = dict(stats)

        out = resolve_playlist_tracks(tracks, resolve_fn, on_progress=on_progress)
        jspf, summary = build_jspf(title, out['resolved'], creator='SoulSync')
        job['summary'] = summary
        job['jspf'] = jspf
        job['stats'] = out['stats']

        if mode == 'push':
            job['phase'] = 'pushing'
            from core.listenbrainz_client import ListenBrainzClient
            client = ListenBrainzClient()
            # Re-export updates the same LB playlist in place instead of duplicating it (#903).
            existing = db.get_playlist_export_target(int(playlist_id), 'listenbrainz')
            res = client.create_or_update_playlist(title, jspf['playlist']['track'], existing_mbid=existing)
            job['push'] = res
            if res.get('success'):
                if res.get('playlist_mbid'):
                    db.set_playlist_export_target(int(playlist_id), 'listenbrainz', res['playlist_mbid'])
                job['phase'] = 'done'
            else:
                job['phase'] = 'error'
                job['error'] = res.get('error') or 'ListenBrainz push failed'
        else:
            job['phase'] = 'done'
    except Exception as e:
        logger.error(f"[Playlist Export] job {job_id} failed: {e}")
        job['phase'] = 'error'
        job['error'] = str(e)


@bp.route('/api/playlists/<playlist_id>/export/listenbrainz', methods=['POST'])
def start_playlist_export_listenbrainz(playlist_id):
    """Start a background export of a mirrored playlist to ListenBrainz/JSPF.

    Body: {"mode": "download"|"push"} (default "download"). Returns {job_id} to poll."""
    try:
        body = request.get_json(silent=True) or {}
        mode = 'push' if body.get('mode') == 'push' else 'download'
        db = get_database()
        meta = _owned_mirrored_playlist(db, int(playlist_id))
        if not meta:
            return jsonify({"success": False, "error": "Playlist not found"}), 404
        title = (meta.get('name') or meta.get('title') or 'SoulSync Export').strip() or 'SoulSync Export'

        import uuid
        job_id = uuid.uuid4().hex
        with _playlist_export_jobs_lock:
            _playlist_export_jobs[job_id] = {
                'job_id': job_id, 'playlist_id': str(playlist_id), 'title': title,
                'mode': mode, 'phase': 'starting', 'done': 0, 'total': 0,
                'stats': {}, 'error': None,
            }
        t = threading.Thread(target=_run_playlist_export, args=(job_id, playlist_id, title, mode), daemon=True)
        t.start()
        return jsonify({"success": True, "job_id": job_id})
    except Exception as e:
        logger.error(f"Playlist export start failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/playlists/<playlist_id>/export/service/<service>', methods=['POST'])
def start_playlist_export_service(playlist_id, service):
    """Export a mirrored playlist back to a streaming service (#945).

    ``service`` ∈ {spotify, deezer}. Creates a service-owned playlist (or updates the
    one a prior export created) from the library tracks' stored service IDs. Returns
    {job_id} to poll via the shared export-status endpoint."""
    try:
        service = (service or '').lower()
        if service not in ('spotify', 'deezer'):
            return jsonify({"success": False, "error": f"Unsupported export target: {service}"}), 400
        # Spotify export needs the write scope, which the normal login doesn't request. If the
        # current token doesn't carry it, tell the UI to send the user through the one-time
        # on-demand export-auth (instead of starting a job that would 403). #945.
        if service == 'spotify' and not (_spotify_client() and _spotify_client().has_write_scope()):
            return jsonify({
                "success": False, "needs_auth": True,
                "auth_url": "/auth/spotify/export",
                "error": "Spotify needs permission to create playlists",
            }), 200
        body = request.get_json(silent=True) or {}
        backfill = bool(body.get('backfill'))
        db = get_database()
        meta = _owned_mirrored_playlist(db, int(playlist_id))
        if not meta:
            return jsonify({"success": False, "error": "Playlist not found"}), 404
        title = (meta.get('name') or meta.get('title') or 'SoulSync Export').strip() or 'SoulSync Export'

        import uuid
        job_id = uuid.uuid4().hex
        with _playlist_export_jobs_lock:
            _playlist_export_jobs[job_id] = {
                'job_id': job_id, 'playlist_id': str(playlist_id), 'title': title,
                'mode': service, 'backfill': backfill, 'phase': 'starting', 'done': 0,
                'total': 0, 'stats': {}, 'error': None,
            }
        t = threading.Thread(target=_run_playlist_export,
                             args=(job_id, playlist_id, title, service), daemon=True)
        t.start()
        return jsonify({"success": True, "job_id": job_id})
    except Exception as e:
        logger.error(f"Service playlist export start failed ({service}): {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/playlists/export/status/<job_id>', methods=['GET'])
def playlist_export_status(job_id):
    """Live status for an export job (polled by the mirrored-playlist card)."""
    job = _playlist_export_jobs.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "unknown job"}), 404
    # Don't ship the full JSPF on every poll — just status + coverage.
    out = {k: v for k, v in job.items() if k != 'jspf'}
    out['has_download'] = bool(job.get('jspf'))
    return jsonify({"success": True, "job": out})


@bp.route('/api/playlists/export/download/<job_id>', methods=['GET'])
def playlist_export_download(job_id):
    """Download a completed export's JSPF file."""
    job = _playlist_export_jobs.get(job_id)
    if not job or not job.get('jspf'):
        return jsonify({"success": False, "error": "no export available"}), 404
    import json as _json
    safe = re.sub(r'[^\w\-]+', '_', (job.get('title') or 'playlist')).strip('_') or 'playlist'
    return Response(
        _json.dumps(job['jspf'], indent=2, ensure_ascii=False),
        mimetype='application/jspf+json',
        headers={'Content-Disposition': f'attachment; filename="{safe}.jspf"'},
    )


@bp.route('/api/library/artists/export', methods=['GET'])
def export_library_artists():
    """Export the WHOLE library artist roster (name + every source id/url we have,
    optional external links, optional owned album/track counts) as json/csv/txt."""
    try:
        from core.exports.artist_export import build_artist_export, export_mime_and_ext
        fmt = (request.args.get('format', 'json') or 'json').lower()
        include_links = request.args.get('links', '') in ('1', 'true', 'yes')
        include_contents = request.args.get('contents', '') in ('1', 'true', 'yes')
        database = get_database()
        conn = database._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT id, name, soul_id, {_ARTIST_IDS_SQL}
                FROM lib2_artists ORDER BY name COLLATE NOCASE
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]

            counts = {}
            if include_contents:
                # Counts roll up the OWNED v2 catalogue. Two things this must not
                # do, both of which it used to: read legacy `albums`/`tracks` (the
                # cutover ported the roster query above but not this roll-up, and
                # because the empty legacy tables still exist the query "worked"
                # and returned nothing), and count every catalogue row (v2 keeps
                # discography and wishlist releases beside the owned ones, where
                # legacy was the owned library by construction).
                from core.library2.sql_util import owned_sql
                roll_ups = (
                    ('album_count', f"""
                        SELECT al.primary_artist_id, COUNT(*)
                          FROM lib2_albums al
                         WHERE {owned_sql('album', 'al')}
                      GROUP BY al.primary_artist_id"""),
                    ('track_count', f"""
                        SELECT al.primary_artist_id, COUNT(*)
                          FROM lib2_tracks t
                          JOIN lib2_albums al ON al.id = t.album_id
                         WHERE {owned_sql('track', 't')}
                      GROUP BY al.primary_artist_id"""),
                )
                for key, sql in roll_ups:
                    try:
                        for aid, n in cur.execute(sql):
                            counts.setdefault(str(aid), {})[key] = n
                    except Exception as e:
                        # Still best-effort, but never silently again: a null
                        # count column is exactly how the legacy read survived.
                        logger.warning("Library export %s roll-up failed: %s", key, e)
        finally:
            conn.close()

        # Normalize onto the canonical *_artist_id keys the exporter expects.
        artists = []
        for r in rows:
            c = counts.get(str(r['id']), {})
            artists.append({
                'name': r['name'],
                'spotify_artist_id': r['spotify_artist_id'],
                'musicbrainz_artist_id': r['musicbrainz_id'],
                'deezer_artist_id': r['deezer_id'],
                'discogs_artist_id': r['discogs_id'],
                'itunes_artist_id': r['itunes_artist_id'],
                'tidal_artist_id': r['tidal_id'],
                'qobuz_artist_id': r['qobuz_id'],
                'amazon_artist_id': r['amazon_id'],
                'lastfm_url': r['lastfm_url'],
                'genius_url': r['genius_url'],
                'soul_id': r['soul_id'],
                'album_count': c.get('album_count'),
                'track_count': c.get('track_count'),
            })
        extra = ['lastfm_url', 'genius_url', 'soul_id']
        if include_contents:
            extra += ['album_count', 'track_count']
        content = build_artist_export(artists, fmt=fmt, include_links=include_links, extra_fields=extra)
        mime, ext = export_mime_and_ext(fmt)
        return Response(content, mimetype=mime,
                        headers={'X-Export-Count': str(len(artists)), 'X-Export-Ext': ext})
    except Exception as e:
        logger.error(f"Library artist export failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/watchlist/add', methods=['POST'])
def add_to_watchlist():
    """Add an artist to the watchlist"""
    try:
        data = request.get_json() or {}
        # Strict types: `artist_id` used to be truthiness-checked only, so a list
        # or int survived until a `.isdigit()` deep in the DB raised a 500 (P3-01).
        artist_id = parse_strict_id(data.get('artist_id'))
        artist_name = data.get('artist_name')
        artist_name = artist_name.strip() if isinstance(artist_name, str) else None

        if not artist_id or not artist_name:
            return jsonify({"success": False, "error": "Missing artist_id or artist_name"}), 400

        database = get_database()
        quality_profile_id = None
        if data.get('quality_profile_id') is not None:
            quality_profile_id = parse_strict_int(data.get('quality_profile_id'))
            if quality_profile_id is None or quality_profile_id <= 0:
                return jsonify({"success": False, "error": "Invalid quality_profile_id"}), 400
            if not database.quality_profile_exists(quality_profile_id):
                return jsonify({"success": False, "error": "Unknown quality_profile_id"}), 400
        # Callers that KNOW the id's source (e.g. the Discovery Web, whose candidates carry
        # [source, id] pairs) can pass it explicitly — numeric Deezer/iTunes ids are ambiguous and
        # the detection below can otherwise mistake them for a library DB row id.
        # An unrecognised source is a client error, not a reason to guess (P1-05).
        from core.watchlist_sources import normalize_source
        explicit_source = None
        if data.get('source') is not None:
            explicit_source = normalize_source(data.get('source'))
            if explicit_source is None:
                return jsonify({"success": False, "error": "Unknown source"}), 400
        # Detect source from ID — check if it's a library DB ID first
        is_numeric_id = artist_id.isdigit()
        source = explicit_source
        if is_numeric_id and not explicit_source:
            # Could be a library DB ID, iTunes ID, Deezer ID, or Discogs ID
            # Check if this is a library DB artist and use their actual source IDs
            try:
                conn = database._get_connection()
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT {_ARTIST_IDS_SQL}
                    FROM lib2_artists WHERE id = ? LIMIT 1
                """, (artist_id,))
                row = cursor.fetchone()
                conn.close()
                if row:
                    # Library artist — use the best available source ID
                    fallback = _get_metadata_fallback_source()
                    if fallback == 'discogs' and row['discogs_id']:
                        artist_id = row['discogs_id']
                        source = 'discogs'
                    elif fallback == 'musicbrainz' and row['musicbrainz_id']:
                        artist_id = row['musicbrainz_id']
                        source = 'musicbrainz'
                    elif fallback == 'deezer' and row['deezer_id']:
                        artist_id = row['deezer_id']
                        source = 'deezer'
                    elif row['spotify_artist_id']:
                        artist_id = row['spotify_artist_id']
                        source = 'spotify'
                    elif row['itunes_artist_id']:
                        artist_id = row['itunes_artist_id']
                        source = 'itunes'
                    elif row['deezer_id']:
                        artist_id = row['deezer_id']
                        source = 'deezer'
                    elif row['discogs_id']:
                        artist_id = row['discogs_id']
                        source = 'discogs'
                    elif row['musicbrainz_id']:
                        artist_id = row['musicbrainz_id']
                        source = 'musicbrainz'
            except Exception as e:
                logger.debug("watchlist artist source lookup failed: %s", e)
        fallback_source = _get_metadata_fallback_source()   # always defined — image block below reads it
        if not source:
            # The fallback source is a hint, not a storable provider: hydrabase,
            # jiosaavn and bandcamp are in METADATA_SOURCE_PRIORITY but have no
            # watchlist id column, and handing one of those to the strict
            # provider check turned a working add into a hard failure (R2-01).
            from core.watchlist_sources import storable_source
            source = storable_source(fallback_source, artist_id) if is_numeric_id else 'spotify'
        success = database.add_artist_to_watchlist(
            artist_id,
            artist_name,
            profile_id=get_current_profile_id(),
            source=source,
            quality_profile_id=quality_profile_id,
        )
        if success:
            database.backfill_watchlist_musicbrainz_ids_from_library(profile_id=get_current_profile_id())

        if success:

            # Fetch and cache artist image immediately
            try:
                if is_numeric_id:
                    # For numeric IDs, fetch image from the configured fallback source
                    try:
                        if source == 'discogs':
                            # Discogs: fetch artist image from API
                            dc = _get_discogs_client()
                            dc_data = dc.get_artist(artist_id)
                            if dc_data:
                                image_url = dc_data.get('image_url')
                                logger.info(f"Discogs artist image: {image_url[:60] if image_url else 'None'}")
                        elif source == 'deezer' or fallback_source == 'deezer':
                            # Deezer: fetch artist image directly from API
                            # shared deezer budget — this call used to bypass it entirely
                            from core.deezer_throttle import wait_for_slot
                            wait_for_slot()
                            dz_resp = requests.get(f'https://api.deezer.com/artist/{artist_id}', timeout=5)
                            if dz_resp.ok:
                                dz_data = dz_resp.json()
                                image_url = dz_data.get('picture_xl') or dz_data.get('picture_big') or dz_data.get('picture_medium')
                                logger.info(f"Deezer artist image: {image_url[:60] if image_url else 'None'}")
                        else:
                            # iTunes: look up album entity for artwork
                            itunes_url = f"https://itunes.apple.com/lookup?id={artist_id}&entity=album&limit=5"
                            logger.info(f"Fetching iTunes artist image: {itunes_url}")
                            resp = requests.get(itunes_url, timeout=5)

                            image_url = None
                            if resp.status_code == 200:
                                resp_data = resp.json()
                                results = resp_data.get('results', [])

                                # Iterate results to find one with artwork
                                for res in results:
                                    if 'artworkUrl100' in res:
                                        image_url = res['artworkUrl100'].replace('100x100', '600x600')
                                        break

                        if image_url:
                            database.update_watchlist_artist_image(artist_id, image_url)
                            logger.warning(f"Cached {fallback_source} artist image for {artist_name}")
                        else:
                            logger.warning(f"No artwork found for {fallback_source} artist {artist_name}")
                    except Exception as fb_error:
                        logger.error(f"Error fetching {fallback_source} artwork: {fb_error}")
                elif _spotify_client() and _spotify_client().is_authenticated():
                    # For Spotify artists, fetch from Spotify API
                    artist_data = _spotify_client().get_artist(artist_id)
                    if artist_data and 'images' in artist_data and artist_data['images']:
                        # Get medium-sized image (usually the second one, or first if only one)
                        image_url = None
                        if len(artist_data['images']) > 1:
                            image_url = artist_data['images'][1]['url']
                        else:
                            image_url = artist_data['images'][0]['url']

                        # Update in database
                        if image_url:
                            database.update_watchlist_artist_image(artist_id, image_url)
                            logger.info(f"Cached artist image for {artist_name}")
                        else:
                            logger.warning(f"No image URL found for {artist_name}")
                    else:
                        logger.warning(f"No images in Spotify data for {artist_name}")
                else:
                    logger.info("Spotify client not available for fetching artist image")
            except Exception as img_error:
                # Don't fail the add operation if image fetch fails
                logger.error(f"Could not fetch artist image for {artist_name}: {img_error}")

            # Push updated count to this profile's WebSocket room immediately
            try:
                pid = get_current_profile_id()
                socketio.emit('watchlist:count', _build_watchlist_count_payload(profile_id=pid), room=f'profile:{pid}')
            except Exception as e:
                logger.debug("watchlist count emit failed: %s", e)
            try:
                if _automation_engine():
                    _automation_engine().emit('watchlist_artist_added', {
                        'artist': artist_name,
                        'artist_id': str(artist_id),
                    })
            except Exception as e:
                logger.debug("watchlist_artist_added emit failed: %s", e)
            _artmap_cache_invalidate(get_current_profile_id())
            return jsonify({"success": True, "message": f"Added {artist_name} to watchlist"})
        else:
            return jsonify({"success": False, "error": "Failed to add artist to watchlist"}), 500

    except Exception as e:
        logger.error(f"Error adding to watchlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/remove', methods=['POST'])
def remove_from_watchlist():
    """Remove an artist from the watchlist"""
    try:
        data = request.get_json()
        artist_id = data.get('artist_id')

        if not artist_id:
            return jsonify({"success": False, "error": "Missing artist_id"}), 400

        database = get_database()
        # §69.1 reverse edge: capture the row's identity BEFORE deleting so the
        # matching monitored lib2 artist can be demonitored (states must stay in
        # sync both ways). Fires only for a user-facing removal — the forward
        # mirror removes rows via the DB layer directly, never through here.
        _wl_descriptor = database.get_watchlist_artist_descriptor(artist_id, profile_id=get_current_profile_id())
        success = database.remove_artist_from_watchlist(artist_id, profile_id=get_current_profile_id())

        if success:
            try:
                from core.library2.monitor_sync import sync_watchlist_removal
                sync_watchlist_removal(database, config_manager, _wl_descriptor,
                                       profile_id=get_current_profile_id())
            except Exception as _sync_e:
                logger.debug("watchlist reverse-sync (single) failed: %s", _sync_e)
            # Push updated count to this profile's WebSocket room immediately
            try:
                pid = get_current_profile_id()
                socketio.emit('watchlist:count', _build_watchlist_count_payload(profile_id=pid), room=f'profile:{pid}')
            except Exception as e:
                logger.debug("watchlist count emit failed: %s", e)
            try:
                if _automation_engine():
                    _automation_engine().emit('watchlist_artist_removed', {
                        'artist': data.get('artist_name', str(artist_id)),
                        'artist_id': str(artist_id),
                    })
            except Exception as e:
                logger.debug("watchlist_artist_removed emit failed: %s", e)
            _artmap_cache_invalidate(get_current_profile_id())
            return jsonify({"success": True, "message": "Removed artist from watchlist"})
        else:
            return jsonify({"success": False, "error": "Failed to remove artist from watchlist"}), 500

    except Exception as e:
        logger.error(f"Error removing from watchlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/add-batch', methods=['POST'])
def add_batch_to_watchlist():
    """Add multiple artists to the watchlist at once"""
    try:
        data = request.get_json()
        artists = data.get('artists', [])

        if not artists or not isinstance(artists, list):
            return jsonify({"success": False, "error": "Missing or invalid artists list"}), 400

        from core.watchlist_sources import storable_source

        database = get_database()
        added = 0
        skipped = 0
        rejected = []

        # The metadata fallback source is read once, not per artist, and the
        # Quality Profile is validated with the dedicated existence check
        # instead of pulling the whole profile table inside the loop (R2-15).
        fb_source = _get_metadata_fallback_source()
        checked_profile_ids = set()

        for artist in artists:
            artist_id = artist.get('artist_id')
            artist_name = artist.get('artist_name')
            if not artist_id or not artist_name:
                continue

            # Check if already watched (by ID or name)
            if database.is_artist_in_watchlist(artist_id, profile_id=get_current_profile_id(), artist_name=artist_name):
                skipped += 1
                continue

            is_numeric = artist_id.isdigit()
            src = storable_source(fb_source, artist_id) if is_numeric else 'spotify'
            quality_profile_id = artist.get('quality_profile_id')
            if quality_profile_id is not None:
                try:
                    quality_profile_id = int(quality_profile_id)
                except (TypeError, ValueError):
                    # A rejected artist is reported, never silently dropped from
                    # the count the client sees (R2-15).
                    rejected.append({'artist_name': artist_name, 'reason': 'Invalid quality_profile_id'})
                    continue
                if quality_profile_id not in checked_profile_ids:
                    if not database.quality_profile_exists(quality_profile_id):
                        rejected.append({'artist_name': artist_name, 'reason': 'Unknown quality_profile_id'})
                        continue
                    checked_profile_ids.add(quality_profile_id)
            success = database.add_artist_to_watchlist(
                artist_id,
                artist_name,
                profile_id=get_current_profile_id(),
                source=src,
                quality_profile_id=quality_profile_id,
            )
            if success:
                added += 1
                # Cache artist image
                try:
                    is_numeric_id = artist_id.isdigit()
                    if is_numeric_id:
                        if fb_source == 'deezer':
                            fb_client = _get_metadata_fallback_client()
                            fb_artist = fb_client.get_artist(artist_id)
                            if fb_artist and fb_artist.get('images'):
                                image_url = fb_artist['images'][0].get('url')
                                if image_url:
                                    database.update_watchlist_artist_image(artist_id, image_url)
                        else:
                            itunes_url = f"https://itunes.apple.com/lookup?id={artist_id}&entity=album&limit=5"
                            resp = requests.get(itunes_url, timeout=5)
                            if resp.status_code == 200:
                                results = resp.json().get('results', [])
                                for res in results:
                                    if 'artworkUrl100' in res:
                                        image_url = res['artworkUrl100'].replace('100x100', '600x600')
                                        database.update_watchlist_artist_image(artist_id, image_url)
                                        break
                    elif _spotify_client() and _spotify_client().is_authenticated():
                        artist_data = _spotify_client().get_artist(artist_id)
                        if artist_data and 'images' in artist_data and artist_data['images']:
                            image_url = artist_data['images'][1]['url'] if len(artist_data['images']) > 1 else artist_data['images'][0]['url']
                            if image_url:
                                database.update_watchlist_artist_image(artist_id, image_url)
                except Exception as img_error:
                    logger.error(f"Could not fetch artist image for {artist_name}: {img_error}")
            else:
                rejected.append({'artist_name': artist_name, 'reason': 'Could not be added to the watchlist'})

        message = f"Added {added} artist{'s' if added != 1 else ''} to watchlist ({skipped} already watched)"
        if rejected:
            message += f", {len(rejected)} rejected"
        return jsonify({
            "success": True,
            "added": added,
            "skipped": skipped,
            "rejected": rejected,
            "message": message
        })

    except Exception as e:
        logger.error(f"Error batch adding to watchlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/library/watchlist-all-unwatched', methods=['POST'])
def watchlist_all_unwatched_library_artists():
    """Add all unwatched library artists (that have valid external IDs) to the watchlist"""
    try:
        database = get_database()
        active_source = _get_active_discovery_source()

        # Fetch all unwatched artists in pages (SQLite variable limit safe)
        unwatched_artists = []
        page = 1
        page_size = 400
        while True:
            result = database.get_library_artists(
                search_query='',
                letter='all',
                page=page,
                limit=page_size,
                watchlist_filter='unwatched',
                profile_id=get_current_profile_id()
            )
            unwatched_artists.extend(result.get('artists', []))
            if not result.get('pagination', {}).get('has_next', False):
                break
            page += 1
        added = 0
        skipped_no_id = 0
        skipped_already = 0

        # Try the active source's ID first, fall back through every other
        # supported source. Pre-fix this loop required the active source's
        # ID and silently dropped library artists that only had iTunes,
        # Deezer, or Discogs IDs — surfaced as "Library and Watchlist not
        # syncing correctly" on Discord because the bulk add reported
        # "Added X" with no breakdown of why others were rejected.
        from core.watchlist.source_picker import pick_artist_id_for_watchlist

        for artist in unwatched_artists:
            artist_id, picked_source = pick_artist_id_for_watchlist(artist, active_source)

            if not artist_id:
                skipped_no_id += 1
                continue

            artist_name = artist.get('name', '')
            if not artist_name:
                continue

            # Check if already watched (shouldn't be since we filtered, but safety check)
            if database.is_artist_in_watchlist(artist_id, profile_id=get_current_profile_id(), artist_name=artist_name):
                skipped_already += 1
                continue

            success = database.add_artist_to_watchlist(artist_id, artist_name, profile_id=get_current_profile_id(), source=picked_source)
            if success:
                added += 1
                # Use library thumb_url if available (no HTTP calls needed)
                if artist.get('image_url'):
                    try:
                        database.update_watchlist_artist_image(artist_id, artist['image_url'])
                    except Exception as e:
                        logger.debug("watchlist artist image update failed: %s", e)

        total_unwatched = len(unwatched_artists)
        message_parts = [f"Added {added} artist{'s' if added != 1 else ''} to watchlist"]
        if skipped_no_id > 0:
            message_parts.append(f"{skipped_no_id} skipped (no matching ID yet)")

        return jsonify({
            "success": True,
            "added": added,
            "skipped_no_id": skipped_no_id,
            "skipped_already": skipped_already,
            "total_unwatched": total_unwatched,
            "message": " — ".join(message_parts)
        })

    except Exception as e:
        logger.error(f"Error bulk watchlisting library artists: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/remove-batch', methods=['POST'])
def remove_batch_from_watchlist():
    """Remove multiple artists from the watchlist"""
    try:
        data = request.get_json()
        artist_ids = data.get('artist_ids', [])

        if not artist_ids or not isinstance(artist_ids, list):
            return jsonify({"success": False, "error": "Missing or invalid artist_ids"}), 400

        database = get_database()
        removed = 0
        # §69.1 reverse edge (covers "Clear Watchlist" = batch remove): demonitor
        # the matching lib2 artist for every row we actually removed.
        removed_descriptors = []
        for artist_id in artist_ids:
            descriptor = database.get_watchlist_artist_descriptor(artist_id, profile_id=get_current_profile_id())
            if database.remove_artist_from_watchlist(artist_id, profile_id=get_current_profile_id()):
                removed += 1
                if descriptor:
                    removed_descriptors.append(descriptor)

        try:
            from core.library2.monitor_sync import sync_watchlist_removal
            for descriptor in removed_descriptors:
                sync_watchlist_removal(database, config_manager, descriptor,
                                       profile_id=get_current_profile_id())
        except Exception as _sync_e:
            logger.debug("watchlist reverse-sync (batch) failed: %s", _sync_e)

        return jsonify({
            "success": True,
            "removed": removed,
            "message": f"Removed {removed} artist{'s' if removed != 1 else ''} from watchlist"
        })

    except Exception as e:
        logger.error(f"Error batch removing from watchlist: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/check', methods=['POST'])
def check_watchlist_status():
    """Check if an artist is in the watchlist"""
    try:
        data = request.get_json()
        artist_id = data.get('artist_id')
        
        if not artist_id:
            return jsonify({"success": False, "error": "Missing artist_id"}), 400
        
        database = get_database()
        is_watching = database.is_artist_in_watchlist(artist_id, profile_id=get_current_profile_id())

        return jsonify({"success": True, "is_watching": is_watching})
        
    except Exception as e:
        logger.error(f"Error checking watchlist status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/check-batch', methods=['POST'])
def check_watchlist_status_batch():
    """Check watchlist status for multiple artists in one request"""
    try:
        data = request.get_json()
        artist_ids = data.get('artist_ids', [])
        if not artist_ids:
            return jsonify({"success": False, "error": "Missing artist_ids"}), 400

        database = get_database()
        pid = get_current_profile_id()
        results = {}
        for aid in artist_ids:
            results[aid] = database.is_artist_in_watchlist(aid, profile_id=pid)

        return jsonify({"success": True, "results": results})

    except Exception as e:
        logger.error(f"Error batch checking watchlist status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/scan', methods=['POST'])
def start_watchlist_scan():
    """Start a watchlist scan for new releases"""
    try:
        # Check if MetadataService can provide a working client (Spotify OR fallback)
        from core.metadata.service import MetadataService
        metadata_service = MetadataService()

        # Get active provider - will be spotify or the configured fallback
        active_provider = metadata_service.get_active_provider()
        provider_info = metadata_service.get_provider_info()

        # Verify we have at least one working provider
        if not provider_info['spotify_authenticated'] and not provider_info['itunes_available']:
            fallback_name = provider_info.get('fallback_source', 'iTunes').capitalize()
            return jsonify({
                "success": False,
                "error": f"No music provider available. Please authenticate Spotify or ensure {fallback_name} is accessible."
            }), 400
        
        logger.info(f"Starting watchlist scan with {active_provider} provider")

        # Check if watchlist is already scanning
        if is_watchlist_actually_scanning():
            return jsonify({"success": False, "error": "Watchlist scan is already in progress."}), 409
        
        # Start the scan in a background thread
        scan_profile_id = get_current_profile_id()
        def run_scan():
            _ew_state = {}
            try:
                global watchlist_scan_state, watchlist_auto_scanning, watchlist_auto_scanning_timestamp
                from core.watchlist_scanner import WatchlistScanner
                from database.music_database import get_database

                # Set flag and timestamp for manual scan
                import time
                with watchlist_timer_lock:
                    watchlist_auto_scanning = True
                    watchlist_auto_scanning_timestamp = time.time()
                    logger.info(f"[Manual Watchlist Scan] Flag set at timestamp {watchlist_auto_scanning_timestamp}")

                # Get list of artists to scan (for the current profile)
                database = get_database()
                watchlist_artists = database.get_watchlist_artists(profile_id=scan_profile_id)

                if not watchlist_artists:
                    watchlist_scan_state['status'] = 'completed'
                    watchlist_scan_state['summary'] = {
                        'total_artists': 0,
                        'successful_scans': 0,
                        'new_tracks_found': 0,
                        'tracks_added_to_wishlist': 0
                    }
                    # Reset flag
                    with watchlist_timer_lock:
                        watchlist_auto_scanning = False
                        watchlist_auto_scanning_timestamp = 0
                    return

                # Initialize scanner with MetadataService for cross-provider support
                scanner = WatchlistScanner(metadata_service=metadata_service)
                
                # PROACTIVE ID BACKFILLING (cross-provider support)
                # Before scanning, ensure all artists have IDs for ALL available sources
                providers_to_backfill = ['itunes', 'deezer', 'musicbrainz']
                if _spotify_client() and _spotify_client().is_spotify_authenticated():
                    providers_to_backfill.append('spotify')
                try:
                    if config_manager.get('discogs.token', ''):
                        providers_to_backfill.append('discogs')
                except Exception as e:
                    logger.debug("discogs token backfill check failed: %s", e)
                for _bf_provider in providers_to_backfill:
                    try:
                        logger.debug(f"Checking for missing {_bf_provider} IDs in watchlist...")
                        scanner._backfill_missing_ids(watchlist_artists, _bf_provider)
                    except Exception as backfill_error:
                        logger.error(f"Error during {_bf_provider} ID backfilling: {backfill_error}")
                        # Continue with next provider
                try:
                    filled = scanner.backfill_watchlist_artist_images(scan_profile_id)
                    if filled:
                        logger.info(f"Backfilled {filled} watchlist artist images")
                except Exception as img_err:
                    logger.error(f"Image backfill error: {img_err}")

                # Initialize detailed progress tracking
                watchlist_scan_state.update({
                    'total_artists': len(watchlist_artists),
                    'current_artist_index': 0,
                    'current_artist_name': '',
                    'current_artist_image_url': '',
                    'current_phase': 'starting',
                    'albums_to_check': 0,
                    'albums_checked': 0,
                    'current_album': '',
                    'current_album_image_url': '',
                    'current_track_name': '',
                    'tracks_found_this_scan': 0,
                    'tracks_added_this_scan': 0,
                    'recent_wishlist_additions': [],
                    # #831: full per-run ledger of found tracks (added vs
                    # skipped) so the completed-scan summary can list WHICH
                    # tracks the "New tracks / Added to wishlist" counts mean.
                    'scan_track_events': [],
                    'scan_run_id': datetime.now().strftime('%Y%m%d-%H%M%S'),
                })

                scan_results = []

                # Pause enrichment workers during scan to reduce API contention
                _ew_state = _pause_enrichment_workers('watchlist scan')
                scan_results = scanner.scan_watchlist_profile(
                    scan_profile_id,
                    watchlist_artists=watchlist_artists,
                    scan_state=watchlist_scan_state,
                    cancel_check=lambda: watchlist_scan_state.get('cancel_requested', False),
                )

                # --- Label watchlist phase (same thread, same live state) ---
                # Labels ride the normal watchlist scan via the SHARED helper
                # (also used by the scheduled automation), so one "Scan Watchlist"
                # covers artists THEN labels with the identical live display.
                _lbl_tracks = 0
                if not watchlist_scan_state.get('cancel_requested', False):
                    try:
                        from core.automation.handlers.scan_watchlist_labels import run_label_scan_phase
                        _lbl_tracks = run_label_scan_phase(
                            watchlist_scan_state, database=database,
                            get_deezer=_get_deezer_client, profile_id=scan_profile_id)
                    except Exception as _lbl_err:
                        logger.error("Label watchlist scan phase failed: %s", _lbl_err, exc_info=True)

                # Store final results (skip if cancelled — already set by cancel handler)
                was_cancelled = watchlist_scan_state.get('cancel_requested', False)
                if not was_cancelled:
                    _artmap_cache_invalidate(scan_profile_id)
                    successful_scans = [r for r in scan_results if r.success]
                    total_new_tracks = sum(r.new_tracks_found for r in successful_scans)
                    total_added_to_wishlist = sum(r.tracks_added_to_wishlist for r in successful_scans)

                    watchlist_scan_state['status'] = 'completed'
                    watchlist_scan_state['results'] = scan_results
                    watchlist_scan_state['completed_at'] = datetime.now()
                    watchlist_scan_state['current_phase'] = 'completed'

                    try:
                        _labels_scanned = len(database.get_watchlist_labels() or [])
                    except Exception:
                        # a label COUNT must never abort an otherwise successful scan
                        _labels_scanned = 0
                    watchlist_scan_state['summary'] = {
                        'total_artists': len(scan_results),
                        'successful_scans': len(successful_scans),
                        'new_tracks_found': total_new_tracks + _lbl_tracks,
                        'tracks_added_to_wishlist': total_added_to_wishlist + _lbl_tracks,
                        # label breakdown (additive; existing UI ignores extra keys)
                        'labels_scanned': _labels_scanned,
                        'label_tracks_added': _lbl_tracks,
                    }

                    logger.info(f"Watchlist scan completed: {len(successful_scans)}/{len(scan_results)} artists scanned successfully")
                    logger.info(f"Found {total_new_tracks} new tracks, added {total_added_to_wishlist} to wishlist (+ {_lbl_tracks} from labels)")
                else:
                    logger.warning("Watchlist scan cancelled — skipping post-scan steps")

                # #831 round 2: persist this run + its track ledger so the
                # Watchlist History modal can show what every past scan did.
                try:
                    from core.watchlist.scan_history import persist_scan_run
                    persist_scan_run(
                        get_database(), watchlist_scan_state,
                        profile_id=scan_profile_id, was_cancelled=was_cancelled,
                    )
                except Exception as _hist_err:
                    logger.error(f"Failed to persist watchlist scan run: {_hist_err}")

                # Post-scan steps — skip if cancelled
                if not was_cancelled:
                    # Populate discovery pool from similar artists
                    logger.info("Starting discovery pool population...")
                    watchlist_scan_state['current_phase'] = 'populating_discovery_pool'
                    try:
                        scanner.populate_discovery_pool(profile_id=scan_profile_id)
                        logger.info("Discovery pool population complete")
                    except Exception as discovery_error:
                        logger.error(f"Error populating discovery pool: {discovery_error}")
                        import traceback
                        traceback.print_exc()

                    # Update ListenBrainz playlists cache
                    logger.info("Starting ListenBrainz playlists update...")
                    watchlist_scan_state['current_phase'] = 'updating_listenbrainz'
                    try:
                        from core.listenbrainz_manager import ListenBrainzManager
                        db = get_database()
                        db_path = str(db.database_path)
                        # Update for all profiles with LB tokens
                        lb_profiles = db.get_profiles_with_listenbrainz()
                        if lb_profiles:
                            for lb_prof in lb_profiles:
                                lb_manager = ListenBrainzManager(db_path, profile_id=lb_prof['id'], token=lb_prof['token'], base_url=lb_prof['base_url'])
                                lb_result = lb_manager.update_all_playlists()
                                if lb_result.get('success'):
                                    logger.info(f"ListenBrainz update complete for profile {lb_prof['id']}: {lb_result.get('summary', {})}")
                        else:
                            # Fallback: use global config token
                            lb_manager = ListenBrainzManager(db_path)
                            lb_result = lb_manager.update_all_playlists()
                            if lb_result.get('success'):
                                logger.info(f"ListenBrainz update complete (global): {lb_result.get('summary', {})}")
                            elif lb_result.get('error'):
                                logger.error(f"ListenBrainz update skipped: {lb_result.get('error')}")
                    except Exception as lb_error:
                        logger.error(f"Error updating ListenBrainz: {lb_error}")
                        import traceback
                        traceback.print_exc()

                    # Update current seasonal playlist (weekly refresh)
                    logger.info("Starting seasonal content update...")
                    watchlist_scan_state['current_phase'] = 'updating_seasonal'
                    try:
                        from core.seasonal_discovery import get_seasonal_discovery_service
                        seasonal_service = get_seasonal_discovery_service(_spotify_client(), database)

                        # Only update the current active season
                        current_season = seasonal_service.get_current_season()
                        if current_season:
                            if seasonal_service.should_populate_seasonal_content(current_season, days_threshold=7):
                                logger.info(f"Updating {current_season} seasonal content...")
                                seasonal_service.populate_seasonal_content(current_season)
                                seasonal_service.curate_seasonal_playlist(current_season)
                                logger.info(f"{current_season.capitalize()} seasonal content updated")
                            else:
                                logger.info(f"{current_season.capitalize()} seasonal content recently updated, skipping")
                        else:
                            logger.warning("ℹ️ No active season at this time")
                    except Exception as seasonal_error:
                        logger.error(f"Error updating seasonal content: {seasonal_error}")
                        import traceback
                        traceback.print_exc()

                    # Generate Last.fm radio playlists (weekly refresh)
                    logger.info("Starting Last.fm radio generation...")
                    watchlist_scan_state['current_phase'] = 'generating_lastfm_radio'
                    try:
                        scanner._generate_lastfm_radio_playlists()
                        logger.info("Last.fm radio generation complete")
                    except Exception as lastfm_error:
                        logger.error(f"Error generating Last.fm radio playlists: {lastfm_error}")

                    # Sync Spotify library cache
                    logger.info("Syncing Spotify library cache...")
                    watchlist_scan_state['current_phase'] = 'syncing_spotify_library'
                    try:
                        scanner.sync_spotify_library_cache(profile_id=scan_profile_id)
                        logger.info("Spotify library cache sync complete")
                    except Exception as lib_error:
                        logger.error(f"Error syncing Spotify library: {lib_error}")

            except Exception as e:
                logger.error(f"Error during watchlist scan: {e}")
                watchlist_scan_state['status'] = 'error'
                watchlist_scan_state['error'] = str(e)

            finally:
                # Resume enrichment workers if we paused them
                _resume_enrichment_workers(_ew_state, 'watchlist scan')

                # Clear one-time rescan cutoff after full scan cycle
                try:
                    scanner._clear_rescan_cutoff()
                except Exception as e:
                    logger.debug("scanner rescan cutoff clear failed: %s", e)

                # Always reset flag when scan completes (success or error)
                with watchlist_timer_lock:
                    watchlist_auto_scanning = False
                    watchlist_auto_scanning_timestamp = 0
                    logger.info("[Manual Watchlist Scan] Flag reset - scan complete")
        
        # Initialize scan state
        global watchlist_scan_state
        watchlist_scan_state = {
            'status': 'scanning',
            'started_at': datetime.now(),
            'results': [],
            'summary': {},
            'error': None,
            'cancel_requested': False
        }
        
        # Start scan in background
        thread = threading.Thread(target=run_scan)
        thread.daemon = True
        thread.start()
        
        return jsonify({"success": True, "message": "Watchlist scan started"})
        
    except Exception as e:
        logger.error(f"Error starting watchlist scan: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/scan/status', methods=['GET'])
def get_watchlist_scan_status():
    """Get the current status of watchlist scanning"""
    try:
        global watchlist_scan_state
        if 'watchlist_scan_state' not in globals():
            return jsonify({
                "success": True,
                "status": "idle",
                "summary": {}
            })

        # Convert datetime objects to ISO format for JSON serialization
        state = watchlist_scan_state.copy()
        if 'started_at' in state and state['started_at']:
            state['started_at'] = state['started_at'].isoformat()
        if 'completed_at' in state and state['completed_at']:
            state['completed_at'] = state['completed_at'].isoformat()

        # Remove results array - it contains ScanResult objects that aren't JSON serializable
        # The summary already contains the aggregate data we need
        if 'results' in state:
            del state['results']

        return jsonify({"success": True, **state})

    except Exception as e:
        logger.error(f"Error getting watchlist scan status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/scan/history', methods=['GET'])
def get_watchlist_scan_history():
    """Recent watchlist scan runs (counts only — ledgers fetched per run)."""
    try:
        limit = min(int(request.args.get('limit', 30) or 30), 100)
        runs = get_database().get_watchlist_scan_runs(limit=limit)
        return jsonify({"success": True, "runs": runs})
    except Exception as e:
        logger.error(f"Error getting watchlist scan history: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/watchlist/scan/history/<run_id>/tracks', methods=['GET'])
def get_watchlist_scan_history_tracks(run_id):
    """The track ledger (added/skipped) for one past scan run."""
    try:
        events = get_database().get_watchlist_scan_run_events(run_id)
        return jsonify({"success": True, "events": events})
    except Exception as e:
        logger.error(f"Error getting watchlist scan history tracks: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/watchlist/scan/cancel', methods=['POST'])
def cancel_watchlist_scan():
    """Cancel a running watchlist scan"""
    try:
        global watchlist_scan_state
        if watchlist_scan_state.get('status') != 'scanning':
            return jsonify({"success": False, "error": "No scan is currently running"}), 400

        watchlist_scan_state['cancel_requested'] = True
        logger.info("[Watchlist Scan] Cancel requested by user")
        return jsonify({"success": True, "message": "Cancel request sent"})

    except Exception as e:
        logger.error(f"Error cancelling watchlist scan: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# Similar Artists Update State
similar_artists_update_state = {
    'status': 'idle',  # idle, running, completed, error
    'artists_processed': 0,
    'total_artists': 0,
    'current_artist': None,
    'error': None
}
similar_artists_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="SimilarArtistsUpdate")

@bp.route('/api/watchlist/update-similar-artists', methods=['POST'])
def update_similar_artists_endpoint():
    """Update similar artists for all watchlist artists (for discovery feature)"""
    try:
        global similar_artists_update_state

        if similar_artists_update_state['status'] == 'running':
            return jsonify({"success": False, "error": "Similar artists update already in progress"}), 409

        if not _spotify_client() or not _spotify_client().is_authenticated():
            return jsonify({"success": False, "error": "Spotify client not available"}), 400

        # Reset state
        similar_artists_update_state = {
            'status': 'running',
            'artists_processed': 0,
            'total_artists': 0,
            'current_artist': None,
            'error': None
        }

        # Start update in background
        similar_artists_executor.submit(_update_similar_artists_worker)

        return jsonify({"success": True, "message": "Similar artists update started"})

    except Exception as e:
        logger.error(f"Error starting similar artists update: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/similar-artists-status', methods=['GET'])
def get_similar_artists_update_status():
    """Get status of similar artists update"""
    try:
        global similar_artists_update_state
        return jsonify({"success": True, **similar_artists_update_state})
    except Exception as e:
        logger.error(f"Error getting similar artists status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _watchlist_spotify_artist_info(artist_data, fallback_id):
    """Normalize official or Spotify-Free artist metadata for the watchlist UI."""
    if not artist_data:
        return None

    images = artist_data.get('images') or []
    image_url = None
    if images:
        first_image = images[0]
        if isinstance(first_image, dict):
            image_url = first_image.get('url')
        elif isinstance(first_image, str):
            image_url = first_image
    image_url = image_url or artist_data.get('image_url')

    followers = artist_data.get('followers', 0)
    if isinstance(followers, dict):
        followers = followers.get('total', 0)
    try:
        followers = int(followers or 0)
    except (TypeError, ValueError):
        followers = 0

    return {
        'id': artist_data.get('id') or fallback_id,
        'name': artist_data.get('name') or '',
        'image_url': image_url,
        'followers': followers,
        'popularity': artist_data.get('popularity') or 0,
        'genres': artist_data.get('genres') or []
    }


@bp.route('/api/watchlist/artist/<artist_id>/config', methods=['GET', 'POST'])
def watchlist_artist_config(artist_id):
    """Get or update watchlist artist configuration"""
    try:
        from database.music_database import get_database

        database = get_database()
        active_profile_id = get_current_profile_id()

        if request.method == 'GET':
            database.backfill_watchlist_musicbrainz_ids_from_library(profile_id=get_current_profile_id())
            # Get current config from database
            conn = sqlite3.connect(str(database.database_path))
            cursor = conn.cursor()
            cursor.execute("""
                SELECT include_albums, include_eps, include_singles,
                       include_live, include_remixes, include_acoustic, include_compilations,
                       artist_name, image_url, spotify_artist_id, itunes_artist_id,
                       last_scan_timestamp, date_added, include_instrumentals, deezer_artist_id,
                       lookback_days, discogs_artist_id, preferred_metadata_source,
                       amazon_artist_id, musicbrainz_artist_id, auto_download,
                       quality_profile_id, auto_download_pref
                FROM watchlist_artists
                WHERE profile_id = ? AND (
                      spotify_artist_id = ? OR itunes_artist_id = ? OR deezer_artist_id = ?
                      OR discogs_artist_id = ? OR amazon_artist_id = ? OR musicbrainz_artist_id = ?
                )
            """, (active_profile_id, artist_id, artist_id, artist_id, artist_id, artist_id, artist_id))
            result = cursor.fetchone()
            conn.close()

            if not result:
                return jsonify({"success": False, "error": "Artist not found in watchlist"}), 404

            # Determine if this is an iTunes or Spotify artist
            is_itunes_artist = artist_id.isdigit()
            spotify_id = result[9]   # spotify_artist_id from query
            itunes_id = result[10]  # itunes_artist_id from query
            deezer_id = result[14]  # deezer_artist_id from query
            discogs_id = result[16]  # discogs_artist_id from query
            amazon_id = result[18] if len(result) > 18 else None  # amazon_artist_id from query
            musicbrainz_id = result[19] if len(result) > 19 else None  # musicbrainz_artist_id from query

            # Get artist info from the Spotify wrapper so Premium-gated official
            # calls can fall through to the no-creds Spotify metadata source.
            artist_info = None
            spotify_metadata_available = False
            if _spotify_client():
                try:
                    if hasattr(_spotify_client(), 'is_spotify_metadata_available'):
                        spotify_metadata_available = _spotify_client().is_spotify_metadata_available()
                    else:
                        spotify_metadata_available = _spotify_client().is_authenticated()
                except Exception:
                    spotify_metadata_available = False

            if not is_itunes_artist and _spotify_client() and spotify_id and spotify_metadata_available:
                try:
                    artist_info = _watchlist_spotify_artist_info(
                        _spotify_client().get_artist(spotify_id),
                        spotify_id,
                    )
                except Exception as e:
                    logger.warning(f"Could not fetch watchlist artist info from Spotify metadata: {e}")

            # Fallback to database info if Spotify fetch failed
            if not artist_info:
                artist_info = {
                    'id': artist_id,
                    'name': result[7],  # artist_name
                    'image_url': result[8],  # image_url
                    'followers': 0,
                    'popularity': 0,
                    'genres': []
                }

            # Enrich with library artist data (banner, bio, style, mood, label)
            try:
                conn2 = sqlite3.connect(str(database.database_path))
                cur2 = conn2.cursor()
                # The library `artists` table uses `deezer_id` / `discogs_id` for
                # those columns; only the `watchlist_artists` table uses the
                # `_artist_id` suffix for them. Mixing them was producing a
                # 'no such column' on every watchlist-config GET.
                cur2.execute("""
                    SELECT banner_url, summary, style, mood, label, genres
                    FROM lib2_artists
                    WHERE spotify_id = ?
                       OR json_extract(external_ids, '$.itunes') = ?
                       OR json_extract(external_ids, '$.deezer') = ?
                       OR json_extract(external_ids, '$.discogs') = ?
                       OR musicbrainz_id = ?
                    LIMIT 1
                """, (
                    spotify_id or artist_id,
                    itunes_id or artist_id,
                    deezer_id or artist_id,
                    discogs_id or artist_id,
                    musicbrainz_id or artist_id,
                ))
                lib_row = cur2.fetchone()
                if lib_row:
                    artist_info['banner_url'] = lib_row[0]
                    artist_info['summary'] = lib_row[1]
                    artist_info['style'] = lib_row[2]
                    artist_info['mood'] = lib_row[3]
                    artist_info['label'] = lib_row[4]
                    # Backfill genres from library if Spotify didn't provide any
                    if not artist_info.get('genres') and lib_row[5]:
                        try:
                            artist_info['genres'] = json.loads(lib_row[5])
                        except (json.JSONDecodeError, TypeError):
                            pass

                # Get recent releases for this watchlist artist
                cur2.execute("""
                    SELECT rr.album_name, rr.release_date, rr.album_cover_url, rr.track_count
                    FROM recent_releases rr
                    JOIN watchlist_artists wa ON rr.watchlist_artist_id = wa.id
                    WHERE wa.spotify_artist_id = ? OR wa.itunes_artist_id = ? OR wa.deezer_artist_id = ?
                          OR wa.discogs_artist_id = ? OR wa.amazon_artist_id = ? OR wa.musicbrainz_artist_id = ?
                    ORDER BY rr.release_date DESC
                    LIMIT 6
                """, (
                    spotify_id or artist_id,
                    itunes_id or artist_id,
                    deezer_id or artist_id,
                    discogs_id or artist_id,
                    amazon_id or artist_id,
                    musicbrainz_id or artist_id,
                ))
                releases = [
                    {
                        'album_name': r[0],
                        'release_date': r[1],
                        'album_cover_url': r[2],
                        'track_count': r[3],
                    }
                    for r in cur2.fetchall()
                ]
                conn2.close()
            except Exception as e:
                logger.error(f"Could not enrich artist from library: {e}")
                releases = []

            config = {
                'include_albums': bool(result[0]),  # Convert INTEGER to boolean
                'include_eps': bool(result[1]),
                'include_singles': bool(result[2]),
                'include_live': bool(result[3]),
                'include_remixes': bool(result[4]),
                'include_acoustic': bool(result[5]),
                'include_compilations': bool(result[6]),
                'include_instrumentals': bool(result[13]) if result[13] is not None else False,
                'last_scan_timestamp': result[11],
                'date_added': result[12],
                'lookback_days': result[15] if len(result) > 15 else None,
                'preferred_metadata_source': result[17] if len(result) > 17 else None,
                # follow-only toggle (default True/auto-download when column absent)
                'auto_download': bool(result[20]) if len(result) > 20 and result[20] is not None else True,
                'quality_profile_id': int(result[21]) if len(result) > 21 and result[21] is not None else None,
                # Three-state preference: null = follow the global, 0/1 = this
                # artist decides. The global travels with it so the UI can say
                # WHICH way "follow the global" currently resolves.
                'auto_download_pref': (int(result[22])
                                       if len(result) > 22 and result[22] is not None else None),
                'global_auto_download': bool(config_manager.get('watchlist.global_auto_download', True)),
            }

            from core.metadata.registry import available_sources, get_primary_source
            return jsonify({
                "success": True,
                "config": config,
                "artist": artist_info,
                "recent_releases": releases,
                "spotify_artist_id": spotify_id,
                "itunes_artist_id": itunes_id,
                "deezer_artist_id": deezer_id,
                "discogs_artist_id": discogs_id,
                "amazon_artist_id": amazon_id,
                "musicbrainz_artist_id": musicbrainz_id,
                "watchlist_name": result[7],  # Original stored watchlist artist name
                "global_metadata_source": get_primary_source(),
                # Which of those providers can serve RIGHT NOW, and which ids
                # resolve straight to a LIBRARY artist. The panel's "View
                # Discography" link pins ONE source; a pinned source is safe
                # exactly when its provider is alive OR its id lands on a
                # library record (the artist page upgrades to the library view
                # off the id column with no provider call). Anything else is a
                # guaranteed 503 ("provider is unavailable" — the Discord
                # report where the watchlist failed but Discover worked). Only
                # the server knows either fact, so it says both.
                "available_sources": available_sources(
                    ("spotify", "itunes", "deezer", "discogs", "musicbrainz")
                ),
                "library_resolvable_sources": _core_sources_resolvable_in_library(
                    database,
                    {
                        "spotify": spotify_id,
                        "itunes": itunes_id,
                        "deezer": deezer_id,
                        "discogs": discogs_id,
                        "musicbrainz": musicbrainz_id,
                    },
                ),
                "quality_profiles": database.list_quality_profiles(),
            })

        else:  # POST
            data = request.get_json()
            if not data:
                return jsonify({"success": False, "error": "No data provided"}), 400

            # Read the CURRENT configuration first. This endpoint used to fill
            # every absent field with a hardcoded default and then write the lot
            # back, so a client sending only `quality_profile_id` silently reset
            # release types, lookback, metadata source and auto-download (P2-03).
            # Absent now means "keep", so a partial request really is partial
            # while a full form request behaves exactly as before.
            conn = sqlite3.connect(str(database.database_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT include_albums, include_eps, include_singles, include_live,
                       include_remixes, include_acoustic, include_compilations,
                       include_instrumentals, lookback_days, preferred_metadata_source,
                       auto_download, quality_profile_id, auto_download_pref
                FROM watchlist_artists
                WHERE profile_id = ? AND (
                      spotify_artist_id = ? OR itunes_artist_id = ? OR deezer_artist_id = ?
                      OR discogs_artist_id = ? OR amazon_artist_id = ? OR musicbrainz_artist_id = ?
                )
            """, (active_profile_id, artist_id, artist_id, artist_id, artist_id, artist_id, artist_id))
            old_row = cursor.fetchone()
            if old_row is None:
                conn.close()
                return jsonify({"success": False, "error": "Artist not found in watchlist"}), 404

            class _BadBool(Exception):
                def __init__(self, field):
                    self.field = field

            def _field(name, fallback):
                """Sent value, or the stored one when the client omitted the field.

                An unparseable value is an error, NOT a reason to substitute the
                hardcoded default: doing that silently flipped a stored setting
                off on a 200 response, and disagreed with the modular API, which
                answers 400 for the same input (R2-08).
                """
                if name not in data:
                    return old_row[name] if old_row[name] is not None else fallback
                parsed = parse_strict_bool(data.get(name))
                if parsed is None:
                    raise _BadBool(name)
                return parsed

            try:
                include_albums = _field('include_albums', True)
                include_eps = _field('include_eps', True)
                include_singles = _field('include_singles', True)
                include_live = _field('include_live', False)
                include_remixes = _field('include_remixes', False)
                include_acoustic = _field('include_acoustic', False)
                include_compilations = _field('include_compilations', False)
                include_instrumentals = _field('include_instrumentals', False)
            except _BadBool as bad:
                conn.close()
                return jsonify({"success": False, "error": f"{bad.field} must be a boolean"}), 400

            if 'lookback_days' in data:
                lookback_days = data.get('lookback_days')  # None = use global setting
                if lookback_days is not None:
                    try:
                        lookback_days = int(lookback_days) if lookback_days != '' else None
                    except (TypeError, ValueError):
                        conn.close()
                        return jsonify({"success": False, "error": "Invalid lookback_days"}), 400
            else:
                lookback_days = old_row['lookback_days']

            if 'preferred_metadata_source' in data:
                preferred_metadata_source = data.get('preferred_metadata_source', None)
                # Validate — only accept known sources, empty string means clear override
                _watchlist_meta_sources = (
                    'spotify', 'deezer', 'itunes', 'discogs', 'musicbrainz',
                )
                from core.metadata.registry import EXPERIMENTAL_SOURCES, is_source_enabled
                _watchlist_meta_sources = _watchlist_meta_sources + tuple(
                    name for name in EXPERIMENTAL_SOURCES if is_source_enabled(name)
                )
                if preferred_metadata_source == '' or preferred_metadata_source not in _watchlist_meta_sources:
                    preferred_metadata_source = None
            else:
                preferred_metadata_source = old_row['preferred_metadata_source']

            # Follow-only toggle. `auto_download_pref` is the real setting (three
            # states: null = follow the global); the boolean column is kept in
            # step only so an older reader still sees an explicit choice. A
            # request that mentions neither field leaves BOTH alone -- an
            # untouched artist must not pin itself out of future global flips.
            from core.watchlist_auto_download import (
                legacy_column_value as _legacy_auto_download,
                resolve_pref as _resolve_auto_download_pref,
            )
            if 'auto_download_pref' in data:
                auto_download_pref = _resolve_auto_download_pref(
                    sent_pref=data.get('auto_download_pref'))
                auto_download = _legacy_auto_download(auto_download_pref)
            elif 'auto_download' in data:
                # Legacy shape. Parse it strictly first -- the string "false" is
                # truthy, and _field is what already knows that.
                try:
                    legacy = _field('auto_download', True)
                except _BadBool as bad:
                    conn.close()
                    return jsonify({"success": False, "error": f"{bad.field} must be a boolean"}), 400
                auto_download_pref = _resolve_auto_download_pref(sent_legacy=legacy)
                auto_download = _legacy_auto_download(auto_download_pref)
            else:
                auto_download_pref = old_row['auto_download_pref']
                auto_download = old_row['auto_download']

            if 'quality_profile_id' in data and data.get('quality_profile_id') is not None:
                quality_profile_id = parse_strict_int(data.get('quality_profile_id'))
                if quality_profile_id is None or quality_profile_id <= 0:
                    conn.close()
                    return jsonify({"success": False, "error": "Invalid quality_profile_id"}), 400
                if not database.quality_profile_exists(quality_profile_id):
                    conn.close()
                    return jsonify({"success": False, "error": "Unknown quality_profile_id"}), 400
            else:
                quality_profile_id = old_row['quality_profile_id']

            # Validate at least one release type is selected
            if not (include_albums or include_eps or include_singles):
                conn.close()
                return jsonify({"success": False, "error": "At least one release type must be selected"}), 400

            # lookback_days changed — clear last_scan_timestamp to force rescan
            lookback_changed = old_row['lookback_days'] != lookback_days

            cursor.execute("""
                UPDATE watchlist_artists
                SET include_albums = ?, include_eps = ?, include_singles = ?,
                    include_live = ?, include_remixes = ?, include_acoustic = ?, include_compilations = ?,
                    include_instrumentals = ?, lookback_days = ?, preferred_metadata_source = ?,
                    auto_download = ?, auto_download_pref = ?, quality_profile_id = ?,
                    last_scan_timestamp = CASE WHEN ? THEN NULL ELSE last_scan_timestamp END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE profile_id = ? AND (
                      spotify_artist_id = ? OR itunes_artist_id = ? OR deezer_artist_id = ?
                      OR discogs_artist_id = ? OR amazon_artist_id = ? OR musicbrainz_artist_id = ?
                )
            """, (int(include_albums), int(include_eps), int(include_singles),
                  int(include_live), int(include_remixes), int(include_acoustic), int(include_compilations),
                  int(include_instrumentals), lookback_days, preferred_metadata_source, int(auto_download),
                  auto_download_pref, quality_profile_id, lookback_changed, active_profile_id,
                  artist_id, artist_id, artist_id, artist_id, artist_id, artist_id))
            conn.commit()

            if cursor.rowcount == 0:
                conn.close()
                return jsonify({"success": False, "error": "Artist not found in watchlist"}), 404

            conn.close()

            logger.info(f"Updated watchlist config for artist {artist_id}: albums={include_albums}, eps={include_eps}, singles={include_singles}, live={include_live}, remixes={include_remixes}, acoustic={include_acoustic}, compilations={include_compilations}, instrumentals={include_instrumentals}")

            return jsonify({
                "success": True,
                "message": "Artist configuration updated successfully",
                "config": {
                    'include_albums': include_albums,
                    'include_eps': include_eps,
                    'include_singles': include_singles,
                    'include_live': include_live,
                    'include_remixes': include_remixes,
                    'include_acoustic': include_acoustic,
                    'include_compilations': include_compilations,
                    'include_instrumentals': include_instrumentals,
                    # bool, not the raw column int: the echo has always been a
                    # boolean and the clients are typed against that.
                    'auto_download': bool(auto_download),
                    'auto_download_pref': auto_download_pref,
                    'quality_profile_id': quality_profile_id,
                }
            })

    except Exception as e:
        logger.error(f"Error in watchlist artist config: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/artist/<artist_id>/link-provider', methods=['POST'])
def watchlist_artist_link_provider(artist_id):
    """Manually link a watchlist artist to a different Spotify/iTunes artist."""
    try:
        from database.music_database import get_database
        database = get_database()

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        new_provider_id = data.get('provider_id', '').strip()
        provider = data.get('provider', '').strip()

        valid_providers = ('spotify', 'itunes', 'deezer', 'discogs', 'amazon', 'musicbrainz')
        if provider not in valid_providers:
            return jsonify({"success": False, "error": f"Invalid provider. Must be one of: {', '.join(valid_providers)}"}), 400

        # Empty provider_id = clear the match for this source
        is_clear = not new_provider_id

        conn = sqlite3.connect(str(database.database_path))
        cursor = conn.cursor()

        # Find the watchlist artist row
        cursor.execute("""
            SELECT id, artist_name, spotify_artist_id, itunes_artist_id
            FROM watchlist_artists
            WHERE spotify_artist_id = ? OR itunes_artist_id = ? OR deezer_artist_id = ?
                  OR discogs_artist_id = ? OR amazon_artist_id = ? OR musicbrainz_artist_id = ?
        """, (artist_id, artist_id, artist_id, artist_id, artist_id, artist_id))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Artist not found in watchlist"}), 404

        watchlist_row_id = row[0]
        artist_name = row[1]

        # Check for duplicate — another watchlist artist already has this provider ID
        col_map = {
            'spotify': 'spotify_artist_id',
            'itunes': 'itunes_artist_id',
            'deezer': 'deezer_artist_id',
            'discogs': 'discogs_artist_id',
            'amazon': 'amazon_artist_id',
            'musicbrainz': 'musicbrainz_artist_id',
        }
        col = col_map[provider]

        if not is_clear:
            cursor.execute(f"SELECT id, artist_name FROM watchlist_artists WHERE {col} = ? AND id != ?",
                           (new_provider_id, watchlist_row_id))
            duplicate = cursor.fetchone()
            if duplicate:
                conn.close()
                return jsonify({"success": False, "error": f"Another watchlist artist ('{duplicate[1]}') already has this {provider} ID"}), 409

        # Set to new ID or NULL (clear)
        update_val = new_provider_id if not is_clear else None
        cursor.execute(f"UPDATE watchlist_artists SET {col} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                       (update_val, watchlist_row_id))

        conn.commit()
        conn.close()

        action = 'Cleared' if is_clear else 'Linked'
        logger.info(f"{action} watchlist artist '{artist_name}' {provider} ID: {new_provider_id or 'NULL'}")

        return jsonify({
            "success": True,
            "message": f"Linked to {provider} artist successfully",
            "new_provider_id": new_provider_id
        })

    except Exception as e:
        logger.error(f"Error linking watchlist artist provider: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/watchlist/global-config', methods=['GET', 'POST'])
def watchlist_global_config():
    """Get or update global watchlist configuration (overrides per-artist settings)"""
    try:
        if request.method == 'GET':
            config = {
                'global_override_enabled': config_manager.get('watchlist.global_override_enabled', False),
                'include_albums': config_manager.get('watchlist.global_include_albums', True),
                'include_eps': config_manager.get('watchlist.global_include_eps', True),
                'include_singles': config_manager.get('watchlist.global_include_singles', True),
                'include_live': config_manager.get('watchlist.global_include_live', False),
                'include_remixes': config_manager.get('watchlist.global_include_remixes', False),
                'include_acoustic': config_manager.get('watchlist.global_include_acoustic', False),
                'include_compilations': config_manager.get('watchlist.global_include_compilations', False),
                'include_instrumentals': config_manager.get('watchlist.global_include_instrumentals', False),
                'exclude_terms': config_manager.get('watchlist.exclude_terms', ''),
                # Auto-download is a DEFAULT, not an override: an artist's own
                # setting beats it. Separate from global_override_enabled on
                # purpose -- folding it in would force auto-download on for
                # everyone already using that switch for formats.
                'global_auto_download': config_manager.get('watchlist.global_auto_download', True),
            }
            return jsonify({"success": True, "config": config})

        else:  # POST
            data = request.get_json()
            if not data:
                return jsonify({"success": False, "error": "No data provided"}), 400

            global_override_enabled = data.get('global_override_enabled', False)
            include_albums = data.get('include_albums', True)
            include_eps = data.get('include_eps', True)
            include_singles = data.get('include_singles', True)
            include_live = data.get('include_live', False)
            include_remixes = data.get('include_remixes', False)
            include_acoustic = data.get('include_acoustic', False)
            include_compilations = data.get('include_compilations', False)
            include_instrumentals = data.get('include_instrumentals', False)
            exclude_terms = data.get('exclude_terms', '')

            # When override is enabled, validate at least one release type
            if global_override_enabled and not (include_albums or include_eps or include_singles):
                return jsonify({"success": False, "error": "At least one release type must be selected"}), 400

            config_manager.set('watchlist.global_override_enabled', global_override_enabled)
            config_manager.set('watchlist.global_include_albums', include_albums)
            config_manager.set('watchlist.global_include_eps', include_eps)
            config_manager.set('watchlist.global_include_singles', include_singles)
            config_manager.set('watchlist.global_include_live', include_live)
            config_manager.set('watchlist.global_include_remixes', include_remixes)
            config_manager.set('watchlist.global_include_acoustic', include_acoustic)
            config_manager.set('watchlist.global_include_compilations', include_compilations)
            config_manager.set('watchlist.global_include_instrumentals', include_instrumentals)
            config_manager.set('watchlist.exclude_terms', exclude_terms)
            global_auto_download = bool(data.get('global_auto_download', True))
            config_manager.set('watchlist.global_auto_download', global_auto_download)

            logger.info(f"Updated global watchlist config: override={global_override_enabled}, "
                  f"albums={include_albums}, eps={include_eps}, singles={include_singles}, "
                  f"live={include_live}, remixes={include_remixes}, acoustic={include_acoustic}, "
                  f"compilations={include_compilations}, instrumentals={include_instrumentals}, "
                  f"exclude_terms='{exclude_terms}'")

            return jsonify({
                "success": True,
                "message": "Global watchlist configuration updated",
                "config": {
                    'global_override_enabled': global_override_enabled,
                    'include_albums': include_albums,
                    'include_eps': include_eps,
                    'include_singles': include_singles,
                    'include_live': include_live,
                    'include_remixes': include_remixes,
                    'include_acoustic': include_acoustic,
                    'include_compilations': include_compilations,
                    'include_instrumentals': include_instrumentals,
                    'exclude_terms': exclude_terms,
                    'global_auto_download': global_auto_download,
                }
            })

    except Exception as e:
        logger.error(f"Error in watchlist global config: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

def _update_similar_artists_worker():
    """Background worker to update similar artists for all watchlist artists"""
    global similar_artists_update_state

    try:
        from core.watchlist_scanner import get_watchlist_scanner
        from database.music_database import get_database
        import time

        logger.info("[Similar Artists] Starting similar artists update...")

        database = get_database()
        all_profiles = database.get_all_profiles()

        # Build per-profile artist lists and deduplicate for API calls
        # artist_key -> (artist_obj, [profile_ids])
        artist_profiles = {}
        for p in all_profiles:
            for artist in database.get_watchlist_artists(profile_id=p['id']):
                key = (artist.spotify_artist_id or '', artist.itunes_artist_id or '', artist.artist_name.lower())
                if key not in artist_profiles:
                    artist_profiles[key] = (artist, [])
                artist_profiles[key][1].append(p['id'])

        if not artist_profiles:
            similar_artists_update_state['status'] = 'completed'
            logger.warning("[Similar Artists] No watchlist artists to process")
            return

        similar_artists_update_state['total_artists'] = len(artist_profiles)
        logger.info(f"[Similar Artists] Processing {len(artist_profiles)} unique watchlist artists across {len(all_profiles)} profiles")

        scanner = get_watchlist_scanner(_spotify_client())

        for idx, (_key, (artist, profile_ids)) in enumerate(artist_profiles.items(), 1):
            try:
                similar_artists_update_state['artists_processed'] = idx
                similar_artists_update_state['current_artist'] = artist.artist_name

                logger.info(f"[{idx}/{len(artist_profiles)}] Updating similar artists for {artist.artist_name} (profiles: {profile_ids})")

                # Update similar artists for each profile that watches this artist
                for pid in profile_ids:
                    scanner.update_similar_artists(artist, limit=10, profile_id=pid)

                # Rate limiting
                if idx < len(artist_profiles):
                    time.sleep(2.0)  # 2 seconds between artists

            except Exception as artist_error:
                logger.error(f"[Similar Artists] Error processing {artist.artist_name}: {artist_error}")
                continue

        # Update complete
        similar_artists_update_state['status'] = 'completed'
        similar_artists_update_state['current_artist'] = None

        logger.info(f"[Similar Artists] Update complete! Processed {len(artist_profiles)} artists")

    except Exception as e:
        logger.error(f"[Similar Artists] Critical error: {e}")
        import traceback
        traceback.print_exc()

        similar_artists_update_state['status'] = 'error'
        similar_artists_update_state['error'] = str(e)

