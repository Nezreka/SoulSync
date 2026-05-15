"""Quarantine entry management — pure helpers for list/delete/approve/recover.

Quarantined files live in `<download_path>/ss_quarantine/` as
`<timestamp>_<original>.<ext>.quarantined` paired with a JSON sidecar
`<timestamp>_<original>.json` written by `core.imports.guards.move_to_quarantine`.

This module provides the read/write/restore primitives. Web routes are
thin glue around these. Pipeline re-run on approval is the caller's
job (we hand back `(file_path, context, bypass_check)`).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("imports.quarantine")


_QUARANTINE_SUFFIX = ".quarantined"


# JSON-serializable scalar predicate. dict / list values get walked
# recursively; anything else is dropped during sidecar serialization.
_SAFE_SCALARS = (str, int, float, bool, type(None))


def serialize_quarantine_context(context: Any) -> Dict[str, Any]:
    """Walk a context dict and emit a JSON-safe copy.

    Drops non-serializable values (sets, custom objects, callables,
    open file handles, etc) silently — sidecar must round-trip through
    `json.dump` / `json.load` without raising. Lists are walked element
    by element; dicts are walked recursively. Anything that isn't a
    scalar / dict / list is converted to a string fallback so caller
    still sees *something* (rather than a silent drop) but won't break
    the JSON write.
    """
    if not isinstance(context, dict):
        return {}
    return _coerce_dict(context)


def _coerce_value(value: Any) -> Any:
    if isinstance(value, _SAFE_SCALARS):
        return value
    if isinstance(value, dict):
        return _coerce_dict(value)
    if isinstance(value, (list, tuple)):
        return [_coerce_value(v) for v in value]
    if isinstance(value, set):
        return [_coerce_value(v) for v in value]
    # Fallback — preserve via str() so caller sees the value's shape
    # without breaking JSON serialization.
    try:
        return str(value)
    except Exception:
        return None


def _coerce_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in d.items():
        if not isinstance(key, str):
            try:
                key = str(key)
            except Exception:
                continue
        out[key] = _coerce_value(value)
    return out


def _entry_id_from_filename(quarantined_filename: str) -> str:
    """Derive a stable entry id from the quarantined filename.

    Strip the `.quarantined` suffix; strip the original file extension;
    return the bare `<timestamp>_<original>` stem. Sidecar uses the
    same stem with a `.json` extension, so the id pairs both sides.
    """
    base = quarantined_filename
    if base.endswith(_QUARANTINE_SUFFIX):
        base = base[: -len(_QUARANTINE_SUFFIX)]
    return Path(base).stem


def list_quarantine_entries(quarantine_dir: str) -> List[Dict[str, Any]]:
    """Enumerate quarantined files paired with their sidecars.

    Returns one dict per `.quarantined` file with: id, filename,
    original_filename (from sidecar), reason, expected_track,
    expected_artist, timestamp, size_bytes, has_full_context (True
    when the sidecar carries a `context` field — required for one-click
    Approve), trigger (which check fired: integrity / acoustid /
    bit_depth / unknown).

    Orphaned `.quarantined` files (no sidecar) still surface — caller
    can delete them. Orphaned sidecars (no file) are skipped silently.
    Sorted newest-first by timestamp prefix.
    """
    entries: List[Dict[str, Any]] = []
    if not os.path.isdir(quarantine_dir):
        return entries

    for name in os.listdir(quarantine_dir):
        if not name.endswith(_QUARANTINE_SUFFIX):
            continue
        full_path = os.path.join(quarantine_dir, name)
        if not os.path.isfile(full_path):
            continue

        entry_id = _entry_id_from_filename(name)
        sidecar_path = os.path.join(quarantine_dir, f"{entry_id}.json")
        sidecar: Dict[str, Any] = {}
        if os.path.isfile(sidecar_path):
            try:
                with open(sidecar_path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    sidecar = loaded
            except Exception as exc:
                logger.debug("sidecar read failed for %s: %s", entry_id, exc)

        try:
            size_bytes = os.path.getsize(full_path)
        except OSError:
            size_bytes = 0

        # Issue #608 follow-up (AfonsoG6): surface the source username
        # + filename that was originally downloaded, so the user can see
        # at a glance which uploader the bad file came from. Lives
        # under `context.original_search_result` when full context is
        # persisted; absent on legacy thin sidecars.
        ctx = sidecar.get("context") if isinstance(sidecar.get("context"), dict) else {}
        osr = ctx.get("original_search_result") if isinstance(ctx.get("original_search_result"), dict) else {}
        source_username = osr.get("username", "") if isinstance(osr, dict) else ""
        source_filename = osr.get("filename", "") if isinstance(osr, dict) else ""

        entries.append(
            {
                "id": entry_id,
                "filename": name,
                "original_filename": sidecar.get("original_filename", name),
                "reason": sidecar.get("quarantine_reason", "Unknown reason"),
                "expected_track": sidecar.get("expected_track", ""),
                "expected_artist": sidecar.get("expected_artist", ""),
                "timestamp": sidecar.get("timestamp", ""),
                "size_bytes": size_bytes,
                "has_full_context": isinstance(sidecar.get("context"), dict),
                "trigger": sidecar.get("trigger", "unknown"),
                "source_username": source_username,
                "source_filename": source_filename,
            }
        )

    entries.sort(key=lambda e: e["id"], reverse=True)
    return entries


def _resolve_entry_paths(quarantine_dir: str, entry_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Locate the `.quarantined` file + JSON sidecar for an entry id.

    Returns (file_path, sidecar_path), either may be None if missing.
    """
    if not os.path.isdir(quarantine_dir) or not entry_id:
        return None, None
    file_path: Optional[str] = None
    for name in os.listdir(quarantine_dir):
        if not name.endswith(_QUARANTINE_SUFFIX):
            continue
        if _entry_id_from_filename(name) == entry_id:
            file_path = os.path.join(quarantine_dir, name)
            break
    sidecar_path = os.path.join(quarantine_dir, f"{entry_id}.json")
    if not os.path.isfile(sidecar_path):
        sidecar_path = None
    return file_path, sidecar_path


def delete_quarantine_entry(quarantine_dir: str, entry_id: str) -> bool:
    """Delete the quarantined file + sidecar for the given entry id.

    Returns True if at least one of the two was removed. False when
    neither existed (entry already gone).
    """
    file_path, sidecar_path = _resolve_entry_paths(quarantine_dir, entry_id)
    removed = False
    if file_path and os.path.isfile(file_path):
        try:
            os.remove(file_path)
            removed = True
        except OSError as exc:
            logger.error("Failed to delete quarantine file %s: %s", file_path, exc)
    if sidecar_path and os.path.isfile(sidecar_path):
        try:
            os.remove(sidecar_path)
            removed = True
        except OSError as exc:
            logger.error("Failed to delete quarantine sidecar %s: %s", sidecar_path, exc)
    return removed


def _restore_filename(quarantined_filename: str, sidecar_original: Optional[str] = None) -> str:
    """Resolve the filename to restore.

    Sidecar's `original_filename` wins when provided — it's the
    canonical record of what the file was named before quarantine.
    Otherwise parse the `<YYYYMMDD_HHMMSS>_<original>.<ext>.quarantined`
    convention written by `move_to_quarantine`, dropping the timestamp
    prefix and `.quarantined` suffix. Final fallback returns the
    quarantined filename minus the suffix unchanged.
    """
    if sidecar_original:
        return sidecar_original
    base = quarantined_filename
    if base.endswith(_QUARANTINE_SUFFIX):
        base = base[: -len(_QUARANTINE_SUFFIX)]
    parts = base.split("_", 2)
    if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
        return parts[2]
    return base


def approve_quarantine_entry(
    quarantine_dir: str,
    entry_id: str,
    restore_dir: str,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    """Restore a quarantined file for re-import via the post-process pipeline.

    Reads the sidecar's `context` + `trigger`, moves the file out of
    quarantine to `restore_dir` (with the original filename + extension),
    deletes the sidecar.

    Returns `(restored_file_path, context, trigger)` so the caller can
    set the appropriate `_skip_quarantine_check` bypass flag and
    dispatch the post-process pipeline.

    Returns None when:
        - the entry doesn't exist
        - the sidecar lacks a serialized `context` (legacy thin sidecar
          — caller should fall back to `recover_to_staging` instead)
        - the file move fails
    """
    file_path, sidecar_path = _resolve_entry_paths(quarantine_dir, entry_id)
    if not file_path or not sidecar_path:
        logger.warning("approve: entry %s missing file or sidecar", entry_id)
        return None

    try:
        with open(sidecar_path, encoding="utf-8") as f:
            sidecar = json.load(f)
    except Exception as exc:
        logger.error("approve: sidecar read failed for %s: %s", entry_id, exc)
        return None

    context = sidecar.get("context")
    if not isinstance(context, dict):
        logger.info("approve: entry %s has thin sidecar (no context) — caller should recover-to-staging", entry_id)
        return None

    trigger = str(sidecar.get("trigger", "unknown"))

    original_name = sidecar.get("original_filename") or _restore_filename(os.path.basename(file_path))
    os.makedirs(restore_dir, exist_ok=True)
    restored_path = os.path.join(restore_dir, original_name)
    restored_path = _ensure_unique_path(restored_path)

    try:
        shutil.move(file_path, restored_path)
    except OSError as exc:
        logger.error("approve: failed to restore %s -> %s: %s", file_path, restored_path, exc)
        return None

    try:
        os.remove(sidecar_path)
    except OSError as exc:
        logger.warning("approve: failed to remove sidecar %s: %s", sidecar_path, exc)

    return restored_path, context, trigger


def recover_to_staging(
    quarantine_dir: str,
    staging_dir: str,
    entry_id: str,
) -> Optional[str]:
    """Move a quarantined file into Staging for manual import.

    Strips the timestamp prefix + `.quarantined` suffix, drops the file
    into `staging_dir` so the user can finish via the existing Import
    flow. Sidecar is removed. Used as the fallback path for legacy thin
    sidecars (no embedded `context`) where one-click Approve is
    impossible.
    """
    file_path, sidecar_path = _resolve_entry_paths(quarantine_dir, entry_id)
    if not file_path:
        return None

    sidecar_original = None
    if sidecar_path:
        try:
            with open(sidecar_path, encoding="utf-8") as f:
                sidecar_original = json.load(f).get("original_filename")
        except Exception as exc:
            logger.debug("recover: sidecar read failed for %s: %s", entry_id, exc)

    restored_name = _restore_filename(os.path.basename(file_path), sidecar_original)
    os.makedirs(staging_dir, exist_ok=True)
    target = _ensure_unique_path(os.path.join(staging_dir, restored_name))

    try:
        shutil.move(file_path, target)
    except OSError as exc:
        logger.error("recover: failed to move %s -> %s: %s", file_path, target, exc)
        return None

    if sidecar_path and os.path.isfile(sidecar_path):
        try:
            os.remove(sidecar_path)
        except OSError as exc:
            logger.warning("recover: failed to remove sidecar %s: %s", sidecar_path, exc)

    return target


def _ensure_unique_path(target: str) -> str:
    """Append `_(2)`, `_(3)`, ... before the extension when target exists."""
    if not os.path.exists(target):
        return target
    base, ext = os.path.splitext(target)
    counter = 2
    while True:
        candidate = f"{base}_({counter}){ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1
