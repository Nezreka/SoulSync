"""Targeted Navidrome reconciliation after automatic album completion.

Only batches marked as album completion schedule this worker. The caller provides
paths for the completed downloads, and no existing tracks are deleted.
"""
import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)
_PENDING = set()
_PENDING_LOCK = threading.Lock()


def _expected_track_numbers(imported_paths):
    """Parse conventional NN - Title filenames, without guessing track numbers."""
    numbers = set()
    for path in imported_paths:
        name = os.path.basename(os.fspath(path))
        match = re.match(r"^\s*(\d{1,3})\s*[-._ ]\s*\S", name)
        if not match:
            return set()
        number = int(match.group(1))
        if not 1 <= number <= 999:
            return set()
        numbers.add(number)
    return numbers


def schedule(album_id, imported_paths, scan_manager, media_engine):
    """Start one reconciliation thread per album; leave normal downloads alone."""
    album_id = str(album_id or '').strip()
    paths = tuple(os.fspath(p) for p in imported_paths or ())
    if not album_id or not paths or not scan_manager or not media_engine:
        return False
    with _PENDING_LOCK:
        if album_id in _PENDING:
            return False
        _PENDING.add(album_id)

    def run():
        try:
            _reconcile(album_id, paths, scan_manager, media_engine)
        except Exception:
            logger.exception('[Album Completion] Reconciliation failed: %s', album_id)
        finally:
            with _PENDING_LOCK:
                _PENDING.discard(album_id)

    try:
        threading.Thread(target=run, name='album-completion-reconcile', daemon=True).start()
    except Exception:
        with _PENDING_LOCK:
            _PENDING.discard(album_id)
        raise
    return True


def _read_album(database, album_id):
    with database._get_connection() as conn:
        return conn.execute('''
            SELECT a.title, a.artist_id, ar.name AS artist_name
            FROM albums a JOIN artists ar ON ar.id = a.artist_id
            WHERE a.id = ? AND a.server_source = 'navidrome'
              AND ar.server_source = 'navidrome'
        ''', (album_id,)).fetchone()


def _verified(database, album_id, numbers):
    if not numbers:
        return False
    with database._get_connection() as conn:
        rows = conn.execute('''
            SELECT track_number FROM tracks
            WHERE album_id = ? AND track_number IS NOT NULL
        ''', (album_id,)).fetchall()
    actual = {int(row['track_number']) for row in rows}
    return numbers.issubset(actual)


def _reconcile(album_id, imported_paths, scan_manager, media_engine):
    from database.music_database import get_database
    from core.database_update_worker import DatabaseUpdateWorker

    database = get_database()  # Thread-local: acquire inside worker thread.
    album = _read_album(database, album_id)
    if not album:
        logger.warning('[Album Completion] Navidrome album not found: %s', album_id)
        return False

    expected_numbers = _expected_track_numbers(imported_paths)
    if not expected_numbers:
        logger.warning('[Album Completion] Cannot verify track numbers for %s; '
                       'will refresh but not claim verified completion', album_id)

    client = media_engine.client('navidrome')
    if not client:
        logger.warning('[Album Completion] Navidrome client unavailable')
        return False

    # A queued scan can follow a scan-complete callback from an earlier scan.
    # Refresh/verify after each callback, retrying if the target tracks are absent.
    for attempt in range(1, 4):
        logger.info("[Album Completion] Scan attempt %d/3: album=%s", attempt, album_id)
        finished = threading.Event()
        def on_complete():
            finished.set()
        scan_manager.add_scan_completion_callback(on_complete)
        try:
            result = scan_manager.request_scan('Album completion reconciliation: ' + album_id)
            if isinstance(result, dict) and result.get('status') == 'ignored':
                logger.warning('[Album Completion] Scan ignored: %s', album_id)
                return False
            if not finished.wait(timeout=900):
                logger.warning('[Album Completion] Scan timeout: %s attempt=%d', album_id, attempt)
                continue
        finally:
            scan_manager.remove_scan_completion_callback(on_complete)

        time.sleep(2)
        artist = client.get_artist_by_id(str(album['artist_id']))
        if not artist:
            logger.warning('[Album Completion] Artist unavailable: %s', album['artist_name'])
            continue
        worker = DatabaseUpdateWorker(media_client=client, full_refresh=False,
                                      server_type='navidrome', force_sequential=True)
        worker.database = database
        success, details, new_albums, new_tracks = worker._process_artist_with_content(
            artist, skip_existing_tracks=True, seen_track_ids=set())
        logger.info('[Album Completion] %s attempt=%d success=%s new_tracks=%s details=%s',
                    album['artist_name'], attempt, success, new_tracks, details)
        if success and _verified(database, album_id, expected_numbers):
            logger.info('[Album Completion] Verified imported track numbers %s for %s',
                        sorted(expected_numbers), album['title'])
            return True
    logger.warning('[Album Completion] Target tracks not verified after retries: %s', album_id)
    return False
