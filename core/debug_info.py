"""Debug info endpoint — lifted from web_server.py.

The function bodies are byte-identical to the originals. Module-level
shims for ``spotify_client`` and ``tidal_client`` (proxies that resolve
through the metadata registry / runtime client registry) plus injected
state dicts and helpers let the bodies resolve their original names
without modification.
"""
import logging
import os
import platform
from pathlib import Path

from flask import jsonify, request

from config.settings import config_manager
from core.metadata.registry import get_spotify_client

logger = logging.getLogger(__name__)


class _SpotifyClientProxy:
    """Resolves the global Spotify client lazily through core.metadata.registry."""

    def __getattr__(self, name):
        client = get_spotify_client()
        if client is None:
            raise AttributeError(name)
        return getattr(client, name)

    def __bool__(self):
        return get_spotify_client() is not None


class _TidalClientProxy:
    """Resolves the global Tidal client lazily via an injected getter so a
    Tidal re-auth that rebinds web_server.tidal_client is visible here."""

    def __getattr__(self, name):
        if _get_tidal_client is None:
            raise AttributeError(name)
        client = _get_tidal_client()
        if client is None:
            raise AttributeError(name)
        return getattr(client, name)

    def __bool__(self):
        if _get_tidal_client is None:
            return False
        return _get_tidal_client() is not None


spotify_client = _SpotifyClientProxy()
tidal_client = _TidalClientProxy()
_get_tidal_client = None  # injected via init()


# Injected at runtime via init().
SOULSYNC_VERSION = None
_DIRECT_RUN = None
_status_cache = None
qobuz_enrichment_worker = None
download_batches = None
sync_states = None
youtube_playlist_states = None
tidal_discovery_states = None
download_orchestrator = None
_log_path = None
_log_dir = None
app = None
get_database = None


def init(
    soulsync_version,
    direct_run,
    status_cache,
    qobuz_worker,
    download_batches_dict,
    sync_states_dict,
    youtube_playlist_states_dict,
    tidal_discovery_states_dict,
    download_orchestrator_obj,
    log_path,
    log_dir,
    flask_app,
    get_database_fn,
    tidal_client_getter,
):
    """Bind shared state/helpers from web_server."""
    global SOULSYNC_VERSION, _DIRECT_RUN, _status_cache, qobuz_enrichment_worker
    global download_batches, sync_states, youtube_playlist_states
    global tidal_discovery_states, download_orchestrator, _log_path, _log_dir
    global app, get_database, _get_tidal_client
    SOULSYNC_VERSION = soulsync_version
    _DIRECT_RUN = direct_run
    _status_cache = status_cache
    qobuz_enrichment_worker = qobuz_worker
    download_batches = download_batches_dict
    sync_states = sync_states_dict
    youtube_playlist_states = youtube_playlist_states_dict
    tidal_discovery_states = tidal_discovery_states_dict
    download_orchestrator = download_orchestrator_obj
    _log_path = log_path
    _log_dir = log_dir
    app = flask_app
    get_database = get_database_fn
    _get_tidal_client = tidal_client_getter


def _safe_check(fn, default=False):
    """Safely evaluate a check function, returning default on any error."""
    try:
        return fn()
    except Exception:
        return default


def get_debug_info():
    """Collect system diagnostics for troubleshooting support requests."""
    import sys
    import psutil
    import time
    from datetime import timedelta

    log_lines = request.args.get('lines', 20, type=int)
    log_lines = max(10, min(log_lines, 500))
    log_source = request.args.get('log', 'app')

    info = {}

    # App info
    info['version'] = SOULSYNC_VERSION
    info['os'] = f"{platform.system()} {platform.release()}"
    info['python'] = sys.version.split()[0]
    info['docker'] = os.path.exists('/.dockerenv')
    info['runner'] = 'gunicorn' if not _DIRECT_RUN else 'direct (python web_server.py)'

    # ffmpeg version
    try:
        import subprocess
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
        first_line = result.stdout.split('\n')[0] if result.stdout else ''
        # e.g. "ffmpeg version 6.1.1 Copyright ..."
        info['ffmpeg'] = first_line.split('Copyright')[0].replace('ffmpeg version', '').strip() if first_line else 'installed (version unknown)'
    except FileNotFoundError:
        info['ffmpeg'] = 'NOT INSTALLED'
    except Exception:
        info['ffmpeg'] = 'unknown'

    # Uptime
    start_time = getattr(app, 'start_time', time.time())
    uptime_seconds = time.time() - start_time
    info['uptime'] = str(timedelta(seconds=int(uptime_seconds)))

    # Paths
    download_path = config_manager.get('soulseek.download_path', './downloads')
    transfer_folder = config_manager.get('soulseek.transfer_path', './Transfer')
    staging_folder = config_manager.get('import.staging_path', '')
    info['paths'] = {
        'download_path': download_path,
        'download_path_exists': os.path.isdir(download_path) if download_path else False,
        'download_path_writable': os.access(download_path, os.W_OK) if download_path and os.path.isdir(download_path) else False,
        'transfer_folder': transfer_folder,
        'transfer_folder_exists': os.path.isdir(transfer_folder) if transfer_folder else False,
        'transfer_folder_writable': os.access(transfer_folder, os.W_OK) if transfer_folder and os.path.isdir(transfer_folder) else False,
        'staging_folder': staging_folder,
        'staging_folder_exists': os.path.isdir(staging_folder) if staging_folder else False,
    }
    # Music library paths (Settings > Library)
    music_paths = config_manager.get('library.music_paths', [])
    if isinstance(music_paths, list) and music_paths:
        info['paths']['music_library_paths'] = []
        for p in music_paths:
            if p and isinstance(p, str):
                info['paths']['music_library_paths'].append({
                    'path': p,
                    'exists': os.path.isdir(p),
                })
    # Music videos directory
    music_videos_path = config_manager.get('library.music_videos_path', '')
    if music_videos_path:
        info['paths']['music_videos_path'] = music_videos_path
        info['paths']['music_videos_path_exists'] = os.path.isdir(music_videos_path)

    # Services from status cache
    spotify_cache = _status_cache.get('spotify', {})
    media_server_cache = _status_cache.get('media_server', {})
    soulseek_cache = _status_cache.get('soulseek', {})
    info['services'] = {
        'music_source': spotify_cache.get('source', 'unknown'),
        'spotify_connected': spotify_cache.get('connected', False),
        'spotify_rate_limited': spotify_cache.get('rate_limited', False),
        'media_server_type': media_server_cache.get('type', 'none'),
        'media_server_connected': media_server_cache.get('connected', False),
        'soulseek_connected': soulseek_cache.get('connected', False),
        'download_source': config_manager.get('download_source.mode', 'hybrid'),
        'tidal_connected': _safe_check(lambda: bool(tidal_client and tidal_client.is_authenticated())),
        'qobuz_connected': _safe_check(lambda: bool(qobuz_enrichment_worker and qobuz_enrichment_worker.client and qobuz_enrichment_worker.client.is_authenticated())),
    }

    # Enrichment workers
    workers = {}
    worker_names = ['musicbrainz', 'audiodb', 'deezer', 'spotify', 'itunes', 'lastfm', 'genius', 'discogs', 'tidal', 'qobuz']
    for name in worker_names:
        paused_key = f'{name}_enrichment_paused'
        workers[name] = 'paused' if config_manager.get(paused_key, False) else 'active'
    info['enrichment_workers'] = workers

    # Library stats — use same method as dashboard (filters by active server)
    try:
        db = get_database()
        lib_stats = db.get_database_info_for_server()
        info['library'] = {
            'artists': lib_stats.get('artists', 0),
            'albums': lib_stats.get('albums', 0),
            'tracks': lib_stats.get('tracks', 0),
        }
    except Exception:
        info['library'] = {'artists': 0, 'albums': 0, 'tracks': 0}

    # Watchlist count
    try:
        db = get_database()
        info['watchlist_count'] = db.get_watchlist_count()
    except Exception:
        info['watchlist_count'] = 0

    # Wishlist pending count
    try:
        db = get_database()
        info['wishlist_count'] = db.get_wishlist_count()
    except Exception:
        info['wishlist_count'] = 0

    # Automation count
    try:
        db = get_database()
        automations = db.get_automations()
        info['automations'] = {
            'total': len(automations),
            'enabled': len([a for a in automations if a.get('enabled', False)]),
        }
    except Exception:
        info['automations'] = {'total': 0, 'enabled': 0}

    # Active downloads & syncs (use list() snapshots to avoid RuntimeError from concurrent mutation)
    try:
        active_downloads = len([bid for bid, bd in list(download_batches.items()) if bd.get('phase') == 'downloading'])
    except Exception:
        active_downloads = 0
    active_syncs = 0
    try:
        for _pid, ss in list(sync_states.items()):
            if ss.get('status') == 'syncing':
                active_syncs += 1
        for _uh, st in list(youtube_playlist_states.items()):
            if st.get('phase') == 'syncing':
                active_syncs += 1
        for _pid, st in list(tidal_discovery_states.items()):
            if st.get('phase') == 'syncing':
                active_syncs += 1
    except Exception as e:
        logger.debug("count active syncs failed: %s", e)
    info['active_downloads'] = active_downloads
    info['active_syncs'] = active_syncs

    # Config settings relevant to troubleshooting
    source_mode = config_manager.get('download_source.mode', 'hybrid')
    info['config'] = {
        'source_mode': source_mode,
        'quality_profile': config_manager.get('download_source.quality_profile', 'default'),
        'organization_template': config_manager.get('organization.folder_template', ''),
        'post_processing_enabled': config_manager.get('post_processing.enabled', True),
        'acoustid_enabled': bool(config_manager.get('acoustid.api_key', '')),
        'auto_scan_enabled': config_manager.get('watchlist.auto_scan', False),
        'm3u_export_enabled': config_manager.get('m3u.enabled', False),
        'log_level': config_manager.get('logging.level', 'INFO'),
        'primary_metadata_source': config_manager.get('metadata.fallback_source', 'deezer'),
        'lossy_copy_enabled': config_manager.get('post_processing.lossy_copy.enabled', False),
        'lossy_copy_format': config_manager.get('post_processing.lossy_copy.format', 'mp3'),
        'lossy_copy_bitrate': config_manager.get('post_processing.lossy_copy.bitrate', 320),
        'allow_duplicate_tracks': config_manager.get('library.allow_duplicate_tracks', False),
        'replace_lower_quality': config_manager.get('import.replace_lower_quality', False),
        'auto_import_enabled': config_manager.get('import.auto_import_enabled', False),
    }
    # Hybrid source priority order
    if source_mode == 'hybrid':
        info['config']['hybrid_sources'] = config_manager.get('download_source.hybrid_order', [])
    # Discogs connection status
    info['services']['discogs_connected'] = bool(config_manager.get('discogs.token', ''))

    # Download client init failures
    info['download_client_failures'] = []
    if download_orchestrator and hasattr(download_orchestrator, '_init_failures'):
        info['download_client_failures'] = download_orchestrator._init_failures
    elif not download_orchestrator:
        info['download_client_failures'] = ['ALL (orchestrator failed to initialize)']

    # API rate monitor — current calls/min, 24h totals, peaks, rate limit events
    try:
        from core.api_call_tracker import api_call_tracker
        from core.metadata.status import get_spotify_status
        rates = api_call_tracker.get_all_rates()
        info['api_rates'] = rates
        # Rich 24h debug summary with peaks, totals, per-endpoint breakdown, events
        info['api_debug_summary'] = api_call_tracker.get_debug_summary()
        # Spotify rate limit details
        spotify_status = get_spotify_status(spotify_client=spotify_client)
        rl_info = spotify_status.get('rate_limit')
        if spotify_status.get('rate_limited') and rl_info:
            info['spotify_rate_limit'] = {
                'active': True,
                'remaining_seconds': rl_info.get('remaining_seconds', 0),
                'retry_after': rl_info.get('retry_after', 0),
                'endpoint': rl_info.get('endpoint', ''),
                'expires_at': rl_info.get('expires_at', ''),
            }
        else:
            info['spotify_rate_limit'] = {'active': False}
    except Exception:
        info['api_rates'] = {}
        info['api_debug_summary'] = {}
        info['spotify_rate_limit'] = {'active': False}

    # Database size
    db_path = os.path.join('database', 'music_library.db')
    if os.path.exists(db_path):
        db_size_mb = os.path.getsize(db_path) / (1024 * 1024)
        info['database_size'] = f"{db_size_mb:.1f} MB"
    else:
        info['database_size'] = 'not found'

    # Memory & CPU
    process = psutil.Process(os.getpid())
    mem = process.memory_info()
    info['memory_usage'] = f"{mem.rss / (1024 * 1024):.0f} MB"
    info['system_memory'] = f"{psutil.virtual_memory().percent}%"
    try:
        info['cpu_percent'] = f"{process.cpu_percent(interval=0.1):.1f}%"
    except Exception:
        info['cpu_percent'] = 'unknown'
    info['thread_count'] = process.num_threads()

    # Log lines
    log_map = {
        'app': Path(_log_path),
        'acoustid': _log_dir / 'acoustid.log',
        'post_processing': _log_dir / 'post_processing.log',
        'source_reuse': _log_dir / 'source_reuse.log',
    }
    log_path = log_map.get(log_source, log_map['app'])
    info['log_source'] = log_source
    info['log_lines_requested'] = log_lines
    info['recent_logs'] = []
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
                info['recent_logs'] = [line.rstrip() for line in lines[-log_lines:]]
        except Exception:
            info['recent_logs'] = ['(could not read log file)']

    # Available log files
    info['available_logs'] = []
    logs_dir = 'logs'
    if os.path.isdir(logs_dir):
        for fname in sorted(os.listdir(logs_dir)):
            if fname.endswith('.log'):
                fpath = os.path.join(logs_dir, fname)
                size_kb = os.path.getsize(fpath) / 1024
                info['available_logs'].append({
                    'name': fname.replace('.log', ''),
                    'file': fname,
                    'size': f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB",
                })

    return jsonify(info)
