"""Database updater + backup + maintenance endpoints - lifted from web_server.

the db-update family in one piece: the five worker callbacks, the stall
self-heal, the pause/resume-workers-for-scan dance, the run tasks, the
/api/database update/stats/backup/restore/maintenance routes, and the
db_update_worker global they all rebind (module-local now - web_server's
shutdown path reads it as a module attribute). db_update_state/lock/
executor stay in web_server and come in as injected stable objects: the
incremental-update outlier route and the automation deps still use them
there.

bodies byte-identical; only the decorator changed and the enrichment
worker fleet became injected getters - every worker is rebound when its
settings change, so holding the object would hold a stale one.
"""

import json
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from core.database_update_worker import DatabaseUpdateWorker
from core.profile_context import admin_only
from core.runtime_state import add_activity_item
from utils.logging_config import get_logger

logger = get_logger("web_server")

# module-local mutable state: the update worker handle and the automation id
# the callbacks tag their progress with
db_update_worker = None
_db_update_automation_id = None
_workers_paused_by_scan = {}


def set_db_update_automation_id(value):
    """setter exposed to the automation handlers - keeps the module global
    in sync so the live progress callbacks emit against the right card."""
    global _db_update_automation_id
    _db_update_automation_id = value


# injected by configure() - stable objects and helpers
db_update_state = None
db_update_lock = None
db_update_executor = None
media_server_engine = None
get_database = None
config_manager = None
docker_resolve_path = None
automation_engine = None
socketio = None
SOULSYNC_VERSION = None
_automatic_wishlist_cleanup_after_db_update = None
_reconcile_after_scan = None
_update_automation_progress = None
_restart_library_v2_migration = None
# rebindable worker fleet - injected as getters
_amazon_worker = None
_audiodb_worker = None
_bandcamp_worker = None
_deezer_worker = None
_discogs_worker = None
_genius_worker = None
_itunes_enrichment_worker = None
_jiosaavn_worker = None
_lastfm_worker = None
_mb_worker = None
_qobuz_enrichment_worker = None
_repair_worker = None
_soulid_worker = None
_spotify_enrichment_worker = None
_tidal_enrichment_worker = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"database_admin.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('database_admin', __name__)


def create_blueprint():
    return bp


# ===============================
# == DATABASE UPDATER API      ==
# ===============================

def _db_update_progress_callback(current_item, processed, total, percentage):
    logger.info(f"[DB Progress] {current_item} - {processed}/{total} ({percentage:.1f}%)")
    with db_update_lock:
        db_update_state.update({
            "current_item": current_item,
            "processed": processed,
            "total": total,
            "progress": percentage,
            "last_progress_at": time.time(),  # heartbeat for the stall watchdog
        })
    _update_automation_progress(_db_update_automation_id,
                                progress=percentage, processed=processed, total=total,
                                current_item=current_item)

def _db_update_phase_callback(phase):
    logger.info(f"[DB Phase] {phase}")
    with db_update_lock:
        db_update_state["phase"] = phase
        db_update_state["last_progress_at"] = time.time()  # heartbeat for the stall watchdog
    _update_automation_progress(_db_update_automation_id, phase=phase)

def _db_update_artist_callback(artist_name, success, details, album_count, track_count):
    if success:
        # Use the details string from the worker — it includes context like "0 new tracks (150 existing updated)"
        log_msg = f'{artist_name} — {details}' if details else f'{artist_name} — {album_count} albums, {track_count} tracks'
        _update_automation_progress(_db_update_automation_id,
            log_line=log_msg,
            log_type='success')
    else:
        _update_automation_progress(_db_update_automation_id,
            log_line=f'{artist_name} — {details}',
            log_type='error')

def _db_update_finished_callback(total_artists, total_albums, total_tracks, successful, failed):
    global _db_update_automation_id
    # Library extras: keep the whole-library M3U in sync with the DB. Every scan type (deep,
    # incremental, full refresh) converges on this callback, so writing here keeps it current.
    # Destination = the configured M3U output folder if set, else the Transfer folder. Fully
    # guarded — a playlist write must never disturb scan completion.
    # The outcome is surfaced in the scan summary (m3u_note) so "did it trigger?"
    # is answerable from the UI, not just app.log (#1041).
    m3u_note = ""
    try:
        if config_manager.get('m3u_export.library_enabled', False):
            from core.library.m3u_export import write_library_m3u
            _entries = get_database().get_all_library_tracks_for_export()
            _dest = (config_manager.get('m3u_export.library_path', '') or '').strip() \
                or config_manager.get('soulseek.transfer_path', './Transfer')
            _dest = docker_resolve_path(_dest)
            _base = config_manager.get('m3u_export.entry_base_path', '') or ''
            _written = write_library_m3u(
                _entries, _dest, entry_base_path=_base,
                rewrite_from=config_manager.get('m3u_export.rewrite_from', '') or '',
                rewrite_to=config_manager.get('m3u_export.rewrite_to', '') or '')
            if _written:
                logger.info("[library-m3u] auto-synced %d tracks -> %s", len(_entries), _written)
                m3u_note = f" | Library M3U: {len(_entries)} tracks → {_written}"
            else:
                m3u_note = " | Library M3U write failed (see app.log)"
        else:
            # One line per scan so a "never triggers" report diagnoses itself.
            logger.info("[library-m3u] skipped — Library M3U auto-sync is disabled in Settings")
    except Exception as _m3u_err:
        logger.warning("[library-m3u] auto-sync failed: %s", _m3u_err)
        m3u_note = " | Library M3U write failed (see app.log)"
    # Check for removal results from the worker
    removed_artists = 0
    removed_albums = 0
    removed_tracks = 0
    if db_update_worker:
        removed_artists = getattr(db_update_worker, 'removed_artists', 0)
        removed_albums = getattr(db_update_worker, 'removed_albums', 0)
        removed_tracks = getattr(db_update_worker, 'removed_tracks', 0)

    removal_msg = ""
    if removed_artists > 0 or removed_albums > 0:
        removal_msg = f" | Removed: {removed_artists} artists, {removed_albums} albums"
    if removed_tracks > 0:
        removal_msg += f", {removed_tracks} tracks"

    # Build a clear summary message
    # For deep scans: total_tracks = new tracks only, successful = artists processed
    # Include skipped/existing count when available for clarity
    skipped_tracks = 0
    if db_update_worker:
        skipped_tracks = getattr(db_update_worker, '_total_skipped', 0)
        # Calculate from processed counts if not tracked directly
        if not skipped_tracks:
            total_processed = getattr(db_update_worker, 'processed_tracks', 0)
            if total_processed == 0 and total_tracks == 0 and successful > 0:
                # Deep scan with nothing new — show artists scanned
                skipped_tracks = getattr(db_update_worker, 'processed_albums', 0)

    if total_tracks > 0:
        phase_msg = f"Completed: {total_artists} artists, {total_albums} albums, {total_tracks} new tracks{removal_msg}{m3u_note}."
    elif successful > 0:
        phase_msg = f"Completed: {successful} artists scanned, library up to date{removal_msg}{m3u_note}."
    else:
        phase_msg = f"Completed: {successful} successful, {failed} failed{removal_msg}{m3u_note}."

    with db_update_lock:
        db_update_state["status"] = "finished"
        db_update_state["phase"] = phase_msg
        db_update_state["total_albums"] = total_albums
        db_update_state["total_tracks"] = total_tracks
        db_update_state["removed_artists"] = removed_artists
        db_update_state["removed_albums"] = removed_albums
        db_update_state["removed_tracks"] = removed_tracks

    # Finalize automation progress
    auto_summary = f"{total_tracks} tracks, {total_albums} albums from {total_artists} artists"
    if removed_artists > 0 or removed_albums > 0:
        auto_summary += f" | Removed {removed_artists} artists, {removed_albums} albums"
    auto_summary += m3u_note
    _update_automation_progress(_db_update_automation_id,
        status='finished', progress=100, phase='Complete',
        log_line=auto_summary, log_type='success')
    _db_update_automation_id = None

    # Resume enrichment workers now that scan is done
    _resume_workers_after_scan()

    # Add activity for database update completion
    summary = f"{total_tracks} tracks, {total_albums} albums, {total_artists} artists processed"
    if removed_artists > 0 or removed_albums > 0:
        summary += f" | {removed_artists} artists, {removed_albums} albums removed"
    add_activity_item("", "Database Update Complete", summary, "Now")

    try:
        if automation_engine:
            automation_engine.emit('database_update_completed', {
                'total_artists': str(total_artists),
                'total_albums': str(total_albums),
                'total_tracks': str(total_tracks),
            })
    except Exception as e:
        logger.debug("library_updated automation emit failed: %s", e)

    # Invalidate sync match cache (track IDs may have changed)
    try:
        inv_db = get_database()
        cleared = inv_db.invalidate_sync_match_cache()
        if cleared:
            logger.info(f"Cleared {cleared} sync match cache entries after database update")
    except Exception as e:
        logger.debug("sync match cache invalidation failed: %s", e)

    # WISHLIST CLEANUP: Automatically clean up wishlist after database update
    try:
        logger.info("[DB Update] Database update completed, starting automatic wishlist cleanup...")
        # Dedicated thread, NOT `missing_download_executor` — that pool (3
        # workers) also runs post-processing of completed downloads, and on a
        # big wishlist one cleanup pass takes HOURS (per-track fuzzy matching).
        # Stacked cleanups saturated the pool and finished downloads stopped
        # moving to Completed until restart (jadux). The cleanup itself is
        # overlap-guarded (skip-if-running) in core/wishlist/processing.py.
        threading.Thread(
            target=_automatic_wishlist_cleanup_after_db_update,
            name="WishlistCleanup",
            daemon=True,
        ).start()
    except Exception as cleanup_error:
        logger.error(f"[DB Update] Error starting automatic wishlist cleanup: {cleanup_error}")

def _db_update_error_callback(error_message):
    global _db_update_automation_id
    with db_update_lock:
        db_update_state["status"] = "error"
        db_update_state["error_message"] = error_message
    # Resume enrichment workers even on error
    _resume_workers_after_scan()
    _update_automation_progress(_db_update_automation_id,
        status='error', phase='Error',
        log_line=error_message, log_type='error')
    _db_update_automation_id = None

    # Add activity for database update error
    add_activity_item("", "Database Update Failed", error_message, "Now")


def _check_db_update_stall():
    """Watchdog: flip a hung 'running' DB-update job to 'error' so the UI can
    recover (#859). A worker that blocks indefinitely (media-server call with no
    timeout, DB lock) never fires its finished/error callback, so the job would
    otherwise sit at 'running' forever with a frozen progress bar.

    Idempotent — only acts on the running→stalled transition (after the flip,
    status != 'running' so the pure check returns False and we don't re-fire).
    Safe to call from the status endpoint and the 1s broadcast loop. Returns True
    only on the transition."""
    from core.database_update_health import (
        DEFAULT_STALL_TIMEOUT_SECONDS,
        is_db_update_stalled,
        stalled_error_message,
    )
    try:
        timeout = config_manager.get('database.update_stall_timeout_seconds',
                                     DEFAULT_STALL_TIMEOUT_SECONDS)
    except Exception:
        timeout = DEFAULT_STALL_TIMEOUT_SECONDS
    now = time.time()
    with db_update_lock:
        if not is_db_update_stalled(db_update_state, now, timeout):
            return False
        msg = stalled_error_message(db_update_state, now)
        db_update_state["status"] = "error"
        db_update_state["error_message"] = msg
        db_update_state["phase"] = "Stalled"
    logger.error(f"[DB Update Watchdog] {msg}")
    # The hung worker paused enrichment/maintenance workers and won't resume them
    # itself — resume here so a stall doesn't leave them parked indefinitely.
    try:
        _resume_workers_after_scan()
    except Exception as e:
        logger.debug(f"[DB Update Watchdog] resume workers failed: {e}")
    return True

_workers_paused_by_scan = set()  # Track which workers WE paused (don't resume manually-paused ones)

def _pause_workers_for_scan():
    """Pause all enrichment and maintenance workers during database scans to reduce lock contention."""
    global _workers_paused_by_scan
    _workers_paused_by_scan = set()
    workers = {
        'mb': _mb_worker(), 'spotify': _spotify_enrichment_worker(), 'itunes': _itunes_enrichment_worker(),
        'deezer': _deezer_worker(), 'audiodb': _audiodb_worker(), 'discogs': _discogs_worker(), 'lastfm': _lastfm_worker(),
        'genius': _genius_worker(), 'tidal': _tidal_enrichment_worker(), 'qobuz': _qobuz_enrichment_worker(),
        'amazon': _amazon_worker(), 'repair': _repair_worker(), 'soulid': _soulid_worker(), 'jiosaavn': _jiosaavn_worker(),
        'bandcamp': _bandcamp_worker(),
    }
    for name, w in workers.items():
        if w and hasattr(w, 'pause') and not getattr(w, 'paused', True):
            w.pause()
            _workers_paused_by_scan.add(name)
    if _workers_paused_by_scan:
        logger.warning(f"Paused {len(_workers_paused_by_scan)} workers during database scan: {', '.join(_workers_paused_by_scan)}")

def _resume_workers_after_scan():
    """Resume only the workers that WE paused (don't resume manually-paused ones)."""
    global _workers_paused_by_scan
    workers = {
        'mb': _mb_worker(), 'spotify': _spotify_enrichment_worker(), 'itunes': _itunes_enrichment_worker(),
        'deezer': _deezer_worker(), 'audiodb': _audiodb_worker(), 'discogs': _discogs_worker(), 'lastfm': _lastfm_worker(),
        'genius': _genius_worker(), 'tidal': _tidal_enrichment_worker(), 'qobuz': _qobuz_enrichment_worker(),
        'amazon': _amazon_worker(), 'repair': _repair_worker(), 'soulid': _soulid_worker(), 'jiosaavn': _jiosaavn_worker(),
        'bandcamp': _bandcamp_worker(),
    }
    resumed = 0
    for name, w in workers.items():
        if name in _workers_paused_by_scan and w and hasattr(w, 'resume'):
            w.resume()
            resumed += 1
    if resumed:
        logger.info(f"Resumed {resumed} workers after database scan")
    _workers_paused_by_scan = set()

def _run_soulsync_full_refresh():
    """Re-index the standalone output folder through the native import writer.

    This is an explicit local import/recovery operation, not a media-server
    sync.  It never clears catalogue rows or server mappings up front.
    """
    try:
        transfer_path = docker_resolve_path(config_manager.get('soulseek.transfer_path', './Transfer'))
        if not os.path.isdir(transfer_path):
            _db_update_error_callback(f"Output folder not found: {transfer_path}")
            return

        logger.info("[SoulSync Full Refresh] Re-indexing local files: %s", transfer_path)
        db = get_database()

        _db_update_phase_callback('Scanning output folder...')
        audio_exts = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif', '.ape'}
        audio_files = []
        for root, _dirs, files in os.walk(transfer_path):
            for fname in files:
                if os.path.splitext(fname)[1].lower() in audio_exts:
                    audio_files.append(os.path.join(root, fname))

        total = len(audio_files)
        logger.info("[SoulSync Full Refresh] Found %d audio files", total)
        _db_update_phase_callback(f'Importing local index from {total} files...')
        successful = 0
        failed = 0
        from core.library2.autolink import link_download_into_library_v2
        for index, file_path in enumerate(audio_files, 1):
            try:
                file_id = link_download_into_library_v2(
                    {
                        '_final_processed_path': file_path,
                        '_download_username': 'standalone_refresh',
                        'username': 'standalone_refresh',
                    },
                    raise_on_error=True,
                )
                if file_id is None:
                    raise RuntimeError("native import returned no file row")
                successful += 1
            except Exception as exc:
                failed += 1
                logger.error("[SoulSync Full Refresh] Could not import %s: %s", file_path, exc)
            if index % 50 == 0:
                _db_update_phase_callback(f'Importing local index... {index}/{total}')

        # Existing rows that disappeared enter the normal two-scan missing
        # lifecycle.  They are never hard-deleted by this recovery scan.
        scoped_file_ids = []
        transfer_root = os.path.realpath(transfer_path)
        from core.library2.paths import resolve_lib2_directory, resolve_lib2_path
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT id,path FROM lib2_track_files "
                "WHERE COALESCE(file_state,'active')<>'deleted'"
            ).fetchall()
        for row in rows:
            stored = str(row['path'])
            resolved = resolve_lib2_path(stored)
            resolved_dir = None if resolved else resolve_lib2_directory(stored)
            candidate = os.path.realpath(
                resolved
                or (os.path.join(
                    resolved_dir, os.path.basename(stored.replace('\\', '/'))
                ) if resolved_dir else stored)
            )
            try:
                if os.path.commonpath((transfer_root, candidate)) == transfer_root:
                    scoped_file_ids.append(int(row['id']))
            except ValueError:
                continue
        if scoped_file_ids:
            from core.library2.scan import rescan_files
            rescan_files(db, file_ids=scoped_file_ids)

        artist_ids, album_ids = set(), set()
        with db._get_connection() as conn:
            for start in range(0, len(scoped_file_ids), 500):
                chunk = scoped_file_ids[start:start + 500]
                marks = ",".join("?" for _ in chunk)
                for row in conn.execute(
                    f"""SELECT ar.id,al.id
                           FROM lib2_track_files f
                           JOIN lib2_tracks t ON t.id=f.track_id
                           JOIN lib2_albums al ON al.id=t.album_id
                           JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                          WHERE f.id IN ({marks})""",
                    chunk,
                ):
                    artist_ids.add(int(row[0]))
                    album_ids.add(int(row[1]))
        artist_count, album_count = len(artist_ids), len(album_ids)
        summary = (f"Full refresh complete: {successful} local tracks indexed "
                   f"from {album_count} albums by {artist_count} artists")
        if failed > 0:
            summary += f" ({failed} failed)"
        logger.info(f"[SoulSync Full Refresh] {summary}")
        add_activity_item("", "SoulSync Full Refresh", summary, "Now")
        _db_update_finished_callback(artist_count, album_count, total, successful, failed)

    except Exception as e:
        logger.error(f"[SoulSync Full Refresh] {e}")
        import traceback
        traceback.print_exc()
        _db_update_error_callback(f"Full refresh failed: {e}")


def _run_soulsync_deep_scan():
    """Deep scan for SoulSync standalone mode.

    1. Scans the output folder for all audio files
    2. Compares against imported Library-v2 file rows (by resolved path)
    3. Untracked files → moved to import folder for auto-import processing
    4. Missing files → normal suspected/confirmed lifecycle (never raw DELETE)
    """
    try:
        import shutil
        transfer_path = docker_resolve_path(config_manager.get('soulseek.transfer_path', './Transfer'))
        staging_path = docker_resolve_path(config_manager.get('import.staging_path', './Staging'))

        if not os.path.isdir(transfer_path):
            _db_update_error_callback(f"Output folder not found: {transfer_path}")
            return

        logger.info(f"[SoulSync Deep Scan] Starting — Transfer: {transfer_path}")
        _db_update_phase_callback('scanning')

        # Phase 1: Collect all audio files in Transfer
        audio_extensions = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif', '.ape'}
        transfer_files = set()
        for root, _dirs, files in os.walk(transfer_path):
            for filename in files:
                if os.path.splitext(filename)[1].lower() in audio_extensions:
                    transfer_files.add(os.path.realpath(os.path.join(root, filename)))

        logger.info(f"[SoulSync Deep Scan] Found {len(transfer_files)} audio files in Transfer")

        # Phase 2: Get all imported file rows that resolve inside Transfer.
        # Entity server_source is deliberately irrelevant: standalone/import
        # ownership and media-server recognition are separate dimensions.
        db = get_database()
        db_paths = set()
        db_file_ids_by_path = {}
        try:
            from core.library2.paths import resolve_lib2_directory, resolve_lib2_path
            transfer_root = os.path.realpath(transfer_path)
            with db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT f.id AS file_id,f.path AS file_path FROM lib2_track_files f"
                    " WHERE f.path IS NOT NULL"
                    "   AND COALESCE(f.file_state, 'active') <> 'deleted'")
                for row in cursor.fetchall():
                    stored = str(row['file_path'] or '')
                    resolved = resolve_lib2_path(stored)
                    resolved_dir = None if resolved else resolve_lib2_directory(stored)
                    candidate = os.path.realpath(
                        resolved
                        or (os.path.join(
                            resolved_dir, os.path.basename(stored.replace('\\', '/'))
                        ) if resolved_dir else stored)
                    )
                    try:
                        if os.path.commonpath((transfer_root, candidate)) != transfer_root:
                            continue
                    except ValueError:
                        continue
                    db_paths.add(candidate)
                    db_file_ids_by_path[candidate] = int(row['file_id'])
        except Exception as e:
            logger.error(f"[SoulSync Deep Scan] Error reading DB paths: {e}")

        logger.info(f"[SoulSync Deep Scan] {len(db_paths)} tracks in soulsync DB")

        # Phase 3: Plan the untracked → Staging move, with the data-loss guard (#904).
        # A path-only diff treats EVERY file the DB doesn't know about as "a new arrival
        # to relocate". When the DB is empty/out of sync with disk (volume swap, DB reset,
        # external tag edits) but Transfer holds the real library, that flags the whole
        # library as untracked and relocates all of it. The planner refuses the move when
        # the untracked share is implausibly large (the desync signature) or when the user
        # marked Transfer as their permanent library — leaving files in place and warning.
        from core.library.standalone_scan import (
            plan_standalone_deep_scan, BLOCK_TRANSFER_PERMANENT, BLOCK_DESYNC,
        )
        never_move = bool(config_manager.get('import.transfer_is_permanent', False))
        plan = plan_standalone_deep_scan(transfer_files, db_paths, never_move=never_move)
        untracked = plan['untracked']
        move_blocked = plan['move_blocked']
        block_reason = plan['block_reason']

        # Phase 4: Move untracked files to Staging for auto-import — unless guarded.
        moved_count = 0
        blocked_count = 0
        if untracked and move_blocked:
            blocked_count = len(untracked)
            if block_reason == BLOCK_TRANSFER_PERMANENT:
                warn = (f"Deep scan: {blocked_count} file(s) in Transfer aren't in the database, "
                        f"but Transfer is marked your permanent library — nothing was moved.")
            else:  # BLOCK_DESYNC
                pct = round(100 * blocked_count / max(1, len(transfer_files)))
                warn = (f"Deep scan STOPPED to protect your library: {blocked_count} of "
                        f"{len(transfer_files)} files in Transfer ({pct}%) aren't in the database. "
                        f"That usually means the database is out of sync with disk, not that you "
                        f"have {blocked_count} new files — so NOTHING was moved. Re-sync/import "
                        f"before scanning, or enable 'Transfer is my permanent library'.")
            logger.warning(f"[SoulSync Deep Scan] {warn}")
            add_activity_item("", "SoulSync Deep Scan — move blocked", warn, "Now")
        elif untracked and os.path.isdir(staging_path):
            _db_update_phase_callback('moving_untracked')
            for file_path in untracked:
                try:
                    # Preserve relative folder structure from Transfer
                    rel_path = os.path.relpath(file_path, transfer_path)
                    dest_path = os.path.join(staging_path, rel_path)
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    shutil.move(file_path, dest_path)
                    moved_count += 1
                except Exception as e:
                    logger.error(f"[SoulSync Deep Scan] Could not move {os.path.basename(file_path)}: {e}")

            # Clean up empty directories in Transfer after moving files
            for root, dirs, _files in os.walk(transfer_path, topdown=False):
                for d in dirs:
                    dir_path = os.path.join(root, d)
                    try:
                        if not os.listdir(dir_path):
                            os.rmdir(dir_path)
                    except OSError:
                        pass

        # Phase 5: Find stale DB records (in DB but file gone from disk)
        _db_update_phase_callback('cleanup')
        stale_count = 0
        stale_file_ids = []
        for db_path in db_paths:
            if db_path not in transfer_files and not os.path.exists(db_path):
                stale_file_ids.append(db_file_ids_by_path[db_path])
                stale_count += 1

        # Guard the deletes the same way as the move (#904): if a desync blocked the
        # move, the DB<->disk mapping is unreliable, so os.path.exists may be lying for
        # every file — don't delete rows. Also independently skip when the stale share
        # is implausibly large (storage unreachable / remount), mirroring the orphan guard.
        from core.library.stale_guard import is_implausible_stale_removal
        if move_blocked and block_reason == BLOCK_DESYNC:
            if stale_file_ids:
                logger.warning(f"[SoulSync Deep Scan] Skipping removal of {stale_count} 'stale' "
                               f"records — move was blocked for desync, mapping is unreliable.")
            stale_file_ids = []
            stale_count = 0
        elif is_implausible_stale_removal(stale_count, len(db_paths)):
            logger.warning(f"[SoulSync Deep Scan] Skipping removal of {stale_count}/{len(db_paths)} "
                           f"'stale' records — implausibly large share, storage likely unreachable.")
            stale_file_ids = []
            stale_count = 0

        # Observe missing files through Library v2's two-scan lifecycle. This
        # preserves metadata, wanted state, history and repair visibility.
        if stale_file_ids:
            try:
                from core.library2.scan import rescan_files
                observed = rescan_files(db, file_ids=stale_file_ids)
                stale_count = int(observed.get('missing', 0))
            except Exception as e:
                stale_count = 0
                logger.error(f"[SoulSync Deep Scan] Error observing missing records: {e}")

        summary = f"Deep scan complete: {len(transfer_files)} files scanned"
        if moved_count > 0:
            summary += f", {moved_count} untracked files moved to Staging"
        if blocked_count > 0:
            summary += f", {blocked_count} untracked files LEFT IN PLACE (move blocked — see warning)"
        if stale_count > 0:
            summary += f", {stale_count} missing files recorded for repair"
        if moved_count == 0 and blocked_count == 0 and stale_count == 0:
            summary += " — library is clean"

        logger.info(f"[SoulSync Deep Scan] {summary}")
        add_activity_item("", "SoulSync Deep Scan", summary, "Now")
        _db_update_finished_callback(0, 0, len(transfer_files), moved_count + stale_count, 0)

    except Exception as e:
        logger.error(f"[SoulSync Deep Scan] {e}")
        import traceback
        traceback.print_exc()
        _db_update_error_callback(f"Deep scan failed: {e}")


def _run_db_update_task(full_refresh, server_type):
    """The actual function that runs in the background thread."""
    global db_update_worker

    # SoulSync standalone
    if server_type == "soulsync":
        if full_refresh:
            _run_soulsync_full_refresh()
        else:
            # Incremental: library updates at download/import time, nothing to do
            logger.warning("[SoulSync Standalone] Incremental scan skipped — library updates at download time. Use Deep Scan or Full Refresh.")
            _db_update_finished_callback(0, 0, 0, 0, 0)
        return

    media_client = None

    if server_type == "plex":
        media_client = media_server_engine.client('plex')
    elif server_type == "jellyfin":
        media_client = media_server_engine.client('jellyfin')
    elif server_type == "navidrome":
        media_client = media_server_engine.client('navidrome')

    if not media_client:
        _db_update_error_callback(f"Media client for '{server_type}' not available.")
        return

    # Pause enrichment workers to reduce DB lock contention during scan
    _pause_workers_for_scan()

    with db_update_lock:
        db_update_worker = DatabaseUpdateWorker(
            media_client=media_client,
            full_refresh=full_refresh,
            server_type=server_type,
            force_sequential=True  # Force sequential processing in web server mode
        )
        # Connect signals to callbacks (handle both Qt and headless modes)
        try:
            # Try Qt signal connection first
            db_update_worker.progress_updated.connect(_db_update_progress_callback)
            db_update_worker.phase_changed.connect(_db_update_phase_callback)
            db_update_worker.artist_processed.connect(_db_update_artist_callback)
            db_update_worker.finished.connect(_db_update_finished_callback)
            db_update_worker.error.connect(_db_update_error_callback)
        except AttributeError:
            # Headless mode - use callback system
            db_update_worker.connect_callback('progress_updated', _db_update_progress_callback)
            db_update_worker.connect_callback('phase_changed', _db_update_phase_callback)
            db_update_worker.connect_callback('artist_processed', _db_update_artist_callback)
            db_update_worker.connect_callback('finished', _db_update_finished_callback)
            db_update_worker.connect_callback('error', _db_update_error_callback)

    # Auto-reconcile runs as the FINAL scan phase (inside the worker, before the
    # 'finished' signal) so status stays 'running' through it — automations, the
    # dashboard card and the Tools page all treat it as part of the scan.
    db_update_worker.post_scan_hook = _reconcile_after_scan

    # This is a blocking call that runs the worker logic
    db_update_worker.run()


def _run_deep_scan_task(server_type):
    """Run a deep library scan in the background thread."""
    global db_update_worker
    media_client = None

    if server_type == "plex":
        media_client = media_server_engine.client('plex')
    elif server_type == "jellyfin":
        media_client = media_server_engine.client('jellyfin')
    elif server_type == "navidrome":
        media_client = media_server_engine.client('navidrome')
    elif server_type == "soulsync":
        # SoulSync standalone deep scan: find untracked files → move to Staging,
        # remove stale DB records where files no longer exist on disk
        _run_soulsync_deep_scan()
        return

    if not media_client:
        _db_update_error_callback(f"Media client for '{server_type}' not available.")
        return

    # Pause enrichment workers to reduce DB lock contention during deep scan
    _pause_workers_for_scan()

    with db_update_lock:
        db_update_worker = DatabaseUpdateWorker(
            media_client=media_client,
            full_refresh=False,
            server_type=server_type,
            force_sequential=True
        )
        try:
            db_update_worker.progress_updated.connect(_db_update_progress_callback)
            db_update_worker.phase_changed.connect(_db_update_phase_callback)
            db_update_worker.artist_processed.connect(_db_update_artist_callback)
            db_update_worker.finished.connect(_db_update_finished_callback)
            db_update_worker.error.connect(_db_update_error_callback)
        except AttributeError:
            db_update_worker.connect_callback('progress_updated', _db_update_progress_callback)
            db_update_worker.connect_callback('phase_changed', _db_update_phase_callback)
            db_update_worker.connect_callback('artist_processed', _db_update_artist_callback)
            db_update_worker.connect_callback('finished', _db_update_finished_callback)
            db_update_worker.connect_callback('error', _db_update_error_callback)

    # Auto-reconcile runs as the FINAL scan phase (see _run_database_update_task).
    db_update_worker.post_scan_hook = _reconcile_after_scan

    # Run deep scan instead of normal run()
    db_update_worker.run_deep_scan()


@bp.route('/api/database/stats', methods=['GET'])
def get_database_stats():
    """Endpoint to get current database statistics."""
    try:
        # This endpoint returns the same stats shape the UI expects.
        db = get_database()
        stats = db.get_database_info_for_server()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting database stats: {e}")
        return jsonify({"error": str(e)}), 500

# ── wishlist endpoints live in api/wishlist_routes.py now ────────────────────

@bp.route('/api/database/update', methods=['POST'])
@admin_only
def start_database_update():
    """Endpoint to start the database update process."""
    global db_update_worker
    with db_update_lock:
        if db_update_state["status"] == "running":
            return jsonify({"success": False, "error": "An update is already in progress."}), 409

        data = request.get_json()
        full_refresh = data.get('full_refresh', False)
        deep_scan = data.get('deep_scan', False)
        active_server = config_manager.get_active_media_server()

        scan_type = "Deep scan" if deep_scan else ("Full" if full_refresh else "Incremental")
        db_update_state.update({
            "status": "running",
            "phase": f"{scan_type}: Initializing...",
            "progress": 0, "current_item": "", "processed": 0, "total": 0, "error_message": "",
            # Seed the heartbeat now so a worker that hangs during init (before the
            # first progress/phase callback) is still caught by the stall watchdog.
            "last_progress_at": time.time(),
        })

        # Add activity for database update start
        server_name = active_server.capitalize()
        add_activity_item("", "Database Update", f"Starting {scan_type.lower()} update from {server_name}...", "Now")

        # Submit the appropriate worker
        if deep_scan:
            db_update_executor.submit(_run_deep_scan_task, active_server)
        else:
            db_update_executor.submit(_run_db_update_task, full_refresh, active_server)

    return jsonify({"success": True, "message": "Database update started."})

@bp.route('/api/database/update/status', methods=['GET'])
def get_database_update_status():
    """Endpoint to poll for the current update status."""
    _check_db_update_stall()  # self-heal a hung job before reporting (#859)
    with db_update_lock:
        # Debug: Log current state occasionally
        if db_update_state["status"] == "running":
            logger.info(f"[Status Check] {db_update_state['processed']}/{db_update_state['total']} ({db_update_state['progress']:.1f}%) - {db_update_state['phase']}")
        return jsonify(db_update_state)

@bp.route('/api/database/update/stop', methods=['POST'])
@admin_only
def stop_database_update():
    """Endpoint to stop the current database update."""
    global db_update_worker
    with db_update_lock:
        if db_update_worker and db_update_state["status"] == "running":
            db_update_worker.stop()
            db_update_state["status"] = "finished"
            db_update_state["phase"] = "Update stopped by user."
            return jsonify({"success": True, "message": "Stop request sent."})
        else:
            return jsonify({"success": False, "error": "No update is currently running."}), 404

_BACKUP_FILENAME_RE = re.compile(r'^music_library\.db\.backup_\d{8}_\d{6}$')

@bp.route('/api/database/backup', methods=['POST'])
@admin_only
def backup_database_endpoint():
    """Create a rolling backup of the database (max 5)."""
    try:
        import glob as _glob
        from core.db_integrity import DBIntegrityError, safe_backup, prune_backups
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        if not os.path.exists(db_path):
            return jsonify({"success": False, "error": "Database file not found"}), 404
        max_backups = 5
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = f"{db_path}.backup_{timestamp}"
        # safe_backup verifies the SOURCE is healthy before copying and the
        # RESULT after — so a corrupt DB can never silently produce a backup
        # (the incident where every rolling backup copied the corruption).
        try:
            safe_backup(db_path, backup_path)
        except DBIntegrityError as integ:
            logger.error("Backup refused — database integrity check failed: %s", integ)
            return jsonify({
                "success": False,
                "error": "Database failed its integrity check — backup refused to avoid "
                         "saving a corrupt copy. Your existing backups are untouched. " + str(integ),
                "integrity_failed": True,
            }), 409
        size_mb = round(os.path.getsize(backup_path) / (1024 * 1024), 1)
        # Write version metadata sidecar
        meta_path = backup_path + '.meta.json'
        try:
            with open(meta_path, 'w') as mf:
                json.dump({"version": SOULSYNC_VERSION, "created": timestamp}, mf)
        except Exception as e:
            logger.debug("backup meta sidecar write: %s", e)
        # Rolling cleanup — prune_backups never deletes the most-recent
        # VERIFIED-HEALTHY backup, even to honor max_backups, so a run of bad
        # backups can't evict your last good snapshot (the incident).
        existing = [f for f in _glob.glob(f"{db_path}.backup_*")
                    if not f.endswith('.meta.json')]
        for removed in prune_backups(existing, max_backups):
            try:
                os.remove(removed)
                if os.path.exists(removed + '.meta.json'):
                    os.remove(removed + '.meta.json')
            except Exception as e:
                logger.debug("rolling backup cleanup failed: %s", e)
        return jsonify({"success": True, "backup_path": backup_path, "size_mb": size_mb, "version": SOULSYNC_VERSION})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/database/backups', methods=['GET'])
def list_backups_endpoint():
    """List all database backups with metadata."""
    try:
        import glob as _glob
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        backup_files = sorted(
            _glob.glob(f"{db_path}.backup_*"),
            key=os.path.getmtime,
            reverse=True
        )
        backups = []
        for fp in backup_files:
            fname = os.path.basename(fp)
            if not _BACKUP_FILENAME_RE.match(fname):
                continue
            stat = os.stat(fp)
            entry = {
                'filename': fname,
                'size_mb': round(stat.st_size / (1024 * 1024), 2),
                'created': datetime.utcfromtimestamp(stat.st_mtime).isoformat()
            }
            # Read version from sidecar metadata if available
            meta_path = fp + '.meta.json'
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, 'r') as mf:
                        meta = json.load(mf)
                    entry['version'] = meta.get('version')
                except Exception as e:
                    logger.debug("backup metadata read failed: %s", e)
            backups.append(entry)
        db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2) if os.path.exists(db_path) else 0
        return jsonify({
            'success': True,
            'backups': backups,
            'count': len(backups),
            'db_size_mb': db_size_mb
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/database/backups/<filename>', methods=['DELETE'])
@admin_only
def delete_backup_endpoint(filename):
    """Delete a specific database backup."""
    try:
        if not _BACKUP_FILENAME_RE.match(filename) or '/' in filename or '\\' in filename or '..' in filename:
            return jsonify({"success": False, "error": "Invalid backup filename"}), 400
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        backup_path = os.path.join(os.path.dirname(db_path), filename)
        if not os.path.exists(backup_path):
            return jsonify({"success": False, "error": "Backup not found"}), 404
        os.remove(backup_path)
        # Also remove sidecar metadata if present
        meta_path = backup_path + '.meta.json'
        if os.path.exists(meta_path):
            try:
                os.remove(meta_path)
            except Exception as e:
                logger.debug("backup sidecar removal failed: %s", e)
        return jsonify({"success": True, "deleted": filename})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/database/backups/<filename>/restore', methods=['POST'])
@admin_only
def restore_backup_endpoint(filename):
    """Restore the database from a specific backup."""
    try:
        import sqlite3
        if not _BACKUP_FILENAME_RE.match(filename) or '/' in filename or '\\' in filename or '..' in filename:
            return jsonify({"success": False, "error": "Invalid backup filename"}), 400
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        db_dir = os.path.dirname(db_path)
        backup_path = os.path.join(db_dir, filename)
        if not os.path.exists(backup_path):
            return jsonify({"success": False, "error": "Backup not found"}), 404

        # Check version compatibility
        backup_version = None
        meta_path = backup_path + '.meta.json'
        if os.path.exists(meta_path):
            try:
                with open(meta_path, 'r') as mf:
                    meta = json.load(mf)
                backup_version = meta.get('version')
            except Exception as e:
                logger.debug("backup version metadata read failed: %s", e)

        version_warning = None
        # Compare base versions only (strip +commit suffix) to avoid false mismatches
        _backup_base = backup_version.split('+')[0] if backup_version else None
        _current_base = SOULSYNC_VERSION.split('+')[0]
        if _backup_base and _backup_base != _current_base:
            # Allow restore but warn — the caller must pass force=true to confirm
            force = request.json.get('force', False) if request.is_json else False
            if not force:
                return jsonify({
                    "success": False,
                    "version_mismatch": True,
                    "backup_version": backup_version,
                    "current_version": SOULSYNC_VERSION,
                    "error": f"This backup was created on SoulSync v{backup_version}, but you're running v{SOULSYNC_VERSION}. Restoring may cause issues. Send force=true to proceed."
                }), 409
            version_warning = f"Restored from v{backup_version} backup (current: v{SOULSYNC_VERSION})"

        # Create safety backup of current DB before restoring
        safety_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        safety_filename = f"music_library.db.backup_{safety_ts}"
        safety_path = os.path.join(db_dir, safety_filename)
        src_conn = sqlite3.connect(db_path)
        dst_conn = sqlite3.connect(safety_path)
        src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
        # Write version metadata for the safety backup too
        try:
            with open(safety_path + '.meta.json', 'w') as mf:
                json.dump({"version": SOULSYNC_VERSION, "created": safety_ts}, mf)
        except Exception as e:
            logger.debug("safety backup metadata write failed: %s", e)

        # Restore using SQLite backup API (handles concurrent access safely)
        from database.music_database import close_database, get_database
        close_database()

        src_restore = sqlite3.connect(backup_path)
        dst_restore = sqlite3.connect(db_path)
        src_restore.backup(dst_restore)
        dst_restore.close()
        src_restore.close()

        # Reinitialize database and verify
        db = get_database()
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM lib2_artists")
            artist_count = cursor.fetchone()[0]

        # MIG-02: `get_database()` only creates the lib2 schema (DDL, no
        # backfills). A backup taken before the catalogue cutover — or any
        # backup whose lib2_* tables are empty — therefore lands as an EMPTY
        # native catalogue, and the startup migration that would have filled it
        # already retired. Left alone, the supervisor sees a required migration
        # it will never run and pauses every catalogue worker indefinitely.
        # Re-arm the same lifecycle startup uses so the restored database gets
        # migrated instead of just being declared restored.
        migration_restarted = False
        try:
            from core.library2.migration_gate import migration_required
            if migration_required(db) and _restart_library_v2_migration is not None:
                migration_restarted = bool(_restart_library_v2_migration())
                logger.info(
                    "Restore left the native catalogue unmigrated; migration "
                    "re-armed (started=%s)", migration_restarted)
        except Exception as e:
            logger.warning("Could not re-arm the catalogue migration after restore: %s", e)

        result = {
            "success": True,
            "restored_from": filename,
            "safety_backup": safety_filename,
            "artist_count": artist_count,
            "migration_restarted": migration_restarted,
        }
        if backup_version:
            result["backup_version"] = backup_version
        if version_warning:
            result["version_warning"] = version_warning
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/database/backups/<filename>/download', methods=['GET'])
def download_backup_endpoint(filename):
    """Download a specific database backup file."""
    try:
        if not _BACKUP_FILENAME_RE.match(filename) or '/' in filename or '\\' in filename or '..' in filename:
            return jsonify({"success": False, "error": "Invalid backup filename"}), 400
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        backup_path = os.path.join(os.path.dirname(db_path), filename)
        if not os.path.exists(backup_path):
            return jsonify({"success": False, "error": "Backup not found"}), 404
        # Override the default static-cache max-age — this is a sensitive
        # DB backup, browsers should never cache it.
        response = send_file(backup_path, as_attachment=True, download_name=filename)
        response.headers['Cache-Control'] = 'no-store'
        return response
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ===============================
# == DATABASE MAINTENANCE      ==
# ===============================

@bp.route('/api/database/maintenance/info', methods=['GET'])
def database_maintenance_info():
    """Get database size, free pages, and auto_vacuum mode."""
    try:
        import sqlite3
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute('PRAGMA page_count'); total_pages = c.fetchone()[0]
        c.execute('PRAGMA freelist_count'); free_pages = c.fetchone()[0]
        c.execute('PRAGMA page_size'); page_size = c.fetchone()[0]
        c.execute('PRAGMA auto_vacuum'); auto_vacuum = c.fetchone()[0]
        conn.close()

        total_bytes = total_pages * page_size
        free_bytes = free_pages * page_size
        auto_vacuum_labels = {0: 'None', 1: 'Full', 2: 'Incremental'}

        return jsonify({
            'success': True,
            'total_size': total_bytes,
            'total_size_display': f'{total_bytes / 1024 / 1024:.1f} MB',
            'free_pages': free_pages,
            'free_size': free_bytes,
            'free_size_display': f'{free_bytes / 1024 / 1024:.1f} MB',
            'bloat_percent': round(free_pages / total_pages * 100, 1) if total_pages > 0 else 0,
            'auto_vacuum': auto_vacuum,
            'auto_vacuum_label': auto_vacuum_labels.get(auto_vacuum, 'Unknown'),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/database/maintenance/vacuum', methods=['POST'])
@admin_only
def database_vacuum():
    """Run VACUUM to compact the database. Locks DB during operation."""
    try:
        import sqlite3, time
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')

        # Get size before
        size_before = os.path.getsize(db_path)

        conn = sqlite3.connect(db_path)
        start = time.time()
        conn.execute('VACUUM')
        elapsed = time.time() - start
        conn.close()

        size_after = os.path.getsize(db_path)
        saved = size_before - size_after

        logger.info(f"Database VACUUM completed in {elapsed:.1f}s — saved {saved / 1024 / 1024:.1f} MB")
        return jsonify({
            'success': True,
            'elapsed_seconds': round(elapsed, 1),
            'size_before': size_before,
            'size_after': size_after,
            'saved_bytes': saved,
            'saved_display': f'{saved / 1024 / 1024:.1f} MB',
        })
    except Exception as e:
        logger.error(f"Database VACUUM failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/database/maintenance/enable-incremental-vacuum', methods=['POST'])
def enable_incremental_vacuum():
    """Enable incremental auto_vacuum. Requires a full VACUUM to activate."""
    try:
        import sqlite3, time
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')

        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute('PRAGMA auto_vacuum')
        current = c.fetchone()[0]

        if current == 2:
            conn.close()
            return jsonify({'success': True, 'message': 'Incremental vacuum is already enabled', 'already_enabled': True})

        size_before = os.path.getsize(db_path)

        # Set incremental mode and VACUUM to activate it
        c.execute('PRAGMA auto_vacuum = INCREMENTAL')
        start = time.time()
        conn.execute('VACUUM')
        elapsed = time.time() - start
        conn.close()

        size_after = os.path.getsize(db_path)
        saved = size_before - size_after

        logger.info(f"Incremental auto_vacuum enabled in {elapsed:.1f}s — saved {saved / 1024 / 1024:.1f} MB")
        return jsonify({
            'success': True,
            'message': 'Incremental vacuum enabled',
            'elapsed_seconds': round(elapsed, 1),
            'saved_display': f'{saved / 1024 / 1024:.1f} MB',
        })
    except Exception as e:
        logger.error(f"Failed to enable incremental vacuum: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
