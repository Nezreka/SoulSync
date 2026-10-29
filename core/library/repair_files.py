"""Shared file resolution, identity and quarantine boundaries for repair jobs."""
import os
import shutil

from core.library.path_resolver import resolve_library_file_path
from utils.logging_config import get_logger

logger = get_logger('repair_files')


def _same_reviewed_file(current_path: str, reviewed_path: str) -> bool:
    """Accept alternate library mounts only when they identify the same inode.

    Reconciliation scans /app/Music while library rows may use /music. A
    normalized string comparison rejects valid repairs on that deployment.
    Never treat merely similar path strings as proof of file identity.
    """
    try:
        return os.path.samefile(current_path, reviewed_path)
    except (OSError, ValueError):
        return False



def _server_playlist_membership():
    """{track_id: [playlist titles]} on the active media server (cached)."""
    from core.library import playlist_membership
    return playlist_membership.server_playlist_membership()



def _resolve_file_path(file_path, transfer_folder, download_folder=None,
                       config_manager=None, plex_client=None):
    """Resolve a stored DB path to an actual file on disk.

    Thin wrapper around ``core.library.path_resolver.resolve_library_file_path``
    that preserves the legacy signature used by every caller in this module
    and the repair-job modules. The shared resolver also probes the
    user-configured ``library.music_paths`` and Plex-reported library
    locations — which is what fixes the Album Completeness Auto-Fill
    failure on Docker setups (issue #476). Pre-existing call sites that
    don't pass ``config_manager`` keep the old transfer+download-only
    behavior; sites that pass it in pick up the wider search automatically.
    """
    return resolve_library_file_path(
        file_path,
        transfer_folder=transfer_folder,
        download_folder=download_folder,
        config_manager=config_manager,
        plex_client=plex_client,
    )



def _path_mapping_hint(config_manager) -> str:
    try:
        active = config_manager.get_active_media_server()
    except Exception:
        active = None
    if str(active or '').lower() == 'navidrome':
        return (
            'Navidrome may be reporting virtual paths. Enable "Report Real Path" '
            'for the SoulSync player, then run a full database refresh.'
        )
    return 'Check Settings -> Library -> Music Paths so SoulSync can map this path.'



def _quarantine_file_if_present(file_path, transfer_folder, source,
                                config_manager=None, download_folder=None):
    """Move a library file into the deleted-files quarantine instead of deleting it.

    Returns ``(moved, note, dest)``. The file keeps its path relative to the
    transfer folder (or its name, for files outside it) and is recorded in the
    quarantine manifest, so the deleted-files manager can restore it and the
    library.deleted_keep_days retention ages it out like any other removal.
    """
    if not file_path:
        return False, None, None
    resolved = _resolve_file_path(
        file_path, transfer_folder,
        download_folder=download_folder,
        config_manager=config_manager)
    if resolved is None:
        return False, f'file could not be located ({_path_mapping_hint(config_manager)})', None
    if not os.path.exists(resolved):
        return False, 'file was already gone', None
    from core.library.deleted_quarantine import record_deleted_entry
    from core.repair_jobs.base import deleted_quarantine_root
    deleted_root = deleted_quarantine_root(transfer_folder)
    try:
        rel = os.path.relpath(resolved, transfer_folder)
    except ValueError:
        rel = os.path.basename(resolved)
    if rel.startswith('..') or os.path.isabs(rel):
        rel = os.path.basename(resolved)
    dest = os.path.join(deleted_root, rel)
    base, ext = os.path.splitext(dest)
    n = 1
    while os.path.exists(dest):
        dest = f"{base}_{n}{ext}"
        n += 1
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(resolved, dest)
    except OSError as e:
        logger.warning("Could not move %s to the deleted folder: %s", resolved, e)
        return False, str(e), None
    record_deleted_entry(deleted_root, dest, resolved, source)
    return True, None, dest

