"""Hand downloads to Lidarr (``lidarr_download.import_through_lidarr``).

With the setting on, Lidarr is the only writer of the music library: SoulSync
imports every download into ``lidarr_download.transfer_path`` (see
``core.imports.paths.lidarr_root_for_context``), a folder Lidarr reads at the
same path and that must lie outside Lidarr's root folder (Lidarr adopts any
file it finds in there, whatever its own import decided). Then Lidarr imports
the files into its root folder through Manual Import: Lidarr names them and
keeps one copy of each track, so a file that is no upgrade of the track it
has is rejected, never added next to it.

The transfer folder is a queue, not a library:

- ``hand_batch_to_lidarr`` runs when a batch finishes, after the album
  consistency pass (Lidarr identifies a file by the album tags that pass makes
  agree).
- Every file Lidarr has verifiably imported is followed: SoulSync's database
  rows are repointed to Lidarr's path, and the copy in the transfer folder is
  deleted with its lyrics sidecars. Left pointing at a deleted copy, SoulSync
  would take the track for gone and fetch it again.
- ``sweep_lidarr_imports`` hands Lidarr whatever is still waiting there: files
  of a batch that never finished (a restart) or that Lidarr turned down for a
  reason that may pass (an album it did not know yet). It runs at startup and
  after every batch handoff. A file Lidarr keeps rejecting stays, logged with
  Lidarr's reason, for a person to look at.

Tracks that came from Lidarr are never handed back: Lidarr has them.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("downloads.lidarr_handoff")

# A file younger than this may belong to a batch that is still running (its
# handoff comes when the batch ends), so the sweep leaves it alone.
SWEEP_MIN_AGE_SECONDS = 6 * 3600

_AUDIO_EXTS = frozenset(('.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav',
                         '.wma', '.aiff', '.aif', '.ape', '.wv', '.alac'))
_SIDECAR_EXTS = ('.lrc', '.txt')

_sweep_lock = threading.Lock()


def _name(value) -> str:
    if isinstance(value, dict):
        value = value.get('name')
    return str(value or '').strip()


def _track_album_artist(track_info: dict) -> Tuple[str, str]:
    album = track_info.get('album')
    album_artists = album.get('artists') if isinstance(album, dict) else None
    artists = album_artists or track_info.get('artists') or []
    artist = _name(artists[0]) if isinstance(artists, list) and artists else _name(artists)
    return _name(album), artist


def _under(path: str, root: str) -> bool:
    return os.path.normpath(path).startswith(os.path.normpath(root) + os.sep)


def files_for_lidarr(batch: dict, tasks_snapshot: dict, transfer_root: str
                     ) -> Dict[Tuple[str, str], List[Tuple[str, dict]]]:
    """This batch's imported files Lidarr does not have yet, grouped by
    (album, artist): [(path, track_info)]. An album batch is one group under
    its album context; any other batch groups by each track's own album."""
    groups: Dict[Tuple[str, str], List[Tuple[str, dict]]] = {}
    if (batch.get('album_bundle_source') or '').lower() == 'lidarr':
        return groups
    is_album = bool(batch.get('is_album_download'))
    batch_album = _name(batch.get('album_context'))
    batch_artist = _name(batch.get('artist_context'))
    for task in (tasks_snapshot or {}).values():
        if not isinstance(task, dict) or task.get('status') != 'completed':
            continue
        if str(task.get('username') or '').lower() == 'lidarr':
            continue
        path = task.get('final_file_path')
        if not path or not os.path.isfile(path) or not _under(path, transfer_root):
            continue
        track_info = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
        if is_album and batch_album and batch_artist:
            key = (batch_album, batch_artist)
        else:
            key = _track_album_artist(track_info)
        if not all(key):
            # the sweep still offers it to Lidarr, which reads the tags itself
            logger.info("[Lidarr Handoff] %s has no album or artist to look up; "
                        "left to the sweep", os.path.basename(path))
            continue
        groups.setdefault(key, []).append((path, track_info))
    return groups


def finish_imported(imported: Dict[str, str], transfer_root: str, *,
                    repoint: Optional[Callable[[str, str], Any]] = None) -> int:
    """Follow the files Lidarr imported: repoint SoulSync's rows from the
    transfer copy to Lidarr's path, then delete the copy and its lyrics
    sidecars, and the album folder once it is empty. A copy whose rows could
    not be repointed is kept (the rows still name it). Returns copies removed."""
    if not imported:
        return 0
    if repoint is None:
        from core.downloads.atomic_recovery import make_db_path_updater
        from database.music_database import get_database
        repoint = make_db_path_updater(get_database(), count_is_evidence=False)
    removed = 0
    folders = set()
    for source, final in imported.items():
        if not _under(source, transfer_root):
            continue  # never delete outside the transfer folder
        try:
            repoint(source, final)
        except Exception as exc:  # noqa: BLE001 - keep the copy the rows still name
            logger.warning("[Lidarr Handoff] could not repoint %s to %s, keeping it: %s",
                           source, final, exc)
            continue
        stem = os.path.splitext(source)[0]
        for path in (source, *(stem + ext for ext in _SIDECAR_EXTS)):
            try:
                os.remove(path)
                if path == source:
                    removed += 1
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("[Lidarr Handoff] could not delete %s: %s", path, exc)
        folders.add(os.path.dirname(source))
    for folder in folders:
        _prune_empty(folder, transfer_root)
    return removed


def _prune_empty(folder: str, transfer_root: str) -> None:
    """Remove ``folder`` and its empty parents up to the transfer root, when
    nothing but leftover cover art is in them."""
    root = os.path.normpath(transfer_root)
    current = os.path.normpath(folder)
    while _under(current, root):
        try:
            entries = os.listdir(current)
        except OSError:
            return
        if any(not e.lower().startswith(('cover.', 'folder.')) for e in entries):
            return
        for entry in entries:
            try:
                os.remove(os.path.join(current, entry))
            except OSError:
                return
        try:
            os.rmdir(current)
        except OSError:
            return
        current = os.path.dirname(current)


def _tagged(paths: List[str], key: str) -> Optional[str]:
    """The value of tag ``key`` the files agree on, or None."""
    from core.library.file_tags import read_embedded_tags
    values = set()
    for path in paths:
        read = read_embedded_tags(path)
        value = str(((read.get('tags') or {}) if read.get('available') else {})
                    .get(key) or '').strip()
        if value:
            values.add(value)
    return values.pop() if len(values) == 1 else None


def tagged_release_group(paths: List[str]) -> Optional[str]:
    """The MusicBrainz release group the files' tags agree on, or None.

    A track picked from Deezer, iTunes or Spotify carries no MusicBrainz id,
    and Lidarr's text search can miss an album its MusicBrainz id finds at
    once (a new release, a common title). The album consistency pass has just
    tagged every file with the release it settled on, so the tags name it.
    """
    return _tagged(paths, 'musicbrainz_releasegroupid')


def _log_outcome(label: str, outcome: dict, total: int) -> None:
    logger.info("[Lidarr Handoff] %s: Lidarr imported %d of %d file(s)%s", label,
                outcome.get('imported', 0), total,
                f" ({outcome['error']})" if outcome.get('error') else '')
    for path, reason in (outcome.get('rejected') or {}).items():
        logger.warning("[Lidarr Handoff] Lidarr did not take %s: %s", path, reason)


def _lidarr_client(client: Any) -> Any:
    if client is None:
        from core.download_orchestrator import get_download_orchestrator
        client = get_download_orchestrator().client('lidarr')
    if client is None or not hasattr(client, 'import_files'):
        return None
    return client


def hand_batch_to_lidarr(batch_id: str, batch: dict, tasks_snapshot: dict, *,
                         client: Any = None,
                         enabled: Optional[Callable[[], bool]] = None,
                         transfer_root: Optional[str] = None,
                         repoint: Optional[Callable[[str, str], Any]] = None) -> int:
    """Have Lidarr import the batch's files, then sweep older leftovers.
    Returns how many files of the batch Lidarr took. The keyword arguments
    are for tests; by default they come from the settings and the app."""
    from core.imports.paths import imports_through_lidarr, lidarr_transfer_root
    if not (enabled or imports_through_lidarr)():
        return 0
    root = transfer_root or lidarr_transfer_root()
    groups = files_for_lidarr(batch, tasks_snapshot, root) if root else {}
    client = _lidarr_client(client) if root else None
    if client is None:
        if groups:
            logger.warning("[Lidarr Handoff] Batch %s: Lidarr is not available; %d album(s) "
                           "wait in %s for the next sweep", batch_id, len(groups), root)
        return 0

    from core.downloads.master import (
        bundle_release_type_of, bundle_release_year_of, release_group_id_of)
    is_album = bool(batch.get('is_album_download'))
    taken = 0
    for (album, artist), entries in groups.items():
        infos = [info for _path, info in entries]
        files = [path for path, _info in entries]
        outcome = client.import_into_library(
            files, album, artist,
            release_group_id=release_group_id_of(infos) or tagged_release_group(files) or '',
            release_year=bundle_release_year_of(infos) or '',
            release_type=bundle_release_type_of(infos) or '',
            monitored=is_album,
        )
        _log_outcome(f"Batch {batch_id}: '{album}' by '{artist}'", outcome, len(entries))
        taken += finish_imported(outcome.get('paths') or {}, root, repoint=repoint)
    sweep_lidarr_imports(client=client, transfer_root=root, repoint=repoint)
    return taken


def waiting_files(transfer_root: str, min_age_seconds: int = SWEEP_MIN_AGE_SECONDS,
                  now: Optional[float] = None) -> List[str]:
    """Audio files in the transfer folder old enough for the sweep."""
    now = time.time() if now is None else now
    found = []
    for path in Path(transfer_root).rglob('*'):
        try:
            if (path.is_file() and path.suffix.lower() in _AUDIO_EXTS
                    and now - path.stat().st_mtime >= min_age_seconds):
                found.append(str(path))
        except OSError:
            continue
    return sorted(found)


def sweep_lidarr_imports(*, client: Any = None,
                         enabled: Optional[Callable[[], bool]] = None,
                         transfer_root: Optional[str] = None,
                         min_age_seconds: int = SWEEP_MIN_AGE_SECONDS,
                         repoint: Optional[Callable[[str, str], Any]] = None) -> int:
    """Offer Lidarr every file still waiting in the transfer folder. Lidarr
    identifies each by its tags, so it takes what belongs to an album it has.
    One sweep at a time; returns how many files Lidarr took."""
    from core.imports.paths import imports_through_lidarr, lidarr_transfer_root
    if not (enabled or imports_through_lidarr)():
        return 0
    root = transfer_root or lidarr_transfer_root()
    if not root or not os.path.isdir(root):
        return 0
    if not _sweep_lock.acquire(blocking=False):
        return 0
    try:
        files = waiting_files(root, min_age_seconds)
        client = _lidarr_client(client) if files else None
        if client is None:
            return 0
        by_folder: Dict[str, List[str]] = {}
        for path in files:
            by_folder.setdefault(os.path.dirname(path), []).append(path)
        taken = 0
        for folder, folder_files in sorted(by_folder.items()):
            # the batch that knew the album is gone: the tags name it, and
            # Lidarr needs the album (and its artist) before it can take the
            # files. Added unmonitored: a recovery must not start a download.
            album = _tagged(folder_files, 'album')
            artist = _tagged(folder_files, 'albumartist') or _tagged(folder_files, 'artist')
            if album and artist:
                outcome = client.import_into_library(
                    folder_files, album, artist,
                    release_group_id=tagged_release_group(folder_files) or '', monitored=False)
            else:
                outcome = client.import_files(folder_files)
            _log_outcome(f"Sweep of {folder}", outcome, len(folder_files))
            taken += finish_imported(outcome.get('paths') or {}, root, repoint=repoint)
        return taken
    finally:
        _sweep_lock.release()


def start_startup_sweep() -> Optional[threading.Thread]:
    """Run the sweep once in the background (startup: a restart is the case
    it exists for, and Lidarr may be slow to answer)."""
    from core.imports.paths import imports_through_lidarr
    if not imports_through_lidarr():
        return None

    def _run():
        try:
            sweep_lidarr_imports()
        except Exception as exc:  # noqa: BLE001 - the files stay, the next sweep retries
            logger.warning("[Lidarr Handoff] startup sweep failed: %s", exc)

    thread = threading.Thread(target=_run, name='LidarrImportSweep', daemon=True)
    thread.start()
    return thread


__all__ = ['files_for_lidarr', 'finish_imported', 'hand_batch_to_lidarr',
           'start_startup_sweep', 'sweep_lidarr_imports', 'waiting_files']
