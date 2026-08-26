"""Stats page endpoints, lifted out of web_server.py.

Read-only aggregation over the music DB for the Stats page (counts, top lists,
history charts) plus the listening-stats settings handful. Injected deps are
web_server's boot singletons; the automation engine is read through a getter
because it can be None when its init failed."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from urllib.parse import quote, urlparse

from flask import Blueprint, jsonify, request

from core.metadata import is_internal_image_host
from utils.logging_config import get_logger

logger = get_logger("api.stats")

bp = Blueprint("stats", __name__)

# Injected by configure() at boot.
get_database = None
config_manager = None
fix_artist_image_url = None
_automation_engine = lambda: None   # noqa: E731


_listening_stats_worker = lambda: None   # noqa: E731
_lastfm_import_worker = lambda: None     # noqa: E731


def configure(*, get_database, config_manager, fix_artist_image_url, _automation_engine,
              listening_stats_worker_getter, lastfm_import_worker_getter):
    globals()['get_database'] = get_database
    globals()['config_manager'] = config_manager
    globals()['fix_artist_image_url'] = fix_artist_image_url
    globals()['_automation_engine'] = _automation_engine
    globals()['_listening_stats_worker'] = listening_stats_worker_getter
    globals()['_lastfm_import_worker'] = lastfm_import_worker_getter


def create_blueprint():
    return bp


# --- Stats API Endpoints ---
# Logic lives in core/stats/queries.py — these routes are thin handlers.

from core.stats import queries as _stats_queries


@bp.route('/api/stats/cached', methods=['GET'])
def stats_cached():
    """Get all pre-computed stats for a time range from cache. Instant response."""
    try:
        time_range = request.args.get('range', '7d')
        data = _stats_queries.get_cached_stats(get_database(), fix_artist_image_url, time_range)
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/year', methods=['GET'])
def stats_year_in_listening():
    """Your Year in Listening — one fixed twelve-month period, not a range.

    No `range` argument on purpose: the period is the feature. Letting a
    caller narrow it would turn the story back into the filter the rest of
    the page already provides."""
    try:
        data = _stats_queries.get_year_in_listening(get_database(), fix_artist_image_url)
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/stats/album-tracks/<album_id>', methods=['GET'])
def stats_album_tracks(album_id):
    """An owned album's tracks, ready for the media player's queue.

    Exists so a card on a stats surface can start playback without the page
    having to know the player's row shape."""
    try:
        tracks = _stats_queries.get_album_play_tracks(
            get_database(), album_id, fix_artist_image_url,
        )
        return jsonify({'success': True, 'tracks': tracks})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/stats/overview', methods=['GET'])
def stats_overview():
    """Get aggregate listening stats for a time range."""
    try:
        time_range = request.args.get('range', 'all')
        data = _stats_queries.get_overview(get_database(), time_range)
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/top-artists', methods=['GET'])
def stats_top_artists():
    """Get top artists by play count."""
    try:
        time_range = request.args.get('range', 'all')
        limit = int(request.args.get('limit', 10))
        artists = _stats_queries.get_top_artists(get_database(), fix_artist_image_url, time_range, limit)
        return jsonify({'success': True, 'artists': artists})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/top-albums', methods=['GET'])
def stats_top_albums():
    """Get top albums by play count."""
    try:
        time_range = request.args.get('range', 'all')
        limit = int(request.args.get('limit', 10))
        albums = _stats_queries.get_top_albums(get_database(), fix_artist_image_url, time_range, limit)
        return jsonify({'success': True, 'albums': albums})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/top-tracks', methods=['GET'])
def stats_top_tracks():
    """Get top tracks by play count."""
    try:
        time_range = request.args.get('range', 'all')
        limit = int(request.args.get('limit', 10))
        tracks = _stats_queries.get_top_tracks(get_database(), fix_artist_image_url, time_range, limit)
        return jsonify({'success': True, 'tracks': tracks})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/timeline', methods=['GET'])
def stats_timeline():
    """Get play count per time period for chart rendering."""
    try:
        time_range = request.args.get('range', '30d')
        granularity = request.args.get('granularity', 'day')
        data = _stats_queries.get_timeline(get_database(), time_range, granularity)
        return jsonify({'success': True, 'timeline': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/genres', methods=['GET'])
def stats_genres():
    """Get genre distribution by play count."""
    try:
        time_range = request.args.get('range', 'all')
        data = _stats_queries.get_genres(get_database(), time_range)
        return jsonify({'success': True, 'genres': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/library-health', methods=['GET'])
def stats_library_health():
    """Get library health metrics."""
    try:
        data = _stats_queries.get_library_health(get_database())
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/db-storage', methods=['GET'])
def stats_db_storage():
    """Get database storage breakdown by table."""
    try:
        data = _stats_queries.get_db_storage(get_database())
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/library-disk-usage', methods=['GET'])
def stats_library_disk_usage():
    """Library on-disk size + per-format breakdown.

    Reads `tracks.file_size` populated by the deep scan from data the
    media server already returns. Returns ``has_data: false`` on fresh
    installs that haven't run a deep scan since the migration — UI
    shows "Run a Deep Scan to populate" in that case.
    """
    try:
        data = _stats_queries.get_library_disk_usage(get_database())
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/recent', methods=['GET'])
def stats_recent():
    """Get recently played tracks."""
    try:
        limit = int(request.args.get('limit', 20))
        tracks = _stats_queries.get_recent_tracks(get_database(), limit, fix_artist_image_url)
        return jsonify({'success': True, 'tracks': tracks})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _fix_stats_listening_image_url(thumb_url):
    """Fast browser-safe image URL fixer for stats detail rows.

    The normal metadata image fixer also registers remote URLs with the image
    cache. That is useful for high-value surfaces, but expensive when a details
    modal serializes 100 listening-history rows. This keeps local media-server
    artwork reachable without doing cache work per row.
    """
    if not thumb_url:
        return None
    url = str(thumb_url)
    if url.startswith('/api/image-proxy?url=') or url.startswith('/api/image-cache/'):
        return url

    try:
        active_server = config_manager.get_active_media_server()
        path = None
        fixed_url = None

        if url.startswith('/library/') or url.startswith('/Items/') or url.startswith('/api/') or url.startswith('/rest/'):
            path = url
        elif url.startswith('http://') or url.startswith('https://'):
            parsed = urlparse(url)
            if is_internal_image_host(url):
                path = parsed.path
            else:
                return url

        if path and active_server == 'plex':
            plex_config = config_manager.get_plex_config()
            base_url = plex_config.get('base_url', '')
            token = plex_config.get('token', '')
            if base_url and token:
                fixed_url = f"{base_url.rstrip('/')}{path}?X-Plex-Token={token}"
        elif path and active_server == 'jellyfin':
            jellyfin_config = config_manager.get_jellyfin_config()
            base_url = jellyfin_config.get('base_url', '')
            token = jellyfin_config.get('api_key', '')
            if base_url:
                separator = '&' if '?' in path else '?'
                suffix = f"{separator}X-Emby-Token={token}" if token else ''
                fixed_url = f"{base_url.rstrip('/')}{path}{suffix}"
        elif path and active_server == 'navidrome':
            navidrome_config = config_manager.get_navidrome_config()
            base_url = navidrome_config.get('base_url', '')
            username = navidrome_config.get('username', '')
            password = navidrome_config.get('password', '')
            if base_url and username and password:
                import hashlib
                import secrets

                salt = secrets.token_hex(6)
                token = hashlib.md5((password + salt).encode()).hexdigest()
                separator = '&' if '?' in path else '?'
                auth = f"u={username}&t={token}&s={salt}&v=1.16.1&c=SoulSync&f=json"
                fixed_url = f"{base_url.rstrip('/')}{path}{separator}{auth}"
        elif url.startswith('http://') or url.startswith('https://'):
            fixed_url = url

        if fixed_url and is_internal_image_host(fixed_url):
            return f"/api/image-proxy?url={quote(fixed_url, safe='')}"
        return fixed_url or url
    except Exception as e:
        logger.debug("stats listening image URL normalization failed: %s", e)
        return url

@bp.route('/api/stats/listening-events', methods=['GET'])
def stats_listening_events():
    """Rows behind a clicked listening stats chart segment."""
    try:
        time_range = request.args.get('range', '7d')
        filter_type = request.args.get('type', '')
        date = request.args.get('date') or None
        limit = int(request.args.get('limit', 100))
        weekday_arg = request.args.get('weekday')
        hour_arg = request.args.get('hour')
        weekday = int(weekday_arg) if weekday_arg is not None and weekday_arg != '' else None
        hour = int(hour_arg) if hour_arg is not None and hour_arg != '' else None
        data = _stats_queries.get_listening_events(
            get_database(),
            _fix_stats_listening_image_url,
            time_range=time_range,
            filter_type=filter_type,
            date=date,
            weekday=weekday,
            hour=hour,
            limit=limit,
        )
        return jsonify({'success': True, **data})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/lyrics/fetch', methods=['POST'])
def fetch_lyrics_endpoint():
    """Fetch lyrics for the now-playing media player.

    Body: ``{title, artist, album?, duration?}``. Returns
    ``{success, synced, plain, source}`` where ``synced`` is an LRC
    string with ``[mm:ss.xx] line`` timestamps (or None) and ``plain``
    is the untimestamped text (or None). ``source`` is the lookup
    strategy that hit (``exact`` / ``search`` / ``sidecar``).

    Tries the local ``.lrc`` / ``.txt`` sidecar first when a
    ``file_path`` is supplied — already-downloaded tracks should not
    bounce LRClib on every play. Falls through to LRClib's exact-
    match endpoint when title+artist+album+duration are all available,
    then to its generic search endpoint.
    """
    try:
        data = request.get_json() or {}
        title = (data.get('title') or '').strip()
        artist = (data.get('artist') or '').strip()
        album = (data.get('album') or '').strip() or None
        try:
            duration = int(data.get('duration') or 0) or None
        except (TypeError, ValueError):
            duration = None
        file_path = data.get('file_path') or None

        if not title or not artist:
            return jsonify({'success': False, 'error': 'title and artist required',
                            'synced': None, 'plain': None, 'source': None}), 400

        # 1. Sidecar — fastest, no network. The post-processing flow
        #    drops .lrc / .txt next to the audio for every successful
        #    enrichment, so existing downloads almost always have one.
        if file_path:
            try:
                import os as _os
                stem, _ = _os.path.splitext(file_path)
                lrc_path = stem + '.lrc'
                txt_path = stem + '.txt'
                if _os.path.exists(lrc_path):
                    with open(lrc_path, 'r', encoding='utf-8') as fh:
                        body = fh.read().strip()
                    if body:
                        return jsonify({'success': True, 'synced': body,
                                        'plain': None, 'source': 'sidecar'})
                if _os.path.exists(txt_path):
                    with open(txt_path, 'r', encoding='utf-8') as fh:
                        body = fh.read().strip()
                    if body:
                        return jsonify({'success': True, 'synced': None,
                                        'plain': body, 'source': 'sidecar'})
            except Exception as sidecar_err:
                logger.debug("lyrics sidecar read skipped: %s", sidecar_err)

        # 2. LRClib network lookup via the shared client instance.
        from core.lyrics_client import lyrics_client as _lyrics_client
        api = getattr(_lyrics_client, 'api', None)
        if api is None:
            return jsonify({'success': False, 'error': 'lrclib unavailable',
                            'synced': None, 'plain': None, 'source': None}), 200

        result = None
        # Exact-match endpoint requires all four fields. LRClib's API
        # will 404 on any miss; treat as soft failure and fall through
        # to the search endpoint.
        if album and duration:
            try:
                result = api.get_lyrics(track_name=title, artist_name=artist,
                                        album_name=album, duration=duration)
            except Exception as exact_err:
                logger.debug("lrclib exact lookup failed: %s", exact_err)

        if result is None:
            try:
                hits = api.search_lyrics(track_name=title, artist_name=artist)
                if hits:
                    result = hits[0]
            except Exception as search_err:
                logger.debug("lrclib search lookup failed: %s", search_err)

        if result is None:
            return jsonify({'success': False, 'error': 'no lyrics found',
                            'synced': None, 'plain': None, 'source': None})

        synced = getattr(result, 'synced_lyrics', None) or None
        plain = getattr(result, 'plain_lyrics', None) or None
        return jsonify({'success': bool(synced or plain), 'synced': synced,
                        'plain': plain, 'source': 'lrclib'})
    except Exception as e:
        logger.error("lyrics fetch failed: %s", e)
        return jsonify({'success': False, 'error': str(e),
                        'synced': None, 'plain': None, 'source': None}), 500


@bp.route('/api/stats/resolve-track', methods=['POST'])
def stats_resolve_track():
    """Resolve a track by title+artist to get its file_path for playback."""
    try:
        data = request.get_json()
        title = data.get('title', '')
        artist = data.get('artist', '')
        if not title:
            return jsonify({'success': False, 'error': 'Title required'}), 400

        track = _stats_queries.resolve_track(get_database(), fix_artist_image_url, title, artist)
        if track is None:
            return jsonify({'success': False, 'error': 'Track not found in library'})
        return jsonify({'success': True, 'track': track})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/listening-stats/sync', methods=['POST'])
def listening_stats_sync():
    """Trigger an immediate listening stats poll."""
    try:
        if not _listening_stats_worker():
            return jsonify({'success': False, 'error': 'Listening stats worker not initialized'}), 400
        _stats_queries.trigger_listening_sync(_listening_stats_worker())
        return jsonify({'success': True, 'message': 'Sync started'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/listening-stats/status', methods=['GET'])
def listening_stats_status():
    """Get listening stats worker status."""
    try:
        return jsonify(_stats_queries.get_listening_status(_listening_stats_worker()))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/lastfm/listening-import/status', methods=['GET'])
def lastfm_listening_import_status():
    """Get Last.fm listening-history import status."""
    try:
        if not _lastfm_import_worker():
            return jsonify({'success': False, 'enabled': False, 'error': 'Last.fm importer unavailable'})
        status = _lastfm_import_worker().status()
        next_run = _automation_engine().get_system_automation_next_run_seconds('import_lastfm_listening') if _automation_engine() else 0
        username = config_manager.get('lastfm.username', '') or status.get('username') or ''
        can_use_auth_user = bool(
            config_manager.get('lastfm.api_key', '')
            and config_manager.get('lastfm.api_secret', '')
            and config_manager.get('lastfm.session_key', '')
        )
        return jsonify({
            'success': True,
            'enabled': bool(config_manager.get('lastfm.listening_sync_enabled', False)),
            'api_key_configured': bool(config_manager.get('lastfm.api_key', '')),
            'authenticated_user_available': can_use_auth_user,
            'username': username,
            'next_run_in_seconds': next_run,
            **status,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/lastfm/listening-import/run', methods=['POST'])
def lastfm_listening_import_run():
    """Start or update the Last.fm listening-history import."""
    try:
        if not _lastfm_import_worker():
            return jsonify({'success': False, 'error': 'Last.fm importer unavailable'}), 400
        body = request.get_json(silent=True) or {}
        username = str(body.get('username') or config_manager.get('lastfm.username', '') or '').strip()
        if username:
            config_manager.set('lastfm.username', username)
        if 'enabled' in body:
            config_manager.set('lastfm.listening_sync_enabled', bool(body.get('enabled')))
        result = _lastfm_import_worker().start_import(username=username or None, full=bool(body.get('full')))
        ok = result.get('status') not in ('error',)
        return jsonify({'success': ok, **result}), 200 if ok else 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/lastfm/listening-import/cancel', methods=['POST'])
def lastfm_listening_import_cancel():
    """Cancel the active Last.fm listening-history import."""
    try:
        if not _lastfm_import_worker():
            return jsonify({'success': False, 'error': 'Last.fm importer unavailable'}), 400
        _lastfm_import_worker().cancel()
        return jsonify({'success': True, **_lastfm_import_worker().status()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

