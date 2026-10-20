"""Registry and orchestrator for post-download playlist synchronization.

When playlist-grouped wishlist downloads or organize-by-playlist downloads finish,
they register their completed playlist provenance here. Once the media server scan
and the SoulSync database update complete, the pending playlists are atomically
drained and synced so the newly downloaded and indexed tracks are immediately
written to the media server playlist.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Thread-safe in-memory registry of playlists pending sync after downloads.
# Key: (mirrored_playlist_id, profile_id) -> metadata dict
_lock = threading.Lock()
_pending_playlists: Dict[Tuple[int, int], Dict[str, Any]] = {}


def register_completed_playlist_batch(
    batch: Dict[str, Any],
    tasks_snapshot: Optional[Dict[str, Any]] = None,
    database: Any = None,
) -> List[int]:
    """Register any playlists whose tracks were successfully downloaded in this batch.

    Extracts playlist provenance from:
    1. Direct ``batch['mirrored_playlist_id']`` (organize_download or wishlist batch).
    2. Batch-level ``is_playlist_batch`` / ``source_playlist_name`` / ``source_playlist_id``.
    3. Task-level ``track_info['source_info']`` for completed tasks (e.g. album bundles
       whose tracks originated from a playlist sync).

    Resolves to mirrored_playlists PK and registers in the pending queue for
    post-DB-update sync.

    Returns the list of newly registered or existing mirrored_playlist_ids.
    """
    registered_ids: List[int] = []
    # a playlist batch's tracks all carry the same provenance, so each ref is
    # looked up once, not once per track
    seen: set = set()

    def _resolve_and_record(ref: Any, name: Optional[str], profile_id: int):
        seen_key = (str(ref), str(name), int(profile_id))
        if seen_key in seen:
            return
        seen.add(seen_key)
        target_pk: Optional[int] = None
        target_name: str = str(name or "Playlist").strip()

        # only a real int is a mirrored_playlists pk. a digit string is an
        # upstream id (deezer ids are all digits), so it goes through resolve
        if isinstance(ref, int) and not isinstance(ref, bool) and ref > 0:
            target_pk = ref

        db = database
        if db is None:
            try:
                from database.music_database import MusicDatabase
                db = MusicDatabase()
            except Exception as db_err:
                logger.debug("[Post-Download Sync] Could not instantiate MusicDatabase: %s", db_err)

        if target_pk is None and db is not None:
            try:
                resolved = db.resolve_mirrored_playlist_assignment(
                    playlist_ref=ref,
                    playlist_name=name,
                    profile_id=profile_id,
                )
                if resolved and resolved.get('id'):
                    target_pk = int(resolved['id'])
                    target_name = resolved.get('name') or target_name
            except Exception as res_err:
                logger.debug("[Post-Download Sync] Could not resolve playlist %s: %s", ref, res_err)

        if target_pk is not None and target_pk > 0:
            key = (target_pk, int(profile_id))
            with _lock:
                _pending_playlists[key] = {
                    'mirrored_playlist_id': target_pk,
                    'playlist_name': target_name,
                    'profile_id': int(profile_id),
                    'registered_at': time.time(),
                }
            if target_pk not in registered_ids:
                registered_ids.append(target_pk)
            logger.info(
                "[Post-Download Sync] Registered playlist '%s' (id=%d, profile=%d) for post-download sync",
                target_name,
                target_pk,
                profile_id,
            )

    # 1. Direct mirrored_playlist_id on the batch
    batch_mirrored_id = batch.get('mirrored_playlist_id')
    batch_profile_id = int(batch.get('profile_id') or 1)
    batch_name = batch.get('source_playlist_name') or batch.get('playlist_name')

    if batch_mirrored_id:
        _resolve_and_record(batch_mirrored_id, batch_name, batch_profile_id)

    # 2. Batch-level is_playlist_batch / organize_by_playlist
    if batch.get('is_playlist_batch') or batch.get('organize_by_playlist'):
        batch_source_ref = batch.get('source_playlist_id') or batch.get('playlist_id')
        if batch_source_ref and batch_source_ref not in ('wishlist', 'wishlist_manual'):
            _resolve_and_record(batch_source_ref, batch_name, batch_profile_id)

    # 3. Tasks snapshot: check completed tracks for playlist origin
    if tasks_snapshot:
        for task in tasks_snapshot.values():
            if not isinstance(task, dict) or task.get('status') != 'completed':
                continue
            ti = task.get('track_info') or {}
            source_type = ti.get('source_type')
            source_info = ti.get('source_info') or {}
            if source_type == 'playlist' or (
                isinstance(source_info, dict)
                and (source_info.get('playlist_id') or source_info.get('playlist_name'))
            ):
                pl_ref = source_info.get('playlist_id') if isinstance(source_info, dict) else None
                pl_name = source_info.get('playlist_name') if isinstance(source_info, dict) else None
                task_profile = int(ti.get('profile_id') or batch_profile_id)
                if pl_ref or pl_name:
                    _resolve_and_record(pl_ref, pl_name, task_profile)

    return registered_ids


def drain_pending_playlist_syncs(max_age_seconds: float = 86400.0) -> List[Dict[str, Any]]:
    """Atomically drain all pending playlist syncs within the TTL."""
    now = time.time()
    with _lock:
        valid_items = []
        for item in list(_pending_playlists.values()):
            if now - item.get('registered_at', now) <= max_age_seconds:
                valid_items.append(item)
        _pending_playlists.clear()
        return valid_items


def get_pending_playlist_syncs_count() -> int:
    """Return the number of currently pending playlist syncs."""
    with _lock:
        return len(_pending_playlists)


def clear_pending_playlist_syncs() -> None:
    """Clear all pending playlist syncs (primarily for tests)."""
    with _lock:
        _pending_playlists.clear()


def sync_pending_downloaded_playlists(deps: Any, auto_id: Optional[str] = None) -> Dict[str, Any]:
    """Drain all pending playlist syncs and trigger their auto-sync.

    Enforces ``skip_wishlist_add=True`` to guarantee no infinite download/wishlist
    loops, and ``force_sync=True`` to ensure the sync matches newly indexed media server
    tracks even if upstream track IDs haven't changed.
    """
    pending = drain_pending_playlist_syncs()
    if not pending:
        logger.info("[Post-Download Sync] No downloaded playlists pending sync — skipping")
        return {
            'status': 'skipped',
            'reason': 'No downloaded playlists pending sync',
        }

    logger.info(
        "[Post-Download Sync] Drained %d pending playlist sync(s): %s",
        len(pending),
        [p.get('playlist_name') for p in pending],
    )
    if auto_id and getattr(deps, 'update_progress', None):
        deps.update_progress(
            auto_id,
            progress=20,
            phase="Syncing downloaded playlists",
            log_line=f"Starting sync for {len(pending)} playlist(s)",
            log_type="info",
        )

    started_playlists: List[str] = []
    errors: List[str] = []

    for item in pending:
        mirrored_id = item.get('mirrored_playlist_id')
        name = item.get('playlist_name') or f"Playlist {mirrored_id}"
        config = {
            'playlist_id': str(mirrored_id),
            'skip_wishlist_add': True,
            'force_sync': True,
            '_automation_id': auto_id if len(pending) == 1 else None,
        }
        try:
            from core.automation.handlers.sync_playlist import auto_sync_playlist
            res = auto_sync_playlist(config, deps)
            if res.get('status') == 'started':
                started_playlists.append(name)
                logger.info(
                    "[Post-Download Sync] Started auto-sync for playlist '%s' (id=%s) [skip_wishlist_add=True]",
                    name,
                    mirrored_id,
                )
            else:
                logger.info(
                    "[Post-Download Sync] Playlist '%s' (id=%s) sync outcome: %s",
                    name,
                    mirrored_id,
                    res,
                )
        except Exception as exc:
            logger.error(
                "[Post-Download Sync] Failed to sync playlist '%s' (id=%s): %s",
                name,
                mirrored_id,
                exc,
            )
            errors.append(f"{name}: {exc}")

    if auto_id and getattr(deps, 'update_progress', None):
        summary = f"Triggered sync for {len(started_playlists)} playlist(s)"
        if errors:
            summary += f" ({len(errors)} failed)"
        deps.update_progress(
            auto_id,
            progress=100,
            phase="Complete",
            log_line=summary,
            log_type="success" if not errors else "warning",
        )

    return {
        'status': 'started' if started_playlists else ('error' if errors else 'skipped'),
        'synced_playlists': started_playlists,
        'errors': errors,
        '_manages_own_progress': True,
    }
