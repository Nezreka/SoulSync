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
from core.permissions import requires
from core.profile_context import admin_only, is_admin_request
from core.runtime_state import add_activity_item
from utils.logging_config import get_logger

logger = get_logger("web_server")

# module-local mutable state: the update worker handle and the automation id
# the callbacks tag their progress with
db_update_worker = None
_db_update_automation_id = None
# H16: epoch of the DB-update task currently on the executor. Bumped at every
# run start (automation handler, manual endpoints); the finished/error
# callbacks only commit for the current epoch, so a stale worker finishing
# after the stall watchdog handed the run to a successor can't overwrite the
# new run's state.
_active_task_epoch = 0


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

def _db_update_progress_callback(current_item, processed, total, percentage, unit="artists"):
    # unit labels what processed/total count ("artists" for the scan phases,
    # "tracks" for the post-scan tag reconcile) so the Tools page progress
    # line reads "176 / 191 tracks" instead of "176 / 191 artists".
    logger.info(f"[DB Progress] {current_item} - {processed}/{total} ({percentage:.1f}%)")
    with db_update_lock:
        db_update_state.update({
            "current_item": current_item,
            "processed": processed,
            "total": total,
            "progress": percentage,
            "unit": unit,
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

def _db_update_heartbeat():
    """A scan's inner loops are turning (#2036). Only the clock moves: the
    phase and the counts stay what the scan last said."""
    with db_update_lock:
        db_update_state["last_progress_at"] = time.time()


# every DatabaseUpdateWorker beats into this, whoever built it
import core.database_update_worker as _database_update_worker  # noqa: E402

_database_update_worker.heartbeat_sink = _db_update_heartbeat


def _db_update_artist_callback(artist_name, success, details, album_count, track_count):
    # a finished artist is progress, whatever else the worker reported
    with db_update_lock:
        db_update_state["last_progress_at"] = time.time()
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

def _terminal_callback_for_current_run(kind: str) -> bool:
    """H16 gate: True when the calling worker task belongs to the run epoch
    that currently owns db_update_state.

    The stall watchdog flips a hung 'running' job to 'error' without stopping
    its worker thread; the next run then queues behind the hung worker on the
    single-worker executor. When the stale worker finally fires its terminal
    callback, the state already belongs to the successor run — committing the
    write would fake a 'finished'/'error' the new run never produced. Drop it.
    """
    with db_update_lock:
        current = (db_update_state or {}).get("run_epoch", 0)
        if _active_task_epoch != current:
            logger.warning(
                "[DB Update] Ignoring %s callback from a superseded run "
                "(task epoch %s, current epoch %s)",
                kind, _active_task_epoch, current)
            return False
    return True


def _db_update_finished_callback(total_artists, total_albums, total_tracks, successful, failed):
    global _db_update_automation_id
    if not _terminal_callback_for_current_run("finished"):
        return
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
                rewrite_to=config_manager.get('m3u_export.rewrite_to', '') or '',
                relative=bool(config_manager.get('m3u_export.relative_paths', False)))
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

    # #2332: a secondary library folder whose music the server stopped
    # returning (its account lost access) gets a warning, once
    try:
        from core.library_rules import after_scan as _secondary_after_scan
        _secondary_after_scan(get_database())
    except Exception as e:
        logger.debug("secondary library check after scan failed: %s", e)

    # the dashboard's library counts are cached; a scan just changed them
    try:
        from database.music_database import MusicDatabase
        MusicDatabase.invalidate_database_info()
    except Exception as e:
        logger.debug("db info cache invalidation failed: %s", e)

    # the sync match cache is kept. every read re-checks its row (the track
    # still on the server, still the same song, not unmatched), so a scan
    # leaves nothing to clear. wiping it here made the auto-sync this update
    # fires re-match every playlist track from scratch, minutes per playlist

    try:
        if automation_engine:
            automation_engine.emit('database_update_completed', {
                'total_artists': str(total_artists),
                'total_albums': str(total_albums),
                'total_tracks': str(total_tracks),
            })
    except Exception as e:
        logger.debug("library_updated automation emit failed: %s", e)

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
    if not _terminal_callback_for_current_run("error"):
        return
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

def _background_workers():
    """the enrichment and maintenance workers a scan or a sync pauses."""
    return {
        'mb': _mb_worker(), 'spotify': _spotify_enrichment_worker(), 'itunes': _itunes_enrichment_worker(),
        'deezer': _deezer_worker(), 'audiodb': _audiodb_worker(), 'discogs': _discogs_worker(), 'lastfm': _lastfm_worker(),
        'genius': _genius_worker(), 'tidal': _tidal_enrichment_worker(), 'qobuz': _qobuz_enrichment_worker(),
        'amazon': _amazon_worker(), 'repair': _repair_worker(), 'soulid': _soulid_worker(), 'jiosaavn': _jiosaavn_worker(),
        'bandcamp': _bandcamp_worker(),
    }


# a playlist sync takes the same hold (core/discovery/sync.py), so the two
# share one count: neither resumes workers the other still needs paused
from core import background_pause as _background_pause  # noqa: E402

_background_pause.register_workers(_background_workers)


def _pause_workers_for_scan():
    """Pause all enrichment and maintenance workers during database scans to reduce lock contention."""
    _background_pause.hold("database scan")


def _resume_workers_after_scan():
    """Resume only the workers that WE paused (don't resume manually-paused ones)."""
    _background_pause.release("database scan")


def _file_key(path):
    try:
        return os.path.normcase(os.path.realpath(path))
    except Exception:  # noqa: BLE001
        return os.path.normcase(str(path))


def _known_library_files(db):
    """every file row the library holds, by its real path: (id, state).
    review 4 R4-D-3: a 'deleted' row is not a known file. a file back at that
    path falls through to autolink, which re-activates the row"""
    from core.library2.paths import resolve_lib2_path
    known = {}
    with db._get_connection() as conn:
        rows = conn.execute("SELECT id, path, COALESCE(file_state,'active') FROM lib2_track_files "
                            "WHERE path IS NOT NULL AND path<>'' "
                            "AND COALESCE(file_state,'active') <> 'deleted'").fetchall()
    for file_id, path, state in rows:
        for candidate in {path, resolve_lib2_path(path, config_manager=config_manager) or path}:
            key = _file_key(candidate)
            # an active row wins over a retired one for the same file
            if key not in known or known[key][1] != 'active':
                known[key] = (int(file_id), state)
    return known


def _other_music_folders(db, main_folder):
    """``(folders, excluded)``: the music library's other folders, the ones
    beside the main music folder: the extra music folders in Settings and each
    profile's own library folder, as the folder watch lists them
    (core/library/folder_watch.configured_folders). a library on more than
    one drive is one library: a scan reads all of it, whether or not the
    watch is on. a folder inside the main one is read with the main one."""
    try:
        from core.library.folder_watch import configured_folders, is_under, norm
        folders, _import, excluded = configured_folders(config_manager, db)
        main = norm(docker_resolve_path(main_folder))
        mains = {main, norm(main_folder), norm(os.path.realpath(main_folder))}
        out = [f for f in folders if f not in mains and not any(is_under(f, m) for m in mains)]
        return out, list(excluded)
    except Exception as exc:  # noqa: BLE001 - the main folder is still scanned
        logger.warning("could not list the other music folders: %s", exc)
        return [], []


def _scan_other_music_folders(db, main_folder, *, known=None, new_only=False):
    """read the music folders beside the main one, the way the folder watch
    reads a changed folder (core/library2/folder_rescan.py): files the
    library doesn't know are indexed in place, and unless ``new_only``, moved
    files are followed, known ones re-read and missing ones recorded. a folder
    that can't be reached is skipped and said, never read as empty.
    returns ``{folders, added, missing, moved, unreachable: [folders]}``."""
    out = {"folders": 0, "added": 0, "missing": 0, "moved": 0, "unreachable": []}
    folders, excluded = _other_music_folders(db, main_folder)
    if not folders:
        return out
    from core.library2 import folder_rescan
    for folder in folders:
        _db_update_phase_callback(f'Reading {folder}...')
        try:
            if new_only:
                if not folder_rescan._reachable(folder):
                    out["unreachable"].append(folder)
                    continue
                known = known if known is not None else _known_library_files(db)
                fresh = sorted(p for p in folder_rescan._audio_under(folder, True, excluded)
                               if _file_key(p) not in known)
                out["added"] += len(folder_rescan._adopt(db, fresh)) if fresh else 0
            else:
                res = folder_rescan.process_changes(db, folder, [(folder, True)], excluded=excluded,
                                                    config_manager=config_manager)
                if res.get("outage"):
                    out["unreachable"].append(folder)
                    continue
                for key in ("added", "missing", "moved"):
                    out[key] += int(res.get(key) or 0)
            out["folders"] += 1
        except Exception as exc:  # noqa: BLE001 - one folder never stops the scan
            logger.error("[SoulSync scan] could not read %s: %s", folder, exc)
            out["unreachable"].append(folder)
    if out["unreachable"]:
        logger.warning("[SoulSync scan] not reachable, skipped: %s", ", ".join(out["unreachable"]))
    return out


def _other_folders_words(other) -> str:
    """what the other music folders added to a scan, for its summary"""
    words = ""
    if other.get("folders"):
        bits = [f"{other['added']} new"] if other.get("added") else []
        if other.get("moved"):
            bits.append(f"{other['moved']} moved")
        if other.get("missing"):
            bits.append(f"{other['missing']} missing")
        n = other["folders"]
        words += f"; {n} other music folder{'s' if n != 1 else ''} read" + \
                 (f" ({', '.join(bits)})" if bits else "")
    if other.get("unreachable"):
        words += f"; {len(other['unreachable'])} music folder(s) not reachable, skipped"
    return words


def _readable_audio(file_path) -> bool:
    """the file opens as audio and has a length. a file that doesn't (zeroed
    by a copy that never finished, cut short, not audio at all) has no tags
    to read, and indexing it anyway filed it as a one-song album under
    "Unknown Artist" that nothing could play"""
    try:
        import mutagen
        audio = mutagen.File(file_path)
    except Exception:  # noqa: BLE001 - unreadable is the answer
        return False
    info = getattr(audio, "info", None) if audio is not None else None
    return bool(info is not None and (getattr(info, "length", 0) or 0) > 0)


def _index_untracked_in_place(db, paths, label):
    """index files the library doesn't know, where they sit, through the
    native import writer. a file that isn't readable audio is left out and
    named in the log. returns (new file ids, failed)."""
    from core.library2.autolink import link_download_into_library_v2
    new_ids, failed = [], 0
    damaged = []
    for index, file_path in enumerate(paths, 1):
        if not _readable_audio(file_path):
            damaged.append(file_path)
            failed += 1
            continue
        try:
            file_id = link_download_into_library_v2(
                {'_final_processed_path': file_path, '_download_username': 'standalone_refresh',
                 'username': 'standalone_refresh'},
                raise_on_error=True)
            if file_id is None:
                raise RuntimeError("native import returned no file row")
            new_ids.append(int(file_id))
        except Exception as exc:  # noqa: BLE001 - one bad file never stops the scan
            failed += 1
            logger.error("[%s] Could not import %s: %s", label, file_path, exc)
        if index % 50 == 0:
            _db_update_phase_callback(f'Indexing new files... {index}/{len(paths)}')
    if damaged:
        shown = "; ".join(damaged[:10]) + (f" and {len(damaged) - 10} more" if len(damaged) > 10 else "")
        logger.warning("[%s] %d files aren't readable audio and were left out (damaged or not audio): %s",
                       label, len(damaged), shown)
        add_activity_item("", f"{label}: damaged files left out",
                          f"{len(damaged)} files in your music folder aren't readable audio, so they "
                          f"weren't added: {shown}", "Now")
    return new_ids, failed


def _skip_hidden_dirs(dirs) -> None:
    """keep an os.walk out of hidden folders, in place. an album still
    downloading sits in <music folder>/.soulsync_atomic_staging until it is
    whole: walked, a refresh indexed half an album as library tracks and a
    deep scan moved its files to the import folder mid-download. the
    standalone library's own reader skips them for the same reason
    (core/soulsync_client.py), and so does the usual hidden clutter."""
    dirs[:] = [d for d in dirs if not d.startswith('.')]


def _run_soulsync_full_refresh(incremental=False):
    """Re-index the standalone output folder through the native import writer.

    This is an explicit local import/recovery operation, not a media-server
    sync.  It never clears catalogue rows or server mappings up front.

    ``incremental`` indexes only the files the library doesn't know yet and
    skips the re-read of every known file. it used to skip the run outright
    and still say complete, so a file dropped in by hand never showed (#2111).
    """
    label = "SoulSync Incremental Update" if incremental else "SoulSync Full Refresh"
    try:
        transfer_path = docker_resolve_path(config_manager.get('soulseek.transfer_path', './Transfer'))
        if not os.path.isdir(transfer_path):
            _db_update_error_callback(f"Music library folder not found: {transfer_path}")
            return

        logger.info("[%s] Indexing local files: %s", label, transfer_path)
        db = get_database()

        _db_update_phase_callback('Scanning music library folder...')
        from core.repair_jobs.base import walk_library

        audio_exts = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif', '.ape'}
        audio_files = []
        for root, _dirs, files in walk_library(transfer_path):
            _skip_hidden_dirs(_dirs)
            for fname in files:
                if os.path.splitext(fname)[1].lower() in audio_exts:
                    audio_files.append(os.path.join(root, fname))

        # Deterministic order, independent of the filesystem walk (#1696).
        audio_files.sort()

        total = len(audio_files)
        logger.info("[%s] Found %d audio files", label, total)
        _db_update_phase_callback(f'Importing local index from {total} files...')
        successful = 0
        failed = 0
        # a file the library already holds is re-indexed in place, never
        # imported again: autolink on a known file made a second file row on
        # the same path, under its track-artist tag (a compilation split into
        # one album per performer). 3.6.1 rebuilt one row per file.
        known = _known_library_files(db)
        reactivated = kept = 0
        new_file_ids = []
        from core.library2.autolink import link_download_into_library_v2
        for index, file_path in enumerate(audio_files, 1):
            existing = known.get(_file_key(file_path))
            if existing is not None:
                file_row_id, state = existing
                if state in ('missing_suspected', 'missing_confirmed'):
                    with db._get_connection() as conn:
                        conn.execute("UPDATE lib2_track_files SET file_state='active', "
                                     "updated_at=CURRENT_TIMESTAMP WHERE id=?", (file_row_id,))
                        conn.commit()
                    reactivated += 1
                else:
                    kept += 1
                successful += 1
                if index % 50 == 0:
                    _db_update_phase_callback(f'Importing local index... {index}/{total}')
                continue
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
                new_file_ids.append(int(file_id))
                successful += 1
            except Exception as exc:
                failed += 1
                logger.error("[%s] Could not import %s: %s", label, file_path, exc)
            if index % 50 == 0:
                _db_update_phase_callback(f'Importing local index... {index}/{total}')

        logger.info("[%s] %d already indexed, %d back from missing, %d new",
                    label, kept, reactivated, successful - kept - reactivated)

        if incremental:
            other = _scan_other_music_folders(db, transfer_path, known=known, new_only=True)
            artist_count, album_count = _artist_album_counts(db, new_file_ids)
            summary = (f"Incremental update complete: {len(new_file_ids)} new local tracks indexed, "
                       f"{kept + reactivated} already in the library")
            if failed > 0:
                summary += f" ({failed} failed)"
            summary += _other_folders_words(other)
            logger.info("[%s] %s", label, summary)
            add_activity_item("", label, summary, "Now")
            _db_update_finished_callback(artist_count, album_count, len(new_file_ids),
                                         successful, failed)
            return

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

        other = _scan_other_music_folders(db, transfer_path)
        artist_count, album_count = _artist_album_counts(db, scoped_file_ids)
        summary = (f"Full refresh complete: {successful} local tracks indexed "
                   f"from {album_count} albums by {artist_count} artists")
        if failed > 0:
            summary += f" ({failed} failed)"
        summary += _other_folders_words(other)
        logger.info(f"[SoulSync Full Refresh] {summary}")
        add_activity_item("", "SoulSync Full Refresh", summary, "Now")
        _db_update_finished_callback(artist_count, album_count, total, successful, failed)

    except Exception as e:
        logger.error(f"[{label}] {e}")
        import traceback
        traceback.print_exc()
        _db_update_error_callback(f"{'Incremental update' if incremental else 'Full refresh'} failed: {e}")


def _artist_album_counts(db, file_ids):
    """(artists, albums) the given file rows belong to."""
    artist_ids, album_ids = set(), set()
    with db._get_connection() as conn:
        for start in range(0, len(file_ids), 500):
            chunk = file_ids[start:start + 500]
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
    return len(artist_ids), len(album_ids)


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
            _db_update_error_callback(f"Music library folder not found: {transfer_path}")
            return

        logger.info(f"[SoulSync Deep Scan] Starting — music library folder: {transfer_path}")
        _db_update_phase_callback('scanning')

        # Phase 1: Collect all audio files in Transfer
        from core.repair_jobs.base import is_internal_transfer_dir, walk_library

        audio_extensions = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif', '.ape'}
        transfer_files = set()
        for root, _dirs, files in walk_library(transfer_path):
            _skip_hidden_dirs(_dirs)
            for filename in files:
                if os.path.splitext(filename)[1].lower() in audio_extensions:
                    transfer_files.add(os.path.realpath(os.path.join(root, filename)))

        logger.info(f"[SoulSync Deep Scan] Found {len(transfer_files)} audio files in the music library folder")

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
        never_move = bool(config_manager.get('import.transfer_is_permanent', True))
        plan = plan_standalone_deep_scan(transfer_files, db_paths, never_move=never_move)
        untracked = plan['untracked']
        move_blocked = plan['move_blocked']
        block_reason = plan['block_reason']

        # Phase 4: Move untracked files to Staging for auto-import — unless guarded.
        moved_count = 0
        blocked_count = 0
        indexed_count = index_failed = 0
        if untracked and move_blocked and block_reason == BLOCK_TRANSFER_PERMANENT:
            # the music folder is the library, so a file in it the library
            # doesn't know is not something to move away: it is something a
            # scan missed. it is indexed where it sits, by the same test and
            # the same writer the incremental update uses. (this used to warn
            # "nothing was moved" and leave the files out of the library, on a
            # scan whose whole point is to catch what was missed.)
            _db_update_phase_callback('indexing_untracked')
            known = _known_library_files(db)
            fresh = sorted(p for p in untracked if _file_key(p) not in known)
            new_ids, index_failed = _index_untracked_in_place(db, fresh, "SoulSync Deep Scan")
            indexed_count = len(new_ids)
        elif untracked and move_blocked:
            # BLOCK_DESYNC: the folder is not marked permanent, so untracked
            # files would be MOVED, and far too many are untracked to believe
            blocked_count = len(untracked)
            pct = round(100 * blocked_count / max(1, len(transfer_files)))
            warn = (f"Deep scan STOPPED to protect your library: {blocked_count} of "
                    f"{len(transfer_files)} files in your Music Library ({pct}%) aren't in the database. "
                    f"That usually means the database is out of sync with disk, not that you "
                    f"have {blocked_count} new files — so NOTHING was moved. Re-sync/import "
                    f"before scanning, or enable 'Music Library is my permanent library'.")
            logger.warning(f"[SoulSync Deep Scan] {warn}")
            add_activity_item("", "SoulSync Deep Scan — move blocked", warn, "Now")
        elif untracked and os.path.isdir(staging_path):
            _db_update_phase_callback('moving_untracked')
            # G03-6: the walk's paths are realpaths, so the root they're made
            # relative to must be too. against a symlinked Transfer each one
            # came out "../../mnt/disk1/..." and the move left Staging
            real_transfer = os.path.realpath(transfer_path)
            real_staging = os.path.realpath(staging_path)
            for file_path in untracked:
                try:
                    # Preserve relative folder structure from Transfer
                    rel_path = os.path.relpath(file_path, real_transfer)
                    dest_path = os.path.join(staging_path, rel_path)
                    if os.path.commonpath((real_staging, os.path.realpath(dest_path))) != real_staging:
                        logger.warning(f"[SoulSync Deep Scan] Not moving {file_path}: it would land outside Staging")
                        continue
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    shutil.move(file_path, dest_path)
                    moved_count += 1
                except Exception as e:
                    logger.error(f"[SoulSync Deep Scan] Could not move {os.path.basename(file_path)}: {e}")

            # Clean up empty directories in Transfer after moving files
            for root, dirs, _files in os.walk(transfer_path, topdown=False):
                for d in dirs:
                    dir_path = os.path.join(root, d)
                    if is_internal_transfer_dir(dir_path, transfer_path):
                        continue
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

        # the library's other folders (more than one music drive is one library)
        other = _scan_other_music_folders(db, transfer_path)

        summary = f"Deep scan complete: {len(transfer_files)} files scanned"
        if indexed_count > 0:
            summary += f", {indexed_count} new files added"
        if index_failed > 0:
            summary += f", {index_failed} could not be added"
        if moved_count > 0:
            summary += f", {moved_count} untracked files moved to Staging"
        if blocked_count > 0:
            summary += f", {blocked_count} untracked files LEFT IN PLACE (move blocked — see warning)"
        if stale_count > 0:
            summary += f", {stale_count} missing files recorded for repair"
        if moved_count == 0 and blocked_count == 0 and stale_count == 0 and indexed_count == 0 \
                and index_failed == 0 and not (other["added"] or other["missing"] or other["unreachable"]):
            summary += " — library is clean"
        summary += _other_folders_words(other)

        logger.info(f"[SoulSync Deep Scan] {summary}")
        add_activity_item("", "SoulSync Deep Scan", summary, "Now")
        _db_update_finished_callback(0, 0, len(transfer_files),
                                     moved_count + stale_count + indexed_count, index_failed)

    except Exception as e:
        logger.error(f"[SoulSync Deep Scan] {e}")
        import traceback
        traceback.print_exc()
        _db_update_error_callback(f"Deep scan failed: {e}")


def _own_library_scan_clients(server_type):
    """(profile, client) for every profile with a library of its own (#1199):
    the media client connected as that profile, on that profile's server
    library. plex, jellyfin, and navidrome 0.58+ (one navidrome library per
    profile, read as their own navidrome user, #2031). an older navidrome
    has one library per server, so an own library there stays shared."""
    db = get_database()
    profiles = db.get_own_library_profiles()
    if not profiles:
        return []
    from core.library_scope import own_library_unsupported_reason
    _reason = own_library_unsupported_reason(server_type)
    if _reason:
        logger.info(
            "[Own Library] Active media server '%s' does not support per-profile libraries (%s); "
            "skipping own-library scans for %d profile(s)",
            server_type, _reason, len(profiles)
        )
        return []
    base = media_server_engine.client(server_type) if media_server_engine else None
    if base is None:
        return []
    out = []
    for prof in profiles:
        pid = prof['id']
        libs = db.get_profile_server_library(pid) or {}
        try:
            if server_type == 'jellyfin':
                library_id = libs.get('jellyfin_library_id')
                if not library_id:
                    logger.warning(f"[Own Library] profile {prof['name']} has no Jellyfin library picked; skipping its scan")
                    continue
                client = base.as_user(libs.get('jellyfin_user_id') or base.user_id, library_id)
            elif server_type == 'navidrome':
                client = _navidrome_own_library_client(db, base, prof, libs)
                if client is None:
                    continue
            else:
                library_name = libs.get('plex_library_id')
                if not library_name:
                    logger.warning(f"[Own Library] profile {prof['name']} has no Plex library picked; skipping its scan")
                    continue
                if not base.ensure_connection():
                    continue
                client = None
                link = db.get_profile_plex_home_user(pid)
                if link:
                    client = base.as_home_user(link['token'], link.get('title') or prof['name'])
                    if client is None:
                        logger.warning(f"[Own Library] could not connect to Plex as '{link.get('title')}' for {prof['name']}; "
                                       f"scanning their library with the app account")
                if client is None:
                    from core.plex_client import PlexUserView
                    client = PlexUserView(base, base.server, prof['name'])
                if not client.set_music_library_by_name(library_name):
                    logger.warning(f"[Own Library] Plex library '{library_name}' not found for {prof['name']}; skipping its scan")
                    continue
        except Exception as e:
            logger.error(f"[Own Library] could not build a client for profile {prof['name']}: {e}")
            continue
        out.append((prof, client))
    return out


def _navidrome_own_library_client(db, base, prof, libs):
    """the navidrome client for one own-library profile's scan (#2031): a
    view on its picked library (musicFolderId on every listing), as its own
    navidrome user when it saved a login, never a change to the shared
    client. None (and why, logged) when there is nothing safe to scan."""
    folder = libs.get('navidrome_library_id')
    if not folder:
        logger.warning(f"[Own Library] profile {prof['name']} has no Navidrome library picked; skipping its scan")
        return None
    if not base.ensure_connection():
        return None
    shared = str(getattr(base, 'music_folder_id', None) or '')
    if not shared:
        logger.warning(f"[Own Library] the shared Navidrome scan reads every library (no music folder picked "
                       f"in settings), so {prof['name']}'s library is in it too; pick the shared folder")
    elif shared == str(folder):
        logger.warning(f"[Own Library] {prof['name']}'s Navidrome library is the shared one; skipping its scan")
        return None
    login = db.get_profile_navidrome_login(prof['id'])
    if login:
        return base.as_user(login[0], login[1], music_folder_id=folder)
    logger.info(f"[Own Library] {prof['name']} has no Navidrome login of their own; "
                f"scanning their library with the app account")
    return base.as_user(None, None, music_folder_id=folder)


def _run_own_library_scans(server_type, deep):
    """scan every own-library profile's server library, rows stamped with the
    profile. runs as the final phase of the shared scan (its post-scan hook)
    so the status stays 'running' through it and one finished signal ends
    the whole thing. a failure in one profile's scan never stops the rest."""
    from core.library_scope import reset_library_scope, set_library_scope
    for prof, client in _own_library_scan_clients(server_type):
        _db_update_phase_callback(f"Scanning {prof['name']}'s library...")
        # the worker names its owner on every write and per-library read, so
        # the scope is belt and braces: anything it asks the db through the
        # caller's scope is answered from this profile's library, not the
        # shared one the scan thread runs as
        _scope_token = set_library_scope(prof['id'])
        try:
            worker = DatabaseUpdateWorker(
                media_client=client, full_refresh=False, server_type=server_type,
                force_sequential=True, owner_profile_id=prof['id'])
            worker.connect_callback('phase_changed', _db_update_phase_callback)
            # progress has to reach the shared state too. without it the
            # watchdog saw no ticks for the whole profile scan and called any
            # own library that took over 5 minutes stuck (#2036)
            worker.connect_callback(
                'progress_updated',
                lambda item, done, total, pct, _p=prof: _db_update_progress_callback(
                    f"{_p['name']}: {item}", done, total, pct))
            worker.connect_callback('artist_processed', _db_update_artist_callback)
            worker.connect_callback('error', lambda msg, _p=prof: logger.error(f"[Own Library] {_p['name']}: {msg}"))
            if deep:
                worker.run_deep_scan()
            else:
                worker.run()
            logger.info(f"[Own Library] scanned {prof['name']}'s library: {worker.processed_artists} artists, "
                        f"{worker.processed_tracks} new tracks")
        except Exception as e:
            logger.error(f"[Own Library] scan of {prof['name']}'s library failed: {e}")
        finally:
            reset_library_scope(_scope_token)


def _post_scan_hook_with_own_libraries(server_type, deep):
    def hook(worker):
        _run_own_library_scans(server_type, deep)
        if _reconcile_after_scan:
            _reconcile_after_scan(worker)
        # a file that moved to another row of its album leaves its old row
        # behind with no file, and only a scan sees that happen (#2094)
        if getattr(worker, 'database', None) is not None:
            _db_update_phase_callback("Finishing up: folding leftover track rows...")
            from core.library2.dedup_repair import fold_leftovers_after_scan
            fold_leftovers_after_scan(worker.database)
            # albums of one name a scan merged into one row (#2110)
            from core.library2.release_split import split_same_named_after_scan
            split_same_named_after_scan(worker.database)
    return hook


def _capture_task_epoch(run_epoch):
    """H16: record the epoch of the task now starting on the executor.

    run_epoch is the epoch assigned when the task was submitted. A task
    queued behind a hung worker must carry ITS run's epoch, not the epoch
    that happens to be current when it eventually starts — otherwise its
    terminal callback would commit over the successor run's state.
    """
    global _active_task_epoch
    with db_update_lock:
        _active_task_epoch = (
            run_epoch if run_epoch is not None
            else (db_update_state or {}).get("run_epoch", 0)
        )


def _scan_media_client(server_type):
    """The client a scan reads, by the server's registered name. a list of
    plex, jellyfin and navidrome here left music assistant out, so every
    scan of it failed with "not available" (#2111)."""
    if not server_type or media_server_engine is None:
        return None
    return media_server_engine.client(server_type)


def _run_db_update_task(full_refresh, server_type, run_epoch=None):
    """The actual function that runs in the background thread.

    run_epoch is the epoch assigned at submit time (see start_database_update
    / _run_with_progress): a task queued behind a hung worker must carry the
    epoch of ITS run, not the epoch that happens to be current when it
    eventually starts.
    """
    global db_update_worker, _active_task_epoch

    _capture_task_epoch(run_epoch)

    # SoulSync standalone
    if server_type == "soulsync":
        _run_soulsync_full_refresh(incremental=not full_refresh)
        return

    media_client = _scan_media_client(server_type)
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
    db_update_worker.post_scan_hook = _post_scan_hook_with_own_libraries(server_type, deep=False)

    # This is a blocking call that runs the worker logic
    db_update_worker.run()


def _run_deep_scan_task(server_type, run_epoch=None):
    """Run a deep library scan in the background thread.

    run_epoch is the epoch assigned at submit time (see _run_db_update_task).
    """
    global db_update_worker, _active_task_epoch

    _capture_task_epoch(run_epoch)

    if server_type == "soulsync":
        # SoulSync standalone deep scan: find untracked files → move to Staging,
        # remove stale DB records where files no longer exist on disk
        _run_soulsync_deep_scan()
        return

    media_client = _scan_media_client(server_type)
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
    db_update_worker.post_scan_hook = _post_scan_hook_with_own_libraries(server_type, deep=True)

    # Run deep scan instead of normal run()
    db_update_worker.run_deep_scan()


# what a member gets from /api/database/stats (#2207): how big their
# library is. the database's size and path, the server and the scan times
# are the admin's
MEMBER_DB_STATS_KEYS = ('artists', 'albums', 'tracks')


@bp.route('/api/database/stats', methods=['GET'])
@requires("music.access")
def get_database_stats():
    """Endpoint to get current database statistics."""
    try:
        # This endpoint returns the same stats shape the UI expects.
        db = get_database()
        # read in the caller's library scope, so a member's counts are the
        # library they browse
        stats = db.get_database_info_for_server()
        if not is_admin_request():
            stats = {k: stats.get(k, 0) for k in MEMBER_DB_STATS_KEYS}
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
            "unit": "artists",
            # H16: new run epoch — a stale worker from a watchdog-superseded
            # run must not overwrite this run's terminal state.
            "run_epoch": db_update_state.get("run_epoch", 0) + 1,
            # Seed the heartbeat now so a worker that hangs during init (before the
            # first progress/phase callback) is still caught by the stall watchdog.
            "last_progress_at": time.time(),
        })

        # Add activity for database update start
        server_name = active_server.capitalize()
        add_activity_item("", "Database Update", f"Starting {scan_type.lower()} update from {server_name}...", "Now")

        # Submit the appropriate worker
        if deep_scan:
            db_update_executor.submit(_run_deep_scan_task, active_server, db_update_state["run_epoch"])
        else:
            db_update_executor.submit(_run_db_update_task, full_refresh, active_server, db_update_state["run_epoch"])

    return jsonify({"success": True, "message": "Database update started."})

@bp.route('/api/database/update/status', methods=['GET'])
@admin_only
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
        from core.db_integrity import (DBIntegrityError, backup_lock, room_for, rotate_backups,
                                       safe_backup)
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        if not os.path.exists(db_path):
            return jsonify({"success": False, "error": "Database file not found"}), 404
        max_backups = 5
        # a backup is a second copy of the database: say so before filling the drive
        short = room_for(db_path, os.path.getsize(db_path))
        if short:
            return jsonify({"success": False, "error": short, "no_space": True}), 507
        if not backup_lock.acquire(blocking=False):
            return jsonify({"success": False, "busy": True,
                            "error": "Another backup or restore is running. Try again when it finishes."}), 409
        try:
            return _backup_music_now(db_path, max_backups, DBIntegrityError, safe_backup, rotate_backups)
        finally:
            backup_lock.release()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _backup_music_now(db_path, max_backups, DBIntegrityError, safe_backup, rotate_backups):
    """the backup itself, with the lock held"""
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
    # Rolling cleanup, counted in backups (not in the files beside them), and
    # never dropping the most-recent verified-healthy one.
    rotate_backups(db_path, max_backups)
    # the player's and the audiobooks' databases ride along (core/backup_companions.py)
    from core.backup_companions import backup_companions
    also = backup_companions(timestamp, max_backups)
    return jsonify({"success": True, "backup_path": backup_path, "size_mb": size_mb,
                    "version": SOULSYNC_VERSION, "also": also})

@bp.route('/api/database/backups', methods=['GET'])
@admin_only
def list_backups_endpoint():
    """List all database backups with metadata."""
    try:
        from core.db_integrity import list_backups
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        backup_files = sorted(list_backups(db_path), key=os.path.getmtime, reverse=True)
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
        from core.db_integrity import remove_backup
        remove_backup(backup_path)      # the backup and everything beside it
        return jsonify({"success": True, "deleted": filename})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

PRE_RESTORE_KEEP = 3


def _restore_schema_problem(db_path, backup_path, dbi):
    """a sentence when the backup's schema is newer than this database's
    (a backup made by a newer SoulSync), else None. can't tell is no problem."""
    theirs, ours = dbi.schema_version(backup_path), dbi.schema_version(db_path)
    if theirs is None or ours is None or theirs <= ours:
        return None
    return (f"That backup is from a newer SoulSync (database version {theirs}; this one runs "
            f"{ours}). Restoring it here would leave tables this version can't read. "
            "Update SoulSync first, then restore it. Nothing was changed.")


def _prune_pre_restore_copies(db_dir):
    """keep the newest few "what was there before a restore" copies"""
    try:
        names = sorted(n for n in os.listdir(db_dir or ".")
                       if re.match(r'^music_library\.db\.pre-restore_\d{8}_\d{6}$', n))
    except OSError:
        return
    from core.db_integrity import remove_backup
    for old in names[:-PRE_RESTORE_KEEP]:
        remove_backup(os.path.join(db_dir or ".", old))


def _forget_database_initialized(db_path):
    """make the next open of ``db_path`` run schema creation and migrations again"""
    try:
        from database import music_database as mdb
        with getattr(mdb, "_database_initialization_lock", _NO_LOCK):
            for key in list(mdb._database_initialized_paths):
                if os.path.abspath(str(key)) == os.path.abspath(db_path):
                    mdb._database_initialized_paths.discard(key)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not re-arm database init after restore: %s", e)


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


_NO_LOCK = _NoLock()


def _reload_config_after_restore():
    try:
        from core.settings import config_manager
        config_manager.load_config()
        logger.info("settings reloaded from the restored database")
    except Exception as e:  # noqa: BLE001
        logger.warning("could not reload settings after restore (restart to pick them up): %s", e)


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

        from core import db_integrity as dbi

        # 1. the backup itself, before anything is touched. nothing checked it:
        #    a backup that had gone bad on disk was written straight over the
        #    live database.
        if not dbi.is_healthy(backup_path):
            return jsonify({"success": False, "backup_corrupt": True,
                            "error": "That backup failed its integrity check, so it was not restored. "
                                     "Your database is untouched."}), 409
        # 2. a database from a NEWER SoulSync: this version doesn't know its
        #    tables, and nothing would migrate it back
        problem = _restore_schema_problem(db_path, backup_path, dbi)
        if problem:
            return jsonify({"success": False, "newer_schema": True, "error": problem}), 409
        # 3. room for the copy of the current database that is kept first
        short = dbi.room_for(db_path, os.path.getsize(db_path) if os.path.exists(db_path) else 0)
        if short:
            return jsonify({"success": False, "no_space": True,
                            "error": short + " Nothing was changed."}), 507
        if not dbi.backup_lock.acquire(blocking=False):
            return jsonify({"success": False, "busy": True,
                            "error": "Another backup or restore is running. Try again when it finishes."}), 409
        try:
            # 4. keep what is there now, as a verified copy OUTSIDE the rolling
            #    backups: it used to be one of them, so the next backup could
            #    rotate away the only way back. if it can't be made, stop here.
            safety_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            safety_filename = f"music_library.db.pre-restore_{safety_ts}"
            safety_path = os.path.join(db_dir, safety_filename)
            try:
                dbi.safe_backup(db_path, safety_path, verify_source=False)
            except Exception as e:  # noqa: BLE001 - no way back, no restore
                logger.error("restore stopped: the safety copy could not be made: %s", e)
                return jsonify({"success": False,
                                "error": "Could not keep a copy of the current database first, so "
                                         f"nothing was restored. ({e})"}), 500
            _prune_pre_restore_copies(db_dir)

            # Restore using SQLite backup API (handles concurrent access safely)
            from database.music_database import close_database, get_database
            close_database()

            src_restore = sqlite3.connect(backup_path)
            dst_restore = sqlite3.connect(db_path)
            try:
                src_restore.backup(dst_restore)
            finally:
                dst_restore.close()
                src_restore.close()
            # 5. the restored file is whatever schema the backup had. opening it
            #    must run the migrations again; they are remembered as done for
            #    this path, so an older backup ran on unmigrated tables until the
            #    next restart ("no such table").
            _forget_database_initialized(db_path)
        finally:
            dbi.backup_lock.release()
        # 6. the settings live in this database. the ones in memory are the old
        #    database's, and the next settings save would write them over the
        #    restored ones.
        _reload_config_after_restore()

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

# ── a damaged database: its health, and the repair ─────────────────────────

_health_running = threading.Lock()


def _db_path():
    return os.environ.get('DATABASE_PATH', 'database/music_library.db')


def _check_health_in_background():
    """a full check of a large file takes a while: it runs off the request"""
    from core import db_repair
    if not _health_running.acquire(blocking=False):
        return

    def run():
        try:
            db_repair.check(_db_path())
        except Exception:  # noqa: BLE001
            logger.exception("database health check failed to run")
        finally:
            _health_running.release()
    threading.Thread(target=run, name="db-health-check", daemon=True).start()


@bp.route('/api/database/health', methods=['GET'])
@admin_only
def database_health():
    """{healthy: true|false|null, detail, checked_at, checking}. the answer the
    last backup or check gave; ?check=1 (or none known yet, or one over six
    hours old) starts a fresh one in the background"""
    from core import db_repair
    health = db_repair.last_health()
    stale = health["checked_at"] is None or time.time() - health["checked_at"] > 6 * 3600
    if request.args.get('check') == '1' or stale:
        _check_health_in_background()
    return jsonify({"success": True, **health, "checking": _health_running.locked(),
                    "repair_tool": bool(db_repair.sqlite_cli())})


@bp.route('/api/database/repair', methods=['POST'])
@admin_only
def repair_database():
    """repair a damaged database: the damaged files are kept first (raw, in
    their own folder), a repaired copy is made and checked, and only then
    swapped in, the way a restore is. when it can't be repaired the answer
    names the newest healthy backup to restore instead."""
    from core import db_integrity as dbi
    from core import db_repair
    db_path = _db_path()
    db_dir = os.path.dirname(db_path)
    health = db_repair.check(db_path)
    if health["healthy"]:
        return jsonify({"success": True, "healthy": True,
                        "message": "Your database passed its check. There is nothing to repair."})
    if not dbi.backup_lock.acquire(blocking=False):
        return jsonify({"success": False, "busy": True,
                        "error": "A backup or restore is running. Try again when it finishes."}), 409
    try:
        try:
            kept = db_repair.keep_damaged(db_path)
        except OSError as e:
            return jsonify({"success": False,
                            "error": f"Couldn't keep a copy of the damaged files first, so nothing was "
                                     f"changed. {e}"}), 507
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        repaired = os.path.join(db_dir, f"music_library.db.repaired_{stamp}")
        result = db_repair.rebuild(db_path, repaired)
        if not result["ok"]:
            backup = db_repair.newest_healthy_backup(db_path)
            logger.warning("database repair failed: %s", result["detail"])
            return jsonify({"success": False, "repaired": False, "kept": os.path.basename(kept),
                            "error": result["detail"], "missing_tables": result["missing_tables"],
                            "backup": backup}), 422
        # swapped in the way a restore is: every connection closed, the file
        # replaced, the old -wal and -shm (part of the damage) gone, the
        # migrations and settings read again
        from database.music_database import close_database, get_database
        close_database()
        os.replace(repaired, db_path)
        for side in ('-wal', '-shm'):
            try:
                os.remove(db_path + side)
            except OSError:
                pass
        _forget_database_initialized(db_path)
    finally:
        dbi.backup_lock.release()
    _reload_config_after_restore()
    get_database()
    after = db_repair.check(db_path)
    logger.info("database repaired (%s); damaged files kept in %s", result["method"], kept)
    return jsonify({"success": True, "repaired": True, "method": result["method"],
                    "message": result["detail"], "kept": os.path.basename(kept),
                    "healthy": after["healthy"]})


@bp.route('/api/database/companions', methods=['GET'])
@admin_only
def list_companion_backups_endpoint():
    """The databases that ride along with a music backup (player, audiobooks),
    each with its backups, and whether the encryption key file is there."""
    try:
        from core.backup_companions import companions_overview, key_file_path
        return jsonify({"success": True, "companions": companions_overview(),
                        "key_file": os.path.isfile(key_file_path())})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/database/companions/<name>/restore', methods=['POST'])
@admin_only
def restore_companion_endpoint(name):
    """Restore the player or audiobook database from one of its backups."""
    from core.backup_companions import CompanionRestoreError, restore_companion
    body = request.get_json(silent=True) or {}
    try:
        result = restore_companion(name, body.get('filename'))
    except CompanionRestoreError as e:
        return jsonify({"success": False, "error": str(e)}), e.status
    except Exception as e:
        logger.exception("companion restore failed")
        return jsonify({"success": False, "error": str(e)}), 500
    _forget_player_caches()
    return jsonify({"success": True, **result})


def _forget_player_caches():
    """what is held in memory about the player database is the old one's"""
    try:
        from core.player import libraries as _pl, settings as _ps
        with _pl._lock:
            _pl._hidden_cache.clear()
        with _ps._lock:
            _ps._cache.clear()
    except Exception as e:  # noqa: BLE001
        logger.debug("player caches not cleared after restore: %s", e)


@bp.route('/api/database/encryption-key/download', methods=['GET'])
@admin_only
def download_encryption_key_endpoint():
    """The key every saved credential is encrypted with. A backup restored on
    another machine needs it, or the logins and api keys saved in Settings are
    unreadable there."""
    from core.backup_companions import key_file_path
    path = key_file_path()
    if not os.path.isfile(path):
        return jsonify({"success": False, "error": "There is no key file yet."}), 404
    response = send_file(os.path.abspath(path), as_attachment=True, download_name="soulsync.encryption_key")
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route('/api/database/backups/<filename>/download', methods=['GET'])
@admin_only
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
@admin_only
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

        from core import db_housekeeping
        from database.music_database import DB_CACHE_MB, DB_JOURNAL_SIZE_LIMIT_MB, DB_MMAP_MB
        wal_bytes = db_housekeeping.wal_size(db_path)
        try:
            free_disk = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path))).free
        except OSError:
            free_disk = None

        return jsonify({
            'success': True,
            'wal_size': wal_bytes,
            'wal_size_display': f'{wal_bytes / 1024 / 1024:.1f} MB',
            'free_disk': free_disk,
            # VACUUM writes a whole new copy before it swaps it in
            'vacuum_needs': total_bytes,
            'vacuum_has_room': free_disk is None or free_disk > total_bytes * 1.1,
            'tuning': {'cache_mb': DB_CACHE_MB, 'wal_limit_mb': DB_JOURNAL_SIZE_LIMIT_MB,
                       'mmap_mb': DB_MMAP_MB,
                       'checkpoint_minutes': db_housekeeping.CHECKPOINT_MINUTES},
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


def _rebuild_fts_after_vacuum(conn):
    """albums_fts is an external-content index keyed by albums.rowid, and
    VACUUM may renumber the rowids of a table without an INTEGER PRIMARY KEY.
    a rebuild puts the index back in step; skipped when there is no index."""
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'albums_fts'").fetchone():
            conn.execute("INSERT INTO albums_fts(albums_fts) VALUES ('rebuild')")
            conn.commit()
            logger.info("Rebuilt albums_fts after VACUUM")
    except Exception as e:
        logger.warning(f"albums_fts rebuild after VACUUM failed: {e}")


@bp.route('/api/database/maintenance/vacuum', methods=['POST'])
@admin_only
def database_vacuum():
    """Run VACUUM to compact the database. Locks DB during operation."""
    try:
        import sqlite3, time
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')

        # Get size before
        size_before = os.path.getsize(db_path)

        # VACUUM builds a full copy of the database before it replaces the
        # original: without room for it, it fails part way after a long wait
        body = request.get_json(silent=True) or {}
        try:
            free_disk = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path))).free
        except OSError:
            free_disk = None
        if free_disk is not None and free_disk < size_before * 1.1 and not body.get('force'):
            return jsonify({
                'success': False,
                'error': (f'not enough free disk space to compact: VACUUM needs about '
                          f'{size_before / 1024 / 1024 / 1024:.1f} GB free next to the database, '
                          f'there is {free_disk / 1024 / 1024 / 1024:.1f} GB'),
            }), 409

        conn = sqlite3.connect(db_path)
        start = time.time()
        conn.execute('VACUUM')
        elapsed = time.time() - start
        _rebuild_fts_after_vacuum(conn)
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
@admin_only
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
        _rebuild_fts_after_vacuum(conn)
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


@bp.route('/api/database/maintenance/tables', methods=['GET'])
@admin_only
def database_table_sizes():
    """what is taking the room (#2205). sizes come from a measuring pass that
    reads every page, so it runs on its own thread: ``?measure=1`` starts it,
    and this returns the last result plus the planner's row estimates now."""
    try:
        from core import db_housekeeping
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        started = False
        if request.args.get('measure') in ('1', 'true', 'yes'):
            started = db_housekeeping.start_measure(db_path)
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            # row estimates only: dbstat here would read the whole file inline
            estimates = db_housekeeping.table_sizes(conn, use_dbstat=False)
        finally:
            conn.close()
        state = db_housekeeping.measure_state()
        return jsonify({'success': True, 'started': started, 'measure': state,
                        'estimates': estimates.get('tables', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/database/maintenance/optimize', methods=['POST'])
@admin_only
def database_optimize():
    """the safe tidy-up, no VACUUM: prune the caches and logs that keep their
    own retention, merge the search indexes, refresh planner stats, and fold
    the wal back in. it never deletes listening history or library rows."""
    try:
        from core import db_housekeeping
        db_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        start = time.time()
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.execute('PRAGMA busy_timeout = 30000')
            pruned = db_housekeeping.prune(conn)
            optimized = db_housekeeping.optimize(conn)
        finally:
            conn.close()
        wal = db_housekeeping.checkpoint(db_path, truncate_over=0)
        elapsed = time.time() - start
        logger.info("database optimize: pruned %s, fts %s, wal %.1f -> %.1f MB in %.1fs",
                    pruned, optimized.get('fts_optimized'), wal['wal_before'] / 1e6,
                    wal['wal_after'] / 1e6, elapsed)
        return jsonify({'success': True, 'pruned': pruned, 'elapsed_seconds': round(elapsed, 1),
                        'fts_optimized': optimized.get('fts_optimized', []),
                        'wal_before': wal['wal_before'], 'wal_after': wal['wal_after'],
                        'wal_busy': bool(wal.get('busy'))})
    except Exception as e:
        logger.error(f"Database optimize failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
