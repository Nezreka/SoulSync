"""Atomic album publishing (#999) — opt-in, default off.

When ``album_downloads.atomic_publish`` is on, an album batch's tracks are
post-processed into a private STAGING mirror of their final library paths
instead of straight into the media-library ("transfer") folder, and are moved
into the library only once the WHOLE batch completes — so Plex/Jellyfin/
Navidrome never sees a partial album mid-download. If the batch never completes,
the staged files stay out of the library (quarantine) and the failed tracks stay
retryable in the wishlist.

This module is PURE mechanics — path math + move + DB path fix-up. Every gate
decision and all wiring live at the call sites (the pipeline redirect and the
batch-complete publish), behind the config flag. Nothing here reads config or
touches global state, so it is trivially unit-testable and, until wired, inert.

Scope guardrails baked in here (defensive; the call sites also gate):
  * ``to_staging_path`` returns None unless the final path is genuinely UNDER the
    transfer dir — we never stage a path we can't map back, so a bad input falls
    through to today's direct-publish behavior rather than misplacing a file.
  * ``album_folder_is_fresh`` lets callers restrict atomic mode to a NEW album
    folder (empty / absent), so a completeness-fill into an album the user
    already owns is never re-staged (avoids any quality-replace surprise on an
    existing file — that path keeps today's per-track publish).
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("downloads.atomic_album_publish")

# The staging tree lives as a hidden folder INSIDE the transfer dir.
#
# It used to be a SIBLING, on the reasoning that a sibling shares the transfer
# dir's filesystem (so publish is an atomic rename) while sitting outside the
# folder media servers scan. That reasoning fails for nearly every Docker user,
# because the transfer dir is itself a bind mount:
#
#     D:/Music:/app/Transfer   →  sibling is /app  →  the CONTAINER's own layer
#
# which is a different filesystem, is usually not writable, and is thrown away
# when the container is recreated. It produced a hard failure, not a degrade —
# mkdir raised PermissionError, the track never left downloads, and the user
# saw "File verification failed: expected file at 08 - Alabama.flac but it was
# not found after processing" with every file still sitting in /app/downloads.
#
# Inside the transfer dir is the only location that is same-filesystem and
# writable BY CONSTRUCTION: if the library is not writable, there is nothing to
# publish into anyway. The dot prefix keeps it out of media-server scans, and
# SoulSync's own scan prunes dot-directories for the same reason.
_STAGING_DIRNAME = ".soulsync_atomic_staging"

_AUDIO_EXTS = {'.flac', '.mp3', '.m4a', '.mp4', '.ogg', '.oga', '.opus',
               '.wav', '.aiff', '.aif', '.wma', '.alac'}


def staging_root_for_batch(transfer_dir: str, batch_id: str) -> str:
    """The private staging root for a batch — a hidden folder INSIDE the
    transfer dir (same filesystem → atomic publish; dot-prefixed so scanners
    skip it). See the note on _STAGING_DIRNAME for why not a sibling."""
    return os.path.join(os.path.normpath(transfer_dir), _STAGING_DIRNAME, str(batch_id))


def is_staged_path(path: str, transfer_dir: str) -> bool:
    """True when ``path`` is already inside the staging tree.

    Load-bearing now that staging lives UNDER the transfer dir: without it,
    ``to_staging_path`` would happily map an already-staged file into a staging
    mirror of itself, one level deeper on every pass."""
    try:
        path_n = os.path.normpath(os.path.abspath(path))
        root_n = os.path.normpath(os.path.abspath(
            os.path.join(os.path.normpath(transfer_dir), _STAGING_DIRNAME)))
    except (OSError, ValueError):
        return False
    return path_n == root_n or path_n.startswith(root_n + os.sep)


def to_staging_path(final_path: str, transfer_dir: str, staging_root: str) -> Optional[str]:
    """Map a track's FINAL library path into its batch staging mirror, preserving
    the relative artist/album/disc/file structure. Returns None if ``final_path``
    is not under ``transfer_dir`` (caller then keeps today's direct publish)."""
    try:
        final_n = os.path.normpath(os.path.abspath(final_path))
        transfer_n = os.path.normpath(os.path.abspath(transfer_dir))
    except (OSError, ValueError):
        return None
    if final_n == transfer_n:
        return None
    prefix = transfer_n + os.sep
    if not final_n.startswith(prefix):
        return None
    # Already staged — mapping it again would nest a staging mirror inside the
    # staging tree. Only reachable since staging moved under the transfer dir.
    if is_staged_path(final_n, transfer_n):
        return None
    rel = final_n[len(prefix):]
    return os.path.join(staging_root, rel)


def to_final_path(staged_path: str, staging_root: str, transfer_dir: str) -> Optional[str]:
    """Inverse of :func:`to_staging_path` — map a staged file back to its final
    library path. None if ``staged_path`` isn't under ``staging_root``."""
    try:
        staged_n = os.path.normpath(os.path.abspath(staged_path))
        root_n = os.path.normpath(os.path.abspath(staging_root))
    except (OSError, ValueError):
        return None
    prefix = root_n + os.sep
    if not staged_n.startswith(prefix):
        return None
    rel = staged_n[len(prefix):]
    return os.path.join(os.path.normpath(transfer_dir), rel)


def album_folder_is_fresh(album_folder: str) -> bool:
    """True when the target album folder holds no audio yet (absent or empty of
    audio). Lets a caller restrict atomic staging to NEW albums so a
    completeness-fill into an owned album keeps today's per-track publish."""
    try:
        if not os.path.isdir(album_folder):
            return True
        for name in os.listdir(album_folder):
            if os.path.splitext(name)[1].lower() in _AUDIO_EXTS:
                return False
        return True
    except OSError:
        # Can't tell → treat as NOT fresh so we fall back to safe per-track publish.
        return False


def iter_staged_files(staging_root: str) -> List[str]:
    """Every real file under the staging root (audio + sidecars: art, .lrc, …),
    so publish moves the whole prepared album folder, not just the audio."""
    out: List[str] = []
    if not os.path.isdir(staging_root):
        return out
    for root, _dirs, files in os.walk(staging_root):
        for name in files:
            out.append(os.path.join(root, name))
    # Sorted, because publish order decides what a rollback has to undo. os.walk
    # hands back directory order, which is the filesystem's business and differs
    # between machines — the same album published two files in the opposite
    # order on btrfs and on ext4, so a failure on the second track rolled back
    # a different track depending on the box it ran on.
    out.sort()
    return out


def publish_album_batch(
    staging_root: str,
    transfer_dir: str,
    move_fn: Callable[[str, str], None],
    db_path_update_fn: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, object]:
    """Move a completed batch's staged files into the live library, then update
    the DB path for each and remove the emptied staging tree.

    Args:
        staging_root: the batch's staging root (from ``staging_root_for_batch``).
        transfer_dir: the live media-library root.
        move_fn: ``move_fn(src, dst)`` — the same atomic mover the pipeline uses
            (creates parent dirs, atomic same-fs / safe cross-fs). Injected so
            this stays pure/testable.
        db_path_update_fn: optional ``fn(staging_path, final_path)`` to repoint a
            track's DB ``file_path`` from staging to final. Injected.

    Returns ``{success, published: [(staging, final)], failed: [(staging, err)],
    rollback_failed: [(final, err)]}``.

    All or nothing (L2-002). "The whole album appears at once" is the entire
    point of atomic publishing, so a per-file failure is not survivable by
    leaving the rest live: the caller would report Complete for an album that is
    half in the library and half in staging, with no retryable task for the
    missing half. Any failure — a move, a mapping, or the DB repoint that tells
    the library where the file now is — rolls every already-moved file back into
    staging and reports ``success=False``, leaving the batch exactly as it was
    so a retry can publish the whole thing.

    ``db_path_update_fn`` may return the number of rows it repointed. Zero rows
    for an AUDIO file means the library still points at a staging path that is
    about to stop existing, which is a failed publish, not a warning.

    A rollback takes the db pointer back with the file. It is called in reverse
    as ``db_path_update_fn(final, staged)``, so the same function that publishes
    a row also un-publishes it, and only for files that really were repointed.
    """
    published: List[Tuple[str, str]] = []
    failed: List[Tuple[str, str]] = []
    # Which files actually had their library row repointed at the final path.
    # Not the same as `published`: a file whose move worked but whose repoint
    # raised is published and NOT repointed, and rolling its db row back would
    # be undoing something that never happened.
    repointed: set = set()

    def _roll_back() -> List[Tuple[str, str]]:
        """Put every published file back in staging, and take the library's
        pointer back with it. Returns what would not go.

        The repoint is the half that is easy to forget. Moving the file back on
        its own leaves the db insisting the track is live at a path that no
        longer exists, which is the same split state the all-or-nothing rule
        exists to prevent, just pointing the other way.

        Order matters: move first, repoint only if that move actually worked. A
        file still sitting at its final path because the rollback could not
        shift it MUST keep a db row that says final, or we would strand a file
        the library can no longer find.
        """
        stuck: List[Tuple[str, str]] = []
        for staged_path, final_path in reversed(published):
            try:
                move_fn(final_path, staged_path)
            except Exception as e:  # noqa: BLE001
                logger.error("[Atomic Publish] rollback failed %s -> %s: %s",
                             final_path, staged_path, e)
                stuck.append((final_path, str(e)))
                continue
            if db_path_update_fn is None or staged_path not in repointed:
                continue
            try:
                db_path_update_fn(final_path, staged_path)
            except Exception as e:  # noqa: BLE001
                logger.error("[Atomic Publish] DB rollback failed %s -> %s: %s",
                             final_path, staged_path, e)
                stuck.append((final_path, f"DB path rollback failed: {e}"))
        return stuck

    for staged in iter_staged_files(staging_root):
        final = to_final_path(staged, staging_root, transfer_dir)
        if not final:
            failed.append((staged, "could not map staged path back to a library path"))
            break
        try:
            move_fn(staged, final)
        except Exception as e:  # noqa: BLE001 — report + keep staged, never lose the file
            logger.error("[Atomic Publish] move failed %s -> %s: %s", staged, final, e)
            failed.append((staged, str(e)))
            break
        published.append((staged, final))
        if db_path_update_fn is not None:
            try:
                rows = db_path_update_fn(staged, final)
            except Exception as e:  # noqa: BLE001
                logger.error("[Atomic Publish] DB path update failed %s -> %s: %s",
                             staged, final, e)
                failed.append((staged, f"DB path update failed: {e}"))
                break
            repointed.add(staged)
            is_audio = os.path.splitext(staged)[1].lower() in _AUDIO_EXTS
            if is_audio and isinstance(rows, int) and rows < 1:
                logger.error("[Atomic Publish] DB path update matched no row for %s", staged)
                failed.append((staged, "DB path update matched no library row"))
                break

    rollback_failed: List[Tuple[str, str]] = []
    if failed:
        rollback_failed = _roll_back()
        published = []
    else:
        # Remove the staging tree only when everything published.
        _prune_empty_tree(staging_root)

    return {"success": not failed, "published": published, "failed": failed,
            "rollback_failed": rollback_failed}


def discard_staging_root(staging_root: Optional[str]) -> bool:
    """Remove a batch's staging tree — quarantine cleanup for a cancelled or
    abandoned atomic batch (its downloaded-but-unpublished tracks are dropped so
    they re-download). Returns True if a directory was removed.

    Guarded: only ever removes a path whose immediate parent is the dedicated
    ``_STAGING_DIRNAME`` folder, so a blank/misconfigured value can never rmtree
    anything but a genuine staging root. Best-effort; a successfully-published
    batch already had its staging pruned, so this is a no-op there."""
    if not staging_root:
        return False
    import shutil
    try:
        norm = os.path.normpath(staging_root)
        if os.path.basename(os.path.dirname(norm)) != _STAGING_DIRNAME:
            return False  # not a staging root — refuse
        if not os.path.isdir(norm):
            return False
        shutil.rmtree(norm, ignore_errors=True)
        return True
    except OSError:
        return False


def _prune_empty_tree(root: str) -> None:
    """Remove ``root`` and any now-empty subdirs. Best-effort; leaves anything
    still holding files untouched."""
    if not os.path.isdir(root):
        return
    for dirpath, _dirs, _files in os.walk(root, topdown=False):
        try:
            os.rmdir(dirpath)
        except OSError:
            pass  # non-empty or gone — leave it


__all__ = [
    "staging_root_for_batch",
    "to_staging_path",
    "to_final_path",
    "album_folder_is_fresh",
    "iter_staged_files",
    "publish_album_batch",
    "discard_staging_root",
]
