"""Startup reconciliation for abandoned atomic-album staging trees (#1289).

Atomic album publishing (#999) parks a batch's finished tracks under
``<library>/.soulsync_atomic_staging/<batch id>/`` until the whole album is
ready. The publish that empties that tree is driven entirely from
``download_batches``, which is a dict in the web process, so a restart, a crash
or an OOM anywhere in the middle of an album leaves the tree behind with nobody
left who knows what it is.

Nothing in SoulSync looked at it afterwards. The dot prefix hides it from every
media server; SoulSync's own scanner and the repair jobs prune it explicitly.
The audio was complete, tagged and correctly named, and permanently invisible.
The only code that ever touched an abandoned tree again deleted it.

So: at startup, before any new batch can claim a staging directory, walk what is
on disk and finish the job.

On publishing a recovered album
-------------------------------
Recovering publishes the tracks the interrupted batch had actually finished,
which can mean an album that is not yet complete becomes visible. That does not
weaken #999's guarantee, which is about what the media server sees *while an
album is downloading* — that path is untouched, and a live batch still publishes
all-or-nothing at completion. Here the download is already over; the choice is
between an album that fills in and an album that never appears at all.

It also composes with the rest of the flow rather than fighting it: the tracks
the batch never finished are still on the wishlist (that is the other half of
#1289), and when they are downloaded later the album folder is no longer fresh,
so ``album_folder_is_fresh`` routes them through the ordinary per-track publish
straight into the now-existing album. Recovered audio is never re-downloaded,
and the album completes itself.

Users who would rather keep strict quarantine can set
``album_downloads.atomic_recover_orphans`` to false; the trees are then reported
and left alone, never deleted.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Dict, Iterable, List, Optional

# Imported as a MODULE, not as names: recovery reuses the live publish, and
# binding the functions here would freeze whatever they were at import time.
from core.downloads import atomic_album_publish as _publish
from core.downloads.atomic_manifest import (
    manifest_tracks,
    orphan_staging_roots,
    read_manifest,
    remove_manifest,
)
from utils.logging_config import get_logger

logger = get_logger("downloads.atomic_recovery")


def make_db_path_updater(db):
    """``fn(old_path, new_path) -> rows | None`` repointing a library row.

    Returns None — meaning "this count carries no information" — on anything but
    a ``soulsync`` media server, because rows holding a staged path are only
    ever written by ``record_soulsync_library_entry``, which is gated on that.
    Reading a legitimate 0 as failure is what stranded albums in staging for
    Navidrome/Plex/Jellyfin users in the first place.
    """
    def _update(old_path: str, new_path: str):
        conn = db._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE tracks SET file_path = ? WHERE file_path = ?",
                        (new_path, old_path))
            conn.commit()
            rowcount = getattr(cur, 'rowcount', None)
        finally:
            conn.close()
        try:
            from core.settings import config_manager as _cm
            if _cm.get_active_media_server() != 'soulsync':
                return None
        except Exception:  # noqa: BLE001 - can't tell whose rows these are → unknown
            return None
        return int(rowcount) if isinstance(rowcount, int) else None
    return _update


def context_from_manifest_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild the minimum import context a wishlist removal needs.

    The live path hands ``check_and_remove_from_wishlist`` the real context. A
    recovered batch has only what the manifest wrote, so this reassembles the
    few fields ``get_import_source_ids`` reads — enough to resolve the same
    track id, and nothing more.
    """
    source_ids = entry.get('source_ids') or {}
    artist_name = entry.get('artist_name') or ''
    return {
        'source': entry.get('source') or '',
        'artist': {'id': source_ids.get('artist_id') or '', 'name': artist_name},
        'album': {'id': source_ids.get('album_id') or ''},
        'track_info': {
            'id': source_ids.get('track_id') or '',
            'name': entry.get('track_name') or '',
            'artists': [{'name': artist_name}] if artist_name else [],
        },
        'original_search_result': {},
        'search_result': {},
    }


def _pending_from_manifest(manifest: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pending: List[Dict[str, Any]] = []
    for staged_path, entry in manifest_tracks(manifest).items():
        if not isinstance(entry, dict):
            continue
        pending.append({
            'staged_path': os.path.normpath(str(staged_path)),
            'final_path': entry.get('final_path') or '',
            'track_name': entry.get('track_name') or '',
            'context': context_from_manifest_entry(entry),
        })
    return pending


def _set_aside_superseded(root: str, transfer_dir: str,
                          staged_files: List[str]) -> Optional[int]:
    """Quarantine staged files the library already has, before publishing.

    A LIVE atomic batch cannot collide: ``album_folder_is_fresh`` only lets a
    batch stage into an album folder that holds no audio. A tree recovered weeks
    later has no such guarantee — the user has almost certainly re-requested the
    stranded tracks in the meantime, and some of them have landed. And
    ``safe_move_file`` publishes with ``os.replace``, which OVERWRITES, so a
    straight publish would silently put a months-old staged copy on top of the
    file the normal pipeline just imported and verified.

    So a staged file whose final path is already taken is set aside into the
    Downloads recycle bin (restorable, exactly like every other delete path
    here) and the rest of the album publishes normally: the tracks that really
    are missing get recovered, the tracks the library already has are left
    alone, and nothing is overwritten or destroyed.

    Returns how many files were set aside, or None if any of them could not be —
    in which case the caller must not publish, because publishing would
    overwrite.
    """
    collisions = []
    for staged in staged_files:
        final = _publish.to_final_path(staged, root, transfer_dir)
        if final and os.path.exists(final):
            collisions.append((staged, final))
    if not collisions:
        return 0

    try:
        from core.library.deleted_quarantine import record_deleted_entry
        from core.repair_jobs.base import deleted_quarantine_root
        rescue_root = deleted_quarantine_root(transfer_dir)
        dest_parent = os.path.join(rescue_root, 'atomic_superseded', os.path.basename(root))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[Atomic Recovery] %d staged file(s) in %s are already in the library and the "
            "recycle bin is unavailable (%s) — leaving the whole tree staged rather than "
            "overwriting them.", len(collisions), root, exc)
        return None

    moved = 0
    for staged, final in collisions:
        try:
            rel = os.path.relpath(staged, root)
            dest = os.path.join(dest_parent, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            suffix = 1
            while os.path.exists(dest):
                stem, ext = os.path.splitext(dest)
                dest = f"{stem}__{suffix}{ext}"
                suffix += 1
            shutil.move(staged, dest)
            try:
                record_deleted_entry(rescue_root, dest, staged, 'atomic_superseded')
            except Exception as rec_err:  # noqa: BLE001 - the file is safe either way
                logger.debug("[Atomic Recovery] quarantine manifest entry failed: %s", rec_err)
            moved += 1
            logger.warning(
                "[Atomic Recovery] %s is already in your library — the older staged copy "
                "was moved to the recycle bin instead of overwriting it.", final)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[Atomic Recovery] Could not set aside %s (%s) — leaving the whole tree "
                "staged rather than overwriting %s.", staged, exc, final)
            return None
    return moved


def _transfer_dir_for(root: str, manifest: Optional[Dict[str, Any]],
                      staged_files: List[str]) -> Optional[str]:
    """Which library this tree publishes into.

    The manifest says so directly. Without one the staged layout still does —
    everything above ``.soulsync_atomic_staging`` IS the library root — which is
    why a tree with no manifest is still recoverable.
    """
    recorded = (manifest or {}).get('transfer_dir')
    if recorded and os.path.isdir(str(recorded)):
        return str(recorded)
    split = _publish.split_staged_path(staged_files[0] if staged_files else root)
    if split:
        return split[0]
    # `str(recorded) or None` returned the literal string 'None' when there was
    # no manifest, because str(None) is truthy.
    return str(recorded) if recorded else None


def recover_orphan_staging(transfer_dirs: Iterable[str], *,
                           live_batch_ids: Iterable[str] = (),
                           enabled: bool = True,
                           remove_from_wishlist=None) -> Dict[str, int]:
    """Publish (or report) every staging tree no live batch owns.

    Returns counts: ``{scanned, published, files, failed, empty, reported,
    wishlist_cleared}``. Never raises — a startup task that can abort the boot
    is worse than the problem it fixes.
    """
    stats = {'scanned': 0, 'published': 0, 'files': 0, 'failed': 0,
             'empty': 0, 'reported': 0, 'wishlist_cleared': 0, 'superseded': 0}

    roots: List[str] = []
    seen = set()
    for transfer_dir in transfer_dirs or ():
        if not transfer_dir:
            continue
        for root in orphan_staging_roots(transfer_dir, live_batch_ids):
            key = os.path.normpath(root)
            if key not in seen:
                seen.add(key)
                roots.append(root)

    if not roots:
        return stats

    if remove_from_wishlist is None:
        from core.wishlist.resolution import remove_published_wishlist_entries
        remove_from_wishlist = remove_published_wishlist_entries

    for root in roots:
        stats['scanned'] += 1
        try:
            staged_files = _publish.iter_staged_files(root)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Atomic Recovery] Could not read %s: %s", root, exc)
            continue

        manifest = read_manifest(root)

        if not staged_files:
            # A published batch prunes its own tree; an empty one left behind is
            # bookkeeping, not audio, and is the one thing safe to clear.
            remove_manifest(root)
            # prune, not rmdir: a tree whose audio has already been published
            # still holds its empty album/artist subdirectories, and rmdir on a
            # non-empty directory fails, leaving a husk to rescan every boot.
            _publish.prune_empty_tree(root)
            stats['empty'] += 1
            continue

        if not enabled:
            logger.warning(
                "[Atomic Recovery] %d staged file(s) in %s are not in your library and "
                "recovery is disabled (album_downloads.atomic_recover_orphans). The "
                "tracks remain on the wishlist; nothing will be deleted.",
                len(staged_files), root)
            stats['reported'] += 1
            continue

        transfer_dir = _transfer_dir_for(root, manifest, staged_files)
        if not transfer_dir or not os.path.isdir(transfer_dir):
            logger.error(
                "[Atomic Recovery] Cannot resolve the library folder for %s — leaving "
                "%d staged file(s) in place.", root, len(staged_files))
            stats['failed'] += 1
            continue

        # Never overwrite a file the library already has (see _set_aside_superseded).
        set_aside = _set_aside_superseded(root, transfer_dir, staged_files)
        if set_aside is None:
            stats['failed'] += 1
            continue
        if set_aside:
            stats['superseded'] += set_aside
            staged_files = _publish.iter_staged_files(root)
            if not staged_files:
                logger.warning(
                    "[Atomic Recovery] Every staged file in %s was already in the library "
                    "— nothing to publish; the older copies are in the recycle bin.", root)
                remove_manifest(root)
                _publish.prune_empty_tree(root)
                stats['empty'] += 1
                continue

        logger.warning(
            "[Atomic Recovery] Batch %s left %d file(s) in staging (album=%r) — "
            "publishing them into %s",
            os.path.basename(root), len(staged_files),
            (manifest or {}).get('album_name', ''), transfer_dir)

        try:
            from core.imports.file_ops import safe_move_file
            from database.music_database import MusicDatabase
            result = _publish.publish_album_batch(
                root, transfer_dir, safe_move_file,
                make_db_path_updater(MusicDatabase()))
        except Exception as exc:  # noqa: BLE001
            logger.error("[Atomic Recovery] Publish of %s failed, files kept in staging: %s",
                         root, exc, exc_info=True)
            stats['failed'] += 1
            continue

        if not result.get('success'):
            failures = result.get('failed') or []
            logger.error(
                "[Atomic Recovery] %s NOT published: %d file(s) failed (%s) — everything "
                "kept in staging, the tracks stay on the wishlist.",
                root, len(failures), failures[0][1] if failures else 'unknown')
            stats['failed'] += 1
            continue

        published = result.get('published') or []
        stats['published'] += 1
        stats['files'] += len(published)
        logger.warning("[Atomic Recovery] Recovered %d file(s) from %s into the library",
                       len(published), root)

        pending = _pending_from_manifest(manifest)
        if pending:
            try:
                cleared = remove_from_wishlist(
                    pending, {s: f for s, f in published},
                    batch_id=os.path.basename(root))
                stats['wishlist_cleared'] += int(cleared or 0)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[Atomic Recovery] Recovered files are live but the wishlist entries "
                    "could not be cleared (%s) — they will be cleared by the already-owned "
                    "cleanup instead.", exc)

    return stats


__all__ = ["context_from_manifest_entry", "make_db_path_updater", "recover_orphan_staging"]
