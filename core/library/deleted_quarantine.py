"""The music-side deleted quarantine, made browsable.

files removed by the repair worker and the duplicate cleaner don't die -
they move into <transfer>/.deleted with their path relative to the transfer
folder preserved. that made them recoverable in principle but invisible in
practice: no listing, no restore, no idea when something was put there.

this module is that missing layer. movers call record_deleted_entry() right
after their shutil.move so a manifest at the quarantine root remembers the
original absolute path, when it was deleted, and which tool did it. listing
walks the folder (manifest or not - files quarantined before the manifest
existed still show up, just with less detail), restore puts a file back
where it came from, purge deletes it for real.

retention: entries older than library.deleted_keep_days are purged
opportunistically on every list. 0 (the default) keeps everything forever -
nothing changes for anyone who doesn't opt in. only files the manifest has
a deleted_at for are age-purged: a move preserves the file's original
mtime, so aging unmanifested files by mtime would purge a fresh delete of
an old file on day one.

ids handed to the api are "<root-tag>:<relative path>" - "deleted" for the
canonical .deleted root, "legacy" for a bare deleted folder that could not
be renamed (see deleted_quarantine_root). every id is traversal-checked
before any filesystem op.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.repair_jobs.base import (
    DELETED_QUARANTINE_DIRNAME,
    LEGACY_DELETED_DIRNAME,
    deleted_quarantine_root,
)
from utils.logging_config import get_logger

logger = get_logger("library.deleted_quarantine")

MANIFEST_NAME = ".soulsync_deleted.json"


def _roots(transfer_folder: str) -> List[Tuple[str, str]]:
    """The quarantine roots that exist right now, canonical first.

    deleted_quarantine_root() migrates a legacy bare folder when it can; when
    it can't (or both spellings exist side by side) the legacy folder still
    holds files the user may want back, so it is walked too.
    """
    roots: List[Tuple[str, str]] = []
    canonical = deleted_quarantine_root(transfer_folder)
    if os.path.isdir(canonical):
        roots.append(("deleted", canonical))
    legacy = os.path.join(transfer_folder, LEGACY_DELETED_DIRNAME)
    if os.path.normpath(legacy) != os.path.normpath(canonical) and os.path.isdir(legacy):
        roots.append(("legacy", legacy))
    return roots


def _manifest_path(root: str) -> str:
    return os.path.join(root, MANIFEST_NAME)


def _load_manifest(root: str) -> Dict[str, Any]:
    try:
        with open(_manifest_path(root), encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get("entries")
        return entries if isinstance(entries, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:   # corrupt manifest must never block the quarantine
        logger.warning("deleted-quarantine manifest unreadable at %s: %s", root, exc)
        return {}


def _save_manifest(root: str, entries: Dict[str, Any]) -> None:
    path = _manifest_path(root)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "entries": entries}, f, indent=1)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("deleted-quarantine manifest write failed at %s: %s", root, exc)


def _rel_key(root: str, dest_path: str) -> Optional[str]:
    try:
        rel = os.path.relpath(dest_path, root)
    except ValueError:
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    return rel


def record_deleted_entry(deleted_root: str, dest_path: str, original_path: str,
                         source: str) -> None:
    """Remember where a just-quarantined file came from. Best effort - a
    manifest problem must never fail the move that already happened."""
    try:
        rel = _rel_key(deleted_root, dest_path)
        if not rel:
            return
        entries = _load_manifest(deleted_root)
        entries[rel] = {
            "original": str(original_path),
            "deleted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": str(source),
        }
        _save_manifest(deleted_root, entries)
    except Exception as exc:   # noqa: BLE001 - see docstring
        logger.warning("record_deleted_entry failed for %s: %s", dest_path, exc)


def _resolve_id(transfer_folder: str, entry_id: str) -> Optional[Tuple[str, str, str]]:
    """(root, rel, abs_path) for an id, or None when the id is malformed,
    escapes its root, or names a root that does not exist."""
    if not isinstance(entry_id, str) or ":" not in entry_id:
        return None
    tag, _, rel = entry_id.partition(":")
    rel = rel.replace("\\", "/")
    # absolute BEFORE the strip - stripping first would quietly reinterpret
    # "/etc/passwd" as a relative path and hide the refusal
    if not rel or os.path.isabs(rel) or rel.startswith(("/", "..")):
        return None
    rel = rel.strip("/")
    if any(part in ("..", "") for part in rel.split("/")):
        return None
    for root_tag, root in _roots(transfer_folder):
        if root_tag == tag:
            abs_path = os.path.join(root, *rel.split("/"))
            real_root = os.path.realpath(root)
            if not os.path.realpath(abs_path).startswith(real_root + os.sep):
                return None
            return root, rel, abs_path
    return None


def list_entries(transfer_folder: str, *, keep_days: float = 0) -> Dict[str, Any]:
    """Everything in quarantine, oldest known deletion first.

    keep_days > 0 age-purges expired manifested entries before listing -
    the opportunistic half of retention, so the view the user sees is
    already the retained set.
    """
    if keep_days and keep_days > 0:
        purge_expired(transfer_folder, keep_days)

    entries: List[Dict[str, Any]] = []
    total_size = 0
    for tag, root in _roots(transfer_folder):
        manifest = _load_manifest(root)
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name == MANIFEST_NAME or name.endswith(".tmp"):
                    continue
                abs_path = os.path.join(dirpath, name)
                rel = _rel_key(root, abs_path)
                if not rel:
                    continue
                try:
                    size = os.path.getsize(abs_path)
                except OSError:
                    size = 0
                total_size += size
                meta = manifest.get(rel) if isinstance(manifest.get(rel), dict) else {}
                original = meta.get("original") or os.path.join(transfer_folder, *rel.split("/"))
                entries.append({
                    "id": f"{tag}:{rel}",
                    "name": name,
                    "rel": rel,
                    "size": size,
                    "deleted_at": meta.get("deleted_at"),
                    "source": meta.get("source"),
                    "original_path": original,
                })
    entries.sort(key=lambda e: (e["deleted_at"] is None, e["deleted_at"] or "", e["rel"]))
    return {"entries": entries, "total_size": total_size, "count": len(entries)}


def restore_entries(transfer_folder: str, ids: List[str]) -> Dict[str, Any]:
    """Move files back where they came from. Never overwrites - a file
    already living at the original path is an error for that id, not a
    clobber."""
    restored: List[str] = []
    errors: List[Dict[str, str]] = []
    for entry_id in ids or []:
        resolved = _resolve_id(transfer_folder, entry_id)
        if not resolved:
            errors.append({"id": str(entry_id), "error": "unknown entry"})
            continue
        root, rel, abs_path = resolved
        if not os.path.isfile(abs_path):
            errors.append({"id": entry_id, "error": "file is gone"})
            continue
        manifest = _load_manifest(root)
        meta = manifest.get(rel) if isinstance(manifest.get(rel), dict) else {}
        target = meta.get("original") or os.path.join(transfer_folder, *rel.split("/"))
        if os.path.exists(target):
            errors.append({"id": entry_id, "error": f"a file already exists at {target}"})
            continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(abs_path, target)
        except OSError as exc:
            errors.append({"id": entry_id, "error": str(exc)})
            continue
        if rel in manifest:
            manifest.pop(rel, None)
            _save_manifest(root, manifest)
        _prune_empty_dirs(os.path.dirname(abs_path), root)
        restored.append(entry_id)
        logger.info("deleted-quarantine restore: %s -> %s", abs_path, target)
    return {"restored": restored, "errors": errors}


def purge_entries(transfer_folder: str, ids: Optional[List[str]] = None,
                  *, purge_all: bool = False) -> Dict[str, Any]:
    """Delete quarantined files for real."""
    if purge_all:
        ids = [e["id"] for e in list_entries(transfer_folder)["entries"]]
    purged: List[str] = []
    errors: List[Dict[str, str]] = []
    for entry_id in ids or []:
        resolved = _resolve_id(transfer_folder, entry_id)
        if not resolved:
            errors.append({"id": str(entry_id), "error": "unknown entry"})
            continue
        root, rel, abs_path = resolved
        try:
            os.remove(abs_path)
        except FileNotFoundError:
            pass    # already gone counts as purged - the goal state holds
        except OSError as exc:
            errors.append({"id": entry_id, "error": str(exc)})
            continue
        manifest = _load_manifest(root)
        if rel in manifest:
            manifest.pop(rel, None)
            _save_manifest(root, manifest)
        _prune_empty_dirs(os.path.dirname(abs_path), root)
        purged.append(entry_id)
    return {"purged": purged, "errors": errors}


def purge_expired(transfer_folder: str, keep_days: float) -> int:
    """Age-purge manifested entries past retention. Unmanifested files are
    left alone - no deleted_at means no honest age (mtime survives the
    move, so it dates the file, not the deletion)."""
    if not keep_days or keep_days <= 0:
        return 0
    cutoff = time.time() - keep_days * 86400
    expired: List[str] = []
    for tag, root in _roots(transfer_folder):
        manifest = _load_manifest(root)
        for rel, meta in manifest.items():
            if not isinstance(meta, dict) or not meta.get("deleted_at"):
                continue
            try:
                stamp = datetime.fromisoformat(str(meta["deleted_at"])).timestamp()
            except ValueError:
                continue
            if stamp < cutoff:
                expired.append(f"{tag}:{rel}")
    if not expired:
        return 0
    result = purge_entries(transfer_folder, expired)
    purged = len(result["purged"])
    if purged:
        logger.info("deleted-quarantine retention: purged %d entries older than %s days",
                    purged, keep_days)
    return purged


def _prune_empty_dirs(start: str, root: str) -> None:
    """Best-effort removal of now-empty folders between a removed file and
    the quarantine root. Never removes the root itself."""
    current = start
    real_root = os.path.realpath(root)
    for _ in range(10):
        if not current:
            return
        real = os.path.realpath(current)
        if real == real_root or not real.startswith(real_root + os.sep):
            return
        try:
            if os.path.isdir(current) and not os.listdir(current):
                os.rmdir(current)
            else:
                return
        except OSError:
            return
        current = os.path.dirname(current)
