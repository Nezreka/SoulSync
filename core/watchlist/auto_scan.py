"""Background worker for the automatic watchlist artist scan.

`process_watchlist_scan_automatically(automation_id, profile_id, deps)` is
the orchestrator the automation engine schedules (or the user manually
triggers) to scan watchlisted artists for new releases. Strict 1:1 lift
of the original web_server.py helper.

Parity note:
The original mutated `watchlist_auto_scanning`,
`watchlist_auto_scanning_timestamp`, and `watchlist_scan_state` as
module globals (with a leading `global` decl). Here those names are
exposed through the `WatchlistAutoScanDeps` proxy as Python properties,
so the lifted body keeps the same `name = value` / `name[key] = value`
shape. The property setters fan writes back to web_server.py via
callback pairs so external sentinel checks (id() comparison in the
automation handler) still detect a state-dict swap.

The only line that drops out of byte parity is the original `global`
declaration itself — Python doesn't need it here since the names are
now `deps.X` attribute accesses.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class WatchlistAutoScanDeps:
    """Bundle of cross-cutting deps the watchlist auto-scan needs.

    The three watchlist globals (auto_scanning flag, timestamp, and
    scan_state dict) are exposed as Python properties so the lifted
    function body can write to them with `name = value` syntax —
    the property setters fan the writes back to web_server.py.
    """
    app: Any  # Flask app for app_context()
    spotify_client: Any
    automation_engine: Any
    watchlist_timer_lock: Any  # threading.Lock
    is_watchlist_actually_scanning: Callable[[], bool]
    pause_enrichment_workers: Callable[[str], dict]
    resume_enrichment_workers: Callable[[dict, str], None]
    update_automation_progress: Callable
    add_activity_item: Callable
    _get_auto_scanning: Callable[[], bool]
    _set_auto_scanning: Callable[[bool], None]
    _get_auto_scanning_timestamp: Callable[[], float]
    _set_auto_scanning_timestamp: Callable[[float], None]
    _get_watchlist_scan_state: Callable[[], dict]
    _set_watchlist_scan_state: Callable[[dict], None]

    @property
    def watchlist_auto_scanning(self) -> bool:
        return self._get_auto_scanning()

    @watchlist_auto_scanning.setter
    def watchlist_auto_scanning(self, value: bool) -> None:
        self._set_auto_scanning(value)

    @property
    def watchlist_auto_scanning_timestamp(self) -> float:
        return self._get_auto_scanning_timestamp()

    @watchlist_auto_scanning_timestamp.setter
    def watchlist_auto_scanning_timestamp(self, value: float) -> None:
        self._set_auto_scanning_timestamp(value)

    @property
    def watchlist_scan_state(self) -> dict:
        return self._get_watchlist_scan_state()

    @watchlist_scan_state.setter
    def watchlist_scan_state(self, value: dict) -> None:
        self._set_watchlist_scan_state(value)


def process_watchlist_scan_automatically(automation_id=None, profile_id=None, deps: WatchlistAutoScanDeps = None):
    """Main automatic scanning logic that runs in background thread.

    Args:
        automation_id: ID of the automation triggering this scan
        profile_id: If provided, only scan this profile's watchlist (manual trigger).
                    If None, scan all profiles (scheduled automation).
    """
    scope_label = f"profile {profile_id}" if profile_id else "all profiles"
    logger.info(f"[Auto-Watchlist] Timer triggered - starting automatic watchlist scan ({scope_label})...")

    _ew_state = {}

    try:
        # CRITICAL FIX: Use smart stuck detection BEFORE acquiring lock
        # This prevents deadlock and handles stuck flags (2-hour timeout)
        if deps.is_watchlist_actually_scanning():
            logger.info("[Auto-Watchlist] Already scanning (verified with stuck detection), skipping.")
            return

        with deps.watchlist_timer_lock:
            # Re-check inside lock to handle race conditions
            if deps.watchlist_auto_scanning:
                logger.info("[Auto-Watchlist] Already scanning (race condition check), skipping.")
                return

            # Set flag and timestamp
            import time
            deps.watchlist_auto_scanning = True
            deps.watchlist_auto_scanning_timestamp = time.time()
            logger.info(f"[Auto-Watchlist] Flag set at timestamp {deps.watchlist_auto_scanning_timestamp}")

        # Use app context for database operations
        with deps.app.app_context():
            from core.watchlist_scanner import get_watchlist_scanner
            from database.music_database import get_database

            database = get_database()

            # Determine which profiles to scan
            if profile_id:
                # Manual trigger — scan only the triggering profile
                scan_profiles = [{'id': profile_id}]
            else:
                # Scheduled automation — scan all profiles
                scan_profiles = database.get_all_profiles()

            watchlist_count = sum(database.get_watchlist_count(profile_id=p['id']) for p in scan_profiles)
            profile_label = f"profile {profile_id}" if profile_id else f"{len(scan_profiles)} profiles"
            logger.info(f"[Auto-Watchlist] Watchlist count check: {watchlist_count} artists found ({profile_label})")

            if watchlist_count == 0:
                logger.warning("ℹ️ [Auto-Watchlist] No artists in watchlist for auto-scanning.")
                with deps.watchlist_timer_lock:
                    deps.watchlist_auto_scanning = False
                    deps.watchlist_auto_scanning_timestamp = 0
                return

            if not deps.spotify_client or not deps.spotify_client.is_authenticated():
                logger.info("ℹ️ [Auto-Watchlist] Spotify client not available or not authenticated.")
                with deps.watchlist_timer_lock:
                    deps.watchlist_auto_scanning = False
                    deps.watchlist_auto_scanning_timestamp = 0
                return

            logger.info(f"[Auto-Watchlist] Found {watchlist_count} artists in watchlist, starting automatic scan...")
            deps.update_automation_progress(automation_id, progress=5, phase='Loading watchlist',
                                         log_line=f'{watchlist_count} artists ({profile_label})', log_type='info')

            # Get list of artists to scan
            watchlist_artists = []
            for p in scan_profiles:
                watchlist_artists.extend(database.get_watchlist_artists(profile_id=p['id']))
            scanner = get_watchlist_scanner(deps.spotify_client)
            all_profiles = scan_profiles  # Used later for discovery pool population

            for p in scan_profiles:
                try:
                    filled = scanner.backfill_watchlist_artist_images(p['id'])
                    if filled:
                        logger.info(f"Backfilled {filled} watchlist artist images for profile {p['id']}")
                except Exception as img_err:
                    logger.error(f"Image backfill error for profile {p['id']}: {img_err}")

            # Initialize detailed progress tracking (same as manual scan)
            deps.watchlist_scan_state = {
                'status': 'scanning',
                'started_at': datetime.now(),
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
                'results': [],
                'summary': {},
                'error': None,
                'cancel_requested': False
            }

            scan_results = []

            # Pause enrichment workers during scan to reduce API contention
            _ew_state = deps.pause_enrichment_workers('auto-watchlist scan')

            def _scan_progress(event_type, payload):
                if event_type == 'scan_started':
                    deps.update_automation_progress(
                        automation_id,
                        progress=5,
                        phase='Loading watchlist',
                        log_line=f"{len(watchlist_artists)} artists ({profile_label})",
                        log_type='info',
                    )
                elif event_type == 'artist_started':
                    total = max(1, payload.get('total_artists', len(watchlist_artists)))
                    idx = payload.get('artist_index', 1)
                    artist_name = payload.get('artist_name', '')
                    pct = 5 + ((idx - 1) / total) * 90
                    deps.update_automation_progress(
                        automation_id,
                        progress=pct,
                        phase=f'Scanning: {artist_name} ({idx}/{total})',
                        current_item=artist_name,
                        processed=idx - 1,
                        total=total,
                    )
                elif event_type == 'artist_completed':
                    artist_name = payload.get('artist_name', '')
                    new_tracks = payload.get('new_tracks_found', 0)
                    added = payload.get('tracks_added_to_wishlist', 0)
                    if new_tracks > 0:
                        deps.update_automation_progress(
                            automation_id,
                            log_line=f'{artist_name} — {new_tracks} new, {added} added',
                            log_type='success',
                        )
                    else:
                        deps.update_automation_progress(
                            automation_id,
                            log_line=f'{artist_name} — no new tracks',
                            log_type='skip',
                        )
                elif event_type == 'artist_error':
                    artist_name = payload.get('artist_name', '')
                    error_message = payload.get('error_message', 'error')
                    deps.update_automation_progress(
                        automation_id,
                        log_line=f'{artist_name} — error: {error_message[:60]}',
                        log_type='error',
                    )
                elif event_type == 'cancelled':
                    deps.update_automation_progress(
                        automation_id,
                        progress=100,
                        phase='Cancelled by user',
                        log_line='Scan cancelled by user',
                        log_type='warning',
                    )
                elif event_type == 'scan_completed':
                    deps.update_automation_progress(
                        automation_id,
                        progress=95,
                        phase='Scan complete',
                        log_line=(
                            f"Scanned {payload.get('successful_scans', 0)} artists — "
                            f"{payload.get('new_tracks_found', 0)} new tracks, "
                            f"{payload.get('tracks_added_to_wishlist', 0)} added to wishlist"
                        ),
                        log_type='success' if payload.get('new_tracks_found', 0) > 0 else 'info',
                    )

            scan_results = scanner.scan_watchlist_artists(
                watchlist_artists,
                scan_state=deps.watchlist_scan_state,
                progress_callback=_scan_progress,
                cancel_check=lambda: deps.watchlist_scan_state.get('cancel_requested'),
            )

            # Update state with results (skip if cancelled — already set by cancel handler)
            was_cancelled = deps.watchlist_scan_state.get('cancel_requested', False)
            if not was_cancelled:
                successful_scans = [r for r in scan_results if r.success]
                total_new_tracks = sum(r.new_tracks_found for r in successful_scans)
                total_added_to_wishlist = sum(r.tracks_added_to_wishlist for r in successful_scans)

                deps.watchlist_scan_state['status'] = 'completed'
                deps.watchlist_scan_state['results'] = scan_results
                deps.watchlist_scan_state['completed_at'] = datetime.now()
                deps.watchlist_scan_state['summary'] = {
                    'total_artists': len(scan_results),
                    'successful_scans': len(successful_scans),
                    'new_tracks_found': total_new_tracks,
                    'tracks_added_to_wishlist': total_added_to_wishlist
                }

                logger.info(f"Automatic watchlist scan completed: {len(successful_scans)}/{len(scan_results)} artists scanned successfully")
                logger.info(f"Found {total_new_tracks} new tracks, added {total_added_to_wishlist} to wishlist")
                deps.update_automation_progress(automation_id, progress=95, phase='Scan complete',
                                             log_line=f'Scanned {len(successful_scans)} artists — {total_new_tracks} new tracks, {total_added_to_wishlist} added to wishlist',
                                             log_type='success' if total_new_tracks > 0 else 'info')
            else:
                total_new_tracks = deps.watchlist_scan_state.get('summary', {}).get('new_tracks_found', 0)
                total_added_to_wishlist = deps.watchlist_scan_state.get('summary', {}).get('tracks_added_to_wishlist', 0)
                logger.warning("Automatic watchlist scan cancelled — skipping post-scan steps")

            # Post-scan steps — skip if cancelled
            if not was_cancelled:
                # Populate discovery pool from similar artists (per-profile)
                logger.info("Starting discovery pool population...")
                deps.watchlist_scan_state['current_phase'] = 'populating_discovery_pool'
                deps.update_automation_progress(automation_id, progress=96, phase='Populating discovery pool',
                                             log_line='Building discovery pool from similar artists...', log_type='info')
                try:
                    def _discovery_progress(event_type, message):
                        if event_type == 'artist':
                            deps.update_automation_progress(automation_id, phase=f'Discovery pool: {message}',
                                                         log_line=message, log_type='info',
                                                         current_item=message)
                        elif event_type == 'phase':
                            deps.update_automation_progress(automation_id, phase=message,
                                                         log_line=message, log_type='info')
                        elif event_type == 'success':
                            deps.update_automation_progress(automation_id,
                                                         log_line=message, log_type='success')
                        elif event_type == 'skip':
                            deps.update_automation_progress(automation_id,
                                                         log_line=message, log_type='info')

                    for p in all_profiles:
                        scanner.populate_discovery_pool(profile_id=p['id'], progress_callback=_discovery_progress)
                    logger.info("Discovery pool population complete")
                except Exception as discovery_error:
                    logger.error(f"Error populating discovery pool: {discovery_error}")
                    import traceback
                    traceback.print_exc()
                    deps.update_automation_progress(automation_id,
                                                 log_line=f'Discovery pool error: {discovery_error}', log_type='error')

                # Update ListenBrainz playlists cache
                logger.info("Starting ListenBrainz playlists update...")
                deps.watchlist_scan_state['current_phase'] = 'updating_listenbrainz'
                deps.update_automation_progress(automation_id, progress=97, phase='Updating ListenBrainz',
                                             log_line='Fetching ListenBrainz playlists...', log_type='info')
                try:
                    from core.listenbrainz_manager import ListenBrainzManager
                    db = get_database()
                    db_path = str(db.database_path)
                    lb_profiles = db.get_profiles_with_listenbrainz()
                    if lb_profiles:
                        for lb_prof in lb_profiles:
                            lb_manager = ListenBrainzManager(db_path, profile_id=lb_prof['id'], token=lb_prof['token'], base_url=lb_prof['base_url'])
                            lb_result = lb_manager.update_all_playlists()
                            if lb_result.get('success'):
                                summary = lb_result.get('summary', {})
                                logger.info(f"ListenBrainz update complete for profile {lb_prof['id']}: {summary}")
                                deps.update_automation_progress(automation_id,
                                                             log_line=f'ListenBrainz (profile {lb_prof["id"]}): playlists updated', log_type='success')
                    else:
                        lb_manager = ListenBrainzManager(db_path)
                        lb_result = lb_manager.update_all_playlists()
                        if lb_result.get('success'):
                            summary = lb_result.get('summary', {})
                            logger.info(f"ListenBrainz update complete (global): {summary}")
                            deps.update_automation_progress(automation_id,
                                                         log_line='ListenBrainz: playlists updated', log_type='success')
                        else:
                            logger.error(f"ListenBrainz update had issues: {lb_result.get('error', 'Unknown error')}")
                            deps.update_automation_progress(automation_id,
                                                         log_line=f'ListenBrainz: {lb_result.get("error", "Unknown error")}', log_type='error')
                except Exception as lb_error:
                    logger.error(f"Error updating ListenBrainz: {lb_error}")
                    import traceback
                    traceback.print_exc()
                    deps.update_automation_progress(automation_id,
                                                 log_line=f'ListenBrainz error: {lb_error}', log_type='error')

                # Update current seasonal playlist (weekly refresh)
                logger.info("Starting seasonal content update...")
                deps.watchlist_scan_state['current_phase'] = 'updating_seasonal'
                deps.update_automation_progress(automation_id, progress=98, phase='Updating seasonal content',
                                             log_line='Checking seasonal playlists...', log_type='info')
                try:
                    from core.seasonal_discovery import get_seasonal_discovery_service
                    seasonal_service = get_seasonal_discovery_service(deps.spotify_client, database)

                    # Only update the current active season
                    current_season = seasonal_service.get_current_season()
                    if current_season:
                        if seasonal_service.should_populate_seasonal_content(current_season, days_threshold=7):
                            logger.info(f"Updating {current_season} seasonal content...")
                            deps.update_automation_progress(automation_id,
                                                         log_line=f'Updating {current_season} seasonal content...', log_type='info')
                            seasonal_service.populate_seasonal_content(current_season)
                            seasonal_service.curate_seasonal_playlist(current_season)
                            logger.info(f"{current_season.capitalize()} seasonal content updated")
                            deps.update_automation_progress(automation_id,
                                                         log_line=f'{current_season.capitalize()} seasonal content updated', log_type='success')
                        else:
                            logger.info(f"{current_season.capitalize()} seasonal content recently updated, skipping")
                            deps.update_automation_progress(automation_id,
                                                         log_line=f'{current_season.capitalize()} seasonal content up to date', log_type='info')
                    else:
                        logger.warning("ℹ️ No active season at this time")
                        deps.update_automation_progress(automation_id,
                                                     log_line='No active season', log_type='info')
                except Exception as seasonal_error:
                    logger.error(f"Error updating seasonal content: {seasonal_error}")
                    import traceback
                    traceback.print_exc()
                    deps.update_automation_progress(automation_id,
                                                 log_line=f'Seasonal error: {seasonal_error}', log_type='error')

                # Generate Last.fm radio playlists (weekly refresh)
                logger.info("Starting Last.fm radio generation...")
                deps.watchlist_scan_state['current_phase'] = 'generating_lastfm_radio'
                deps.update_automation_progress(automation_id, progress=99, phase='Generating Last.fm radio',
                                             log_line='Building Last.fm radio playlists...', log_type='info')
                try:
                    scanner._generate_lastfm_radio_playlists()
                    logger.info("Last.fm radio generation complete")
                    deps.update_automation_progress(automation_id,
                                                 log_line='Last.fm radio playlists updated', log_type='success')
                except Exception as lastfm_error:
                    logger.error(f"Error generating Last.fm radio playlists: {lastfm_error}")
                    deps.update_automation_progress(automation_id,
                                                 log_line=f'Last.fm radio error: {lastfm_error}', log_type='error')

                # Sync Spotify library cache
                logger.info("Syncing Spotify library cache...")
                try:
                    for p in all_profiles:
                        scanner.sync_spotify_library_cache(profile_id=p['id'])
                    logger.info("Spotify library cache sync complete")
                    deps.update_automation_progress(automation_id,
                                                 log_line='Spotify library cache synced', log_type='info')
                except Exception as lib_error:
                    logger.error(f"Error syncing Spotify library: {lib_error}")
                    deps.update_automation_progress(automation_id,
                                                 log_line=f'Library cache error: {lib_error}', log_type='error')

                # Add activity for watchlist scan completion
                if total_added_to_wishlist > 0:
                    deps.add_activity_item("", "Watchlist Scan Complete", f"{total_added_to_wishlist} new tracks added to wishlist", "Now")

                try:
                    if deps.automation_engine:
                        deps.automation_engine.emit('watchlist_scan_completed', {
                            'artists_scanned': str(len(scan_results)),
                            'new_tracks_found': str(total_new_tracks),
                            'tracks_added': str(total_added_to_wishlist),
                        })
                except Exception as e:
                    logger.debug("watchlist_scan_completed emit failed: %s", e)

    except Exception as e:
        logger.error(f"Error in automatic watchlist scan: {e}")
        import traceback
        traceback.print_exc()
        deps.update_automation_progress(automation_id, log_line=f'Error: {str(e)}', log_type='error')

        deps.watchlist_scan_state['status'] = 'error'
        deps.watchlist_scan_state['error'] = str(e)
        raise  # re-raise so automation wrapper returns error status

    finally:
        # Resume enrichment workers if we paused them
        deps.resume_enrichment_workers(_ew_state, 'auto-watchlist scan')

        # Clear one-time rescan cutoff after full scan cycle
        try:
            scanner._clear_rescan_cutoff()
        except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
            pass

        # Always reset flag
        with deps.watchlist_timer_lock:
            deps.watchlist_auto_scanning = False
            deps.watchlist_auto_scanning_timestamp = 0

    logger.info("Automatic watchlist scanning complete")
