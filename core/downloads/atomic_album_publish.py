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

# The staging tree lives as a hidden sibling of the transfer dir so it shares the
# transfer dir's filesystem (publish becomes an atomic rename) yet sits OUTSIDE
# the folder Plex/Jellyfin/Navidrome actually scan.
_STAGING_DIRNAME = ".soulsync_atomic_staging"

_AUDIO_EXTS = {'.flac', '.mp3', '.m4a', '.mp4', '.ogg', '.oga', '.opus',
               '.wav', '.aiff', '.aif', '.wma', '.alac'}


def staging_root_for_batch(transfer_dir: str, batch_id: str) -> str:
    """The private staging root for a batch — a hidden sibling of the transfer
    dir (same filesystem → atomic publish; not under the scanned library)."""
    parent = os.path.dirname(os.path.normpath(transfer_dir))
    return os.path.join(parent, _STAGING_DIRNAME, str(batch_id))


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

    Returns ``{published: [(staging, final)], failed: [(staging, err)]}``. A
    per-file failure leaves THAT file staged (never partially in the library) and
    is reported — the caller keeps the staging tree for retry/quarantine.
    """
    published: List[Tuple[str, str]] = []
    failed: List[Tuple[str, str]] = []

    for staged in iter_staged_files(staging_root):
        final = to_final_path(staged, staging_root, transfer_dir)
        if not final:
            failed.append((staged, "could not map staged path back to a library path"))
            continue
        try:
            move_fn(staged, final)
        except Exception as e:  # noqa: BLE001 — report + keep staged, never lose the file
            logger.error("[Atomic Publish] move failed %s -> %s: %s", staged, final, e)
            failed.append((staged, str(e)))
            continue
        published.append((staged, final))
        if db_path_update_fn is not None:
            try:
                db_path_update_fn(staged, final)
            except Exception as e:  # noqa: BLE001 — file is published; DB fix is best-effort
                logger.error("[Atomic Publish] DB path update failed %s -> %s: %s",
                             staged, final, e)

    # Remove the staging tree only when everything published (no orphan left behind).
    if not failed:
        _prune_empty_tree(staging_root)

    return {"published": published, "failed": failed}


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
]
