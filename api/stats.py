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

from core.connect.device_access import device_playback
from core.metadata import is_internal_image_host
from core.profile_context import get_current_profile_id, is_admin_request
from utils.logging_config import get_logger
from core.permissions import requires, signed_in
from core.profile_context import admin_only

logger = get_logger("api.stats")

bp = Blueprint("stats", __name__)

# Injected by configure() at boot.
get_database = None
config_manager = None
fix_artist_image_url = None
_automation_engine = lambda: None   # noqa: E731


_listening_stats_worker = lambda: None   # noqa: E731
_lastfm_import_worker = lambda: None     # noqa: E731
_listenbrainz_import_worker = lambda: None # noqa: E731
_listenbrainz_import_workers = lambda: None # noqa: E731
_lastfm_import_workers = lambda: None     # noqa: E731


def configure(*, get_database, config_manager, fix_artist_image_url, _automation_engine,
              listening_stats_worker_getter, lastfm_import_worker_getter,
              listenbrainz_import_worker_getter=lambda: None,
              listenbrainz_import_workers_getter=lambda: None,
              lastfm_import_workers_getter=lambda: None):
    globals()['get_database'] = get_database
    globals()['config_manager'] = config_manager
    globals()['fix_artist_image_url'] = fix_artist_image_url
    globals()['_automation_engine'] = _automation_engine
    globals()['_listening_stats_worker'] = listening_stats_worker_getter
    globals()['_lastfm_import_worker'] = lastfm_import_worker_getter
    globals()['_listenbrainz_import_worker'] = listenbrainz_import_worker_getter
    globals()['_listenbrainz_import_workers'] = listenbrainz_import_workers_getter
    globals()['_lastfm_import_workers'] = lastfm_import_workers_getter


def create_blueprint():
    return bp


# --- Stats API Endpoints ---
# Logic lives in core/stats/queries.py — these routes are thin handlers.

from core.stats import queries as _stats_queries


@bp.route('/api/stats/cached', methods=['GET'])
@device_playback
@requires("music.access")
def stats_cached():
    """Get all pre-computed stats for a time range from cache. Instant response."""
    try:
        time_range = request.args.get('range', '7d')
        data = _stats_queries.get_cached_stats(get_database(), fix_artist_image_url, time_range,
                                               profile_id=get_current_profile_id())
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/year', methods=['GET'])
@requires("music.stats")
def stats_year_in_listening():
    """Your Year in Listening — one fixed twelve-month period, not a range.

    No `range` argument on purpose: the period is the feature. Letting a
    caller narrow it would turn the story back into the filter the rest of
    the page already provides."""
    try:
        data = _stats_queries.get_year_in_listening(get_database(), fix_artist_image_url,
                                                    profile_id=get_current_profile_id())
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/stats/year/playlist', methods=['POST'])
@requires("music.stats")
def stats_year_playlist():
    """Save your year as a playlist: the year's top 50 (or 100) songs into a
    user playlist named "Your <year>". Saving again updates the same one."""
    from core.playlists.user_playlists import UserPlaylistError
    from core.stats import year_extras
    try:
        body = request.get_json(silent=True) or {}
        size = year_extras.clamp_size(body.get('size'))
        database = get_database()
        pid = get_current_profile_id()
        tracks = database.get_year_top_tracks(limit=size, profile_id=pid)
        year = year_extras.story_year(datetime.now().strftime('%Y-%m-%d'))
        saved = year_extras.save_year_playlist(database, pid, year, tracks)
        return jsonify({'success': True, **saved})
    except UserPlaylistError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error("saving the year playlist failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not save the playlist'}), 500


@bp.route('/api/stats/year/soundtrack', methods=['GET'])
@requires("music.stats")
def stats_year_soundtrack():
    """The story's soundtrack: the year's most played songs you own, in the
    player's queue shape, each with where to start it."""
    from core.stats import year_extras
    try:
        database = get_database()
        tracks = database.get_year_top_tracks(limit=25, profile_id=get_current_profile_id())
        rows = year_extras.soundtrack_rows(database, tracks, fix_artist_image_url)
        return jsonify({'success': True, 'tracks': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/stats/year/banner', methods=['GET'])
@requires("music.stats")
def stats_year_banner():
    """{show, year}: the dashboard's "Your <year> is ready", Dec 1 to Jan 15,
    until this profile dismisses it. The first time it shows, the profile
    also gets one bell note."""
    from core.stats import year_extras
    try:
        database = get_database()
        pid = get_current_profile_id()
        state = year_extras.banner_state(database, pid, today=year_extras.today_local())
        if state['show']:
            has_plays = bool(database.get_year_top_tracks(limit=1, profile_id=pid))
            state = year_extras.banner_state(database, pid, today=year_extras.today_local(),
                                             has_plays=has_plays)
        if state['show']:
            from core.profile_notify import notify_profile
            year_extras.notify_once(database, pid, state['year'], notify_profile)
        return jsonify({'success': True, **state})
    except Exception as e:
        return jsonify({'success': False, 'show': False, 'error': str(e)}), 500


@bp.route('/api/stats/year/banner/dismiss', methods=['POST'])
@requires("music.stats")
def stats_year_banner_dismiss():
    from core.stats import year_extras
    year = year_extras.season_year(year_extras.today_local())
    if year is not None:
        year_extras.dismiss_banner(get_database(), get_current_profile_id(), year)
    return jsonify({'success': True})


@bp.route('/api/stats/album-tracks/<album_id>', methods=['GET'])
@requires("music.access")
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
@requires("music.stats")
def stats_overview():
    """Get aggregate listening stats for a time range."""
    try:
        time_range = request.args.get('range', 'all')
        data = _stats_queries.get_overview(get_database(), time_range, profile_id=get_current_profile_id())
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/top-artists', methods=['GET'])
@device_playback
@requires("music.stats")
def stats_top_artists():
    """Get top artists by play count."""
    try:
        time_range = request.args.get('range', 'all')
        limit = int(request.args.get('limit', 10))
        artists = _stats_queries.get_top_artists(get_database(), fix_artist_image_url, time_range, limit,
                                                 profile_id=get_current_profile_id())
        return jsonify({'success': True, 'artists': artists})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/top-albums', methods=['GET'])
@device_playback
@requires("music.stats")
def stats_top_albums():
    """Get top albums by play count."""
    try:
        time_range = request.args.get('range', 'all')
        limit = int(request.args.get('limit', 10))
        albums = _stats_queries.get_top_albums(get_database(), fix_artist_image_url, time_range, limit,
                                               profile_id=get_current_profile_id())
        return jsonify({'success': True, 'albums': albums})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/top-tracks', methods=['GET'])
@device_playback
@requires("music.stats")
def stats_top_tracks():
    """Get top tracks by play count."""
    try:
        time_range = request.args.get('range', 'all')
        limit = int(request.args.get('limit', 10))
        tracks = _stats_queries.get_top_tracks(get_database(), fix_artist_image_url, time_range, limit,
                                               profile_id=get_current_profile_id())
        return jsonify({'success': True, 'tracks': tracks})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/timeline', methods=['GET'])
@requires("music.stats")
def stats_timeline():
    """Get play count per time period for chart rendering."""
    try:
        time_range = request.args.get('range', '30d')
        granularity = request.args.get('granularity', 'day')
        data = _stats_queries.get_timeline(get_database(), time_range, granularity,
                                           profile_id=get_current_profile_id())
        return jsonify({'success': True, 'timeline': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/genres', methods=['GET'])
@device_playback
@requires("music.stats")
def stats_genres():
    """Get genre distribution by play count."""
    try:
        time_range = request.args.get('range', 'all')
        data = _stats_queries.get_genres(get_database(), time_range, profile_id=get_current_profile_id())
        return jsonify({'success': True, 'genres': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/library-health', methods=['GET'])
@requires("music.stats")
def stats_library_health():
    """Get library health metrics."""
    try:
        data = _stats_queries.get_library_health(get_database())
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/db-storage', methods=['GET'])
@requires("music.stats")
def stats_db_storage():
    """Get database storage breakdown by table."""
    try:
        data = _stats_queries.get_db_storage(get_database())
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/library-disk-usage', methods=['GET'])
@requires("music.stats")
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
@device_playback
@requires("music.access")
def stats_recent():
    """Get recently played tracks."""
    try:
        limit = int(request.args.get('limit', 20))
        tracks = _stats_queries.get_recent_tracks(get_database(), limit, fix_artist_image_url,
                                                  profile_id=get_current_profile_id())
        return jsonify({'success': True, 'tracks': tracks})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/stats/weekly-digest', methods=['GET'])
@requires("music.access")
def stats_weekly_digest():
    """This-week listening digest for the dashboard banner."""
    try:
        data = _stats_queries.get_weekly_digest(get_database(),
                                                profile_id=get_current_profile_id())
        return jsonify({'success': True, **data})
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
@requires("music.stats")
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
            profile_id=get_current_profile_id(),
        )
        return jsonify({'success': True, **data})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def _lyrics_local_path(file_path):
    """The library file the player sent, as a path on this machine.

    The stored path can be a media server's view of the file (another
    mount, another OS); the shared resolver maps it the same way playback
    does. None when it can't be found.
    """
    if not file_path or not isinstance(file_path, str):
        return None
    import os as _os
    if _os.path.isfile(file_path):
        return file_path
    try:
        from core.library.path_resolver import resolve_library_file_path
        return resolve_library_file_path(file_path, config_manager=config_manager,
                                         allow_fuzzy=False)
    except Exception as e:  # noqa: BLE001 - a miss falls through to LRCLib
        logger.debug("lyrics path resolve skipped: %s", e)
        return None


@bp.route('/api/lyrics/fetch', methods=['POST'])
@requires("music.access")
def fetch_lyrics_endpoint():
    """Fetch lyrics for the now-playing media player.

    Body: ``{title, artist, album?, duration?}``. Returns
    ``{success, synced, plain, source}`` where ``synced`` is an LRC
    string with ``[mm:ss.xx] line`` timestamps (or None) and ``plain``
    is the untimestamped text (or None). ``source`` is ``sidecar`` for
    a local lyrics file, ``embedded`` for lyrics in the file's tags, or
    ``lrclib`` for a network hit (else None). An LRC ``[offset:]`` tag is
    folded into the stamps. An LRCLib hit for a library file is saved as
    a sidecar when the setting allows (``cached: true``).
    A transient LRCLib failure from the lookup returns
    ``success: False`` with the real reason in ``error`` and
    ``retryable: True`` for transient kinds (#1879) — it is NOT the
    same response as a definitive ``no lyrics found``.

    Tries the local sidecar, then the embedded tags, when a ``file_path``
    is supplied. Falls through to the shared LyricsClient's exact-match
    then search lookup.
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
        out, status = lyrics_answer(title, artist, album, duration,
                                    _lyrics_local_path(data.get('file_path') or None))
        return jsonify(out), status
    except Exception as e:
        logger.error("lyrics fetch failed: %s", e)
        return jsonify({'success': False, 'error': str(e),
                        'synced': None, 'plain': None, 'source': None}), 500


def lyrics_answer(title, artist, album=None, duration=None, local_path=None):
    """the lyrics for a song: ``(body, status)`` in the shape the lyrics
    route answers (see fetch_lyrics_endpoint). ``local_path`` is the
    library file on this machine, when there is one: its sidecar and tags
    come first, and an LRCLib hit is cached beside it. shared with the
    player, which asks by track id instead of sending a path."""
    none = {'synced': None, 'plain': None, 'source': None}
    if not title or not artist:
        return {'success': False, 'error': 'title and artist required', **none}, 400

    # 1. Local lyrics: the .lrc / .txt sidecar, then the lyrics in the
    #    file's own tags (USLT/SYLT, Vorbis LYRICS, MP4 lyr). Already
    #    downloaded tracks should not bounce LRCLib on every play, and
    #    libraries tagged elsewhere often carry embedded lyrics only.
    from core import lyrics_local
    synced, plain, source = lyrics_local.read_local(local_path)
    if synced or plain:
        return {'success': True, 'synced': synced, 'plain': plain, 'source': source}, 200

    # 2. LRClib network lookup via the shared client instance.
    # Go through the client's tri-state lookup (#1879) instead of
    # calling the raw api directly: the client paces requests,
    # retries transient failures honouring Retry-After, and can tell
    # "LRCLib has no lyrics" (404) apart from "LRCLib rate-limited /
    # 503 — retry later", which is surfaced as the error here instead
    # of a misleading 'no lyrics found'.
    from core.lyrics_client import lyrics_client as _lyrics_client
    if getattr(_lyrics_client, 'api', None) is None:
        return {'success': False, 'error': 'LRCLib unavailable', **none}, 200

    # Interactive caller: cap each throttle wait at 10s so a player
    # request can never park an HTTP worker for the length of a
    # rate-limit cooldown (which can exceed the gunicorn timeout).
    # The cap is per reservation, not per request — a lookup that
    # falls through to search waits at most 10s per strategy.
    fetch = _lyrics_client.lookup_remote_lyrics(
        track_name=title, artist_name=artist,
        album_name=album, duration_seconds=duration,
        max_wait_seconds=10)

    if fetch is None:
        # A substituted/legacy client returning the old contract's
        # None (unknown) — answer with the soft unavailable body,
        # not a 500 from dereferencing it below.
        return {'success': False, 'error': 'LRCLib unavailable', **none}, 200

    if fetch.status == 'unknown':
        # retryable only for genuinely transient kinds — a permanent
        # query error (other 4xx) won't succeed on a retry (#1879).
        return {'success': False, 'error': fetch.reason or 'lrclib lookup failed', **none,
                'retryable': fetch.error_kind in ('rate_limited', 'server', 'timeout', 'network')}, 200

    result = fetch.data if fetch.status == 'found' else None
    if result is None:
        return {'success': False, 'error': 'no lyrics found', **none}, 200

    synced = getattr(result, 'synced_lyrics', None) or None
    plain = getattr(result, 'plain_lyrics', None) or None
    if not synced and not plain:
        # A record with no lyrics content is a definitive negative,
        # same as a 404 — say so explicitly instead of returning a
        # success flag with no error and no lyrics.
        return {'success': False, 'error': 'no lyrics found', **none}, 200
    cached = False
    if local_path:
        try:
            cached = lyrics_local.cache_sidecar(local_path, synced, plain, config_manager)
        except Exception as cache_err:  # noqa: BLE001 - caching never fails playback
            logger.debug("lyrics sidecar cache skipped: %s", cache_err)
    return {'success': True, 'synced': lyrics_local.apply_lrc_offset(synced),
            'plain': plain, 'source': 'lrclib', 'cached': cached}, 200


@bp.route('/api/stats/resolve-track', methods=['POST'])
@device_playback
@requires("music.access")
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
@requires("music.stats")
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
@requires("music.stats")
def listening_stats_status():
    """Get listening stats worker status."""
    try:
        return jsonify(_stats_queries.get_listening_status(_listening_stats_worker()))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _import_target(service, shared_getter, workers_getter):
    """which importer the current profile drives on the stats page (#1293).

    ('shared', worker) for the admin and every profile on the shared history,
    same as it always was. ('own', worker) for a profile with its own account
    on this service. ('none', None) when the profile has its own history but
    not from this service, the card isn't theirs to run.
    """
    from core.listening_scope import SHARED_OWNER, has_own_account, listening_owner
    workers = workers_getter()
    if workers is None:
        return 'shared', shared_getter()
    owner = listening_owner(get_database(), get_current_profile_id())
    if owner == SHARED_OWNER:
        return 'shared', shared_getter()
    if has_own_account(get_database(), owner, service):
        return 'own', workers.for_owner(owner)
    return 'none', None


_SERVICE_LABEL = {'lastfm': 'Last.fm', 'listenbrainz': 'ListenBrainz'}


def _run_shared_as_member(worker, body, service):
    """a member on the shared history runs the shared import as the admin set
    it up. the account and the schedule switch are install-wide settings, so
    a member doesn't write them: a different username is refused, ``enabled``
    is left alone."""
    configured = str(config_manager.get(f'{service}.username', '') or '').strip()
    asked = str(body.get('username') or '').strip()
    if asked and asked.casefold() != configured.casefold():
        label = _SERVICE_LABEL.get(service, service)
        return jsonify({'success': False,
                        'error': f"The shared history's {label} account is the admin's to change. "
                                 f"Connect your own {label} in My Accounts."}), 403
    result = worker.start_import(username=configured or None, full=bool(body.get('full')))
    ok = result.get('status') not in ('error',)
    return jsonify({'success': ok, **result}), 200 if ok else 400


def _own_status(worker, username, next_run):
    """the card for a profile running its own account, hourly while connected."""
    status = worker.status()
    return {
        'success': True,
        'enabled': True,
        'api_key_configured': True,
        'token_configured': True,
        'authenticated_user_available': True,
        'next_run_in_seconds': next_run,
        **status,
        'username': username or status.get('username') or '',
        'history_scope': 'profile',
        'own_account': True,
    }


# a profile whose history comes from the other service: nothing to show here
_NOT_YOUR_CARD = {'success': True, 'status': 'idle', 'history_scope': 'profile', 'own_account': False}


def _run_own(worker, body):
    # their own account, and nothing in the admin's settings moves
    result = worker.start_import(full=bool(body.get('full')))
    ok = result.get('status') not in ('error',)
    return jsonify({'success': ok, **result}), 200 if ok else 400


@bp.route('/api/lastfm/listening-import/status', methods=['GET'])
@signed_in
def lastfm_listening_import_status():
    """Get Last.fm listening-history import status."""
    try:
        if not _lastfm_import_worker():
            return jsonify({'success': False, 'enabled': False, 'error': 'Last.fm importer unavailable'})
        next_run = _automation_engine().get_system_automation_next_run_seconds('import_lastfm_listening') if _automation_engine() else 0
        kind, worker = _import_target('lastfm', _lastfm_import_worker, _lastfm_import_workers)
        if kind == 'none':
            return jsonify(_NOT_YOUR_CARD)
        if kind == 'own':
            saved = get_database().get_profile_lastfm(worker.profile_id) or {}
            return jsonify(_own_status(worker, saved.get('username'), next_run))
        status = worker.status()
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
            'next_run_in_seconds': next_run,
            **status,
            'username': username,
            'history_scope': 'shared',
            'own_account': False,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/lastfm/listening-import/run', methods=['POST'])
@requires("music.stats")
def lastfm_listening_import_run():
    """Start or update the Last.fm listening-history import."""
    try:
        if not _lastfm_import_worker():
            return jsonify({'success': False, 'error': 'Last.fm importer unavailable'}), 400
        body = request.get_json(silent=True) or {}
        kind, worker = _import_target('lastfm', _lastfm_import_worker, _lastfm_import_workers)
        if kind == 'none':
            return jsonify({'success': False, 'error': 'Connect your Last.fm in My Accounts first'}), 400
        if kind == 'own':
            return _run_own(worker, body)
        if not is_admin_request():
            return _run_shared_as_member(worker, body, 'lastfm')
        username = str(body.get('username') or config_manager.get('lastfm.username', '') or '').strip()
        if username:
            config_manager.set('lastfm.username', username)
        if 'enabled' in body:
            config_manager.set('lastfm.listening_sync_enabled', bool(body.get('enabled')))
        result = worker.start_import(username=username or None, full=bool(body.get('full')))
        ok = result.get('status') not in ('error',)
        return jsonify({'success': ok, **result}), 200 if ok else 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/lastfm/listening-import/cancel', methods=['POST'])
@requires("music.stats")
def lastfm_listening_import_cancel():
    """Cancel the active Last.fm listening-history import."""
    try:
        if not _lastfm_import_worker():
            return jsonify({'success': False, 'error': 'Last.fm importer unavailable'}), 400
        kind, worker = _import_target('lastfm', _lastfm_import_worker, _lastfm_import_workers)
        if kind == 'none':
            return jsonify(_NOT_YOUR_CARD)
        worker.cancel()
        return jsonify({'success': True, **worker.status()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/listenbrainz/listening-import/status', methods=['GET'])
@signed_in
def listenbrainz_listening_import_status():
    """Get ListenBrainz listening-history import status."""
    try:
        if not _listenbrainz_import_worker():
            return jsonify({'success': False, 'enabled': False, 'error': 'ListenBrainz importer unavailable'})
        next_run = _automation_engine().get_system_automation_next_run_seconds('import_listenbrainz_listening') if _automation_engine() else 0
        kind, worker = _import_target('listenbrainz', _listenbrainz_import_worker, _listenbrainz_import_workers)
        if kind == 'none':
            return jsonify(_NOT_YOUR_CARD)
        if kind == 'own':
            saved = get_database().get_profile_listenbrainz(worker.profile_id) or {}
            return jsonify(_own_status(worker, saved.get('username'), next_run))
        status = worker.status()
        username = config_manager.get('listenbrainz.username', '') or status.get('username') or ''
        has_token = bool(config_manager.get('listenbrainz.token', ''))
        return jsonify({
            'success': True,
            'enabled': bool(config_manager.get('listenbrainz.listening_sync_enabled', False)),
            'token_configured': has_token,
            'authenticated_user_available': bool(has_token or username),
            'next_run_in_seconds': next_run,
            **status,
            'username': username,
            'history_scope': 'shared',
            'own_account': False,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/listenbrainz/listening-import/run', methods=['POST'])
@requires("music.stats")
def listenbrainz_listening_import_run():
    """Start or update the ListenBrainz listening-history import."""
    try:
        if not _listenbrainz_import_worker():
            return jsonify({'success': False, 'error': 'ListenBrainz importer unavailable'}), 400
        body = request.get_json(silent=True) or {}
        kind, worker = _import_target('listenbrainz', _listenbrainz_import_worker, _listenbrainz_import_workers)
        if kind == 'none':
            return jsonify({'success': False, 'error': 'Connect your ListenBrainz in My Accounts first'}), 400
        if kind == 'own':
            return _run_own(worker, body)
        if not is_admin_request():
            return _run_shared_as_member(worker, body, 'listenbrainz')
        username = str(body.get('username') or config_manager.get('listenbrainz.username', '') or '').strip()
        if username:
            config_manager.set('listenbrainz.username', username)
        if 'enabled' in body:
            config_manager.set('listenbrainz.listening_sync_enabled', bool(body.get('enabled')))
        result = worker.start_import(username=username or None, full=bool(body.get('full')))
        ok = result.get('status') not in ('error',)
        return jsonify({'success': ok, **result}), 200 if ok else 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/listenbrainz/listening-import/cancel', methods=['POST'])
@requires("music.stats")
def listenbrainz_listening_import_cancel():
    """Cancel the active ListenBrainz listening-history import."""
    try:
        if not _listenbrainz_import_worker():
            return jsonify({'success': False, 'error': 'ListenBrainz importer unavailable'}), 400
        kind, worker = _import_target('listenbrainz', _listenbrainz_import_worker, _listenbrainz_import_workers)
        if kind == 'none':
            return jsonify(_NOT_YOUR_CARD)
        worker.cancel()
        return jsonify({'success': True, **worker.status()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- Spotify streaming history (privacy export) ---
# the browser reads the export and sends parsed music plays in batches; the
# file itself never comes here. every route works on the caller's own pile:
# the admin's import is the shared history, a member's is their own.

def _rebuild_pile_cache(owner):
    """refresh one pile's stats cache in the background after its imported
    history changed, so the page shows it without waiting for the next poll"""
    worker = _listening_stats_worker()
    build = getattr(worker, '_build_stats_cache', None) if worker else None
    if build is None:
        return

    def run():
        try:
            build(owners=[owner])
        except Exception as e:  # noqa: BLE001 - the next poll rebuilds it anyway
            logger.debug("stats cache rebuild after spotify import failed: %s", e)

    threading.Thread(target=run, name='spotify-history-cache', daemon=True).start()


def _spotify_summary_payload():
    from core.listening_import import spotify_export
    return {'success': True, **spotify_export.summary(get_database(), get_current_profile_id()),
            'max_batch': spotify_export.MAX_BATCH}


@bp.route('/api/listening-import/spotify', methods=['GET'])
@requires("music.stats")
def spotify_history_status():
    """what the caller's imported spotify history holds"""
    try:
        return jsonify(_spotify_summary_payload())
    except Exception as e:
        logger.error("spotify history status failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not read the imported history'}), 500


@bp.route('/api/listening-import/spotify/batch', methods=['POST'])
@requires("music.stats")
def spotify_history_batch():
    """one batch of parsed plays from a spotify privacy export"""
    from core.listening_import import spotify_export
    body = request.get_json(silent=True) or {}
    rows = body.get('rows')
    if not isinstance(rows, list):
        return jsonify({'success': False, 'error': 'rows must be a list'}), 400
    if len(rows) > spotify_export.MAX_BATCH:
        return jsonify({'success': False,
                        'error': f'send at most {spotify_export.MAX_BATCH} plays at a time'}), 400
    include = body.get('include_in_stats')
    try:
        result = spotify_export.import_batch(
            get_database(), rows, get_current_profile_id(),
            include_in_stats=None if include is None else bool(include))
        return jsonify({'success': True, **result})
    except Exception as e:
        logger.error("spotify history batch failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not store these plays'}), 500


@bp.route('/api/listening-import/spotify/finish', methods=['POST'])
@requires("music.stats")
def spotify_history_finish():
    """the last batch is in: refresh the stats and say what was imported"""
    from core.listening_import import spotify_export
    try:
        _rebuild_pile_cache(spotify_export.import_owner(get_current_profile_id()))
        return jsonify(_spotify_summary_payload())
    except Exception as e:
        logger.error("spotify history finish failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not finish the import'}), 500


@bp.route('/api/listening-import/spotify/settings', methods=['POST'])
@requires("music.stats")
def spotify_history_settings():
    """include the import in stats and your year, or keep it for taste only"""
    from core.listening_import import spotify_export
    body = request.get_json(silent=True) or {}
    if 'include_in_stats' not in body:
        return jsonify({'success': False, 'error': 'include_in_stats is required'}), 400
    try:
        profile_id = get_current_profile_id()
        if not spotify_export.set_include_in_stats(get_database(), profile_id,
                                                   bool(body.get('include_in_stats'))):
            return jsonify({'success': False, 'error': 'Nothing imported yet'}), 404
        _rebuild_pile_cache(spotify_export.import_owner(profile_id))
        return jsonify(_spotify_summary_payload())
    except Exception as e:
        logger.error("spotify history settings failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not save that'}), 500


@bp.route('/api/listening-import/spotify', methods=['DELETE'])
@requires("music.stats")
def spotify_history_remove():
    """take the imported spotify history back out"""
    from core.listening_import import spotify_export
    try:
        profile_id = get_current_profile_id()
        removed = spotify_export.remove_import(get_database(), profile_id)
        _rebuild_pile_cache(spotify_export.import_owner(profile_id))
        return jsonify({'success': True, 'removed': removed})
    except Exception as e:
        logger.error("spotify history remove failed: %s", e)
        return jsonify({'success': False, 'error': 'Could not remove the import'}), 500
