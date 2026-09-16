"""Audiobook recycle bin — deleting a book moves it aside, not away.

Every path that destroys a book routes through :func:`discard`. The folder
moves into a hidden ``.deleted`` directory under the audiobook library root,
named ``<YYYYMMDD_HHMMSS>_<original>``, and the daily purge unlinks what has
sat there past ``recycle_keep_days``.

Hidden matters: the bin sits inside the folder the media server scans, so a
visible one would keep every deleted book in Audiobookshelf or Plex for the
whole keep window. ``.deleted`` is the music side's spelling for the same
problem.

A book is a FOLDER, not a file, which is the one real difference from the video
side's bin: a 45-hour book is ninety chapter files plus artwork and sidecars,
and half-deleting that is worse than not deleting it.

Age is read off the NAME STAMP, never mtime. Moving a folder keeps its original
mtime, so a book imported in 2019 would land in the bin already older than any
keep window and be purged on the spot — a zero-second undo window for exactly
the thing somebody most wants back. That lesson is the video bin's, learned
there first.

Deliberately a sibling of core/video/recycle.py rather than a call into it: the
audiobook side must not import core.video, which the isolation guards enforce.
The stamp format is kept identical so the two bins behave the same and a person
who has seen one recognises the other.

Failure discipline: if the move fails the book is LEFT WHERE IT IS and
``{"ok": False}`` comes back. discard never half-deletes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_recycle")

# Hidden, deliberately. The bin lives INSIDE the audiobook library root, which
# is the folder Audiobookshelf, Plex and Jellyfin are pointed at — so a visible
# folder means every book you delete keeps showing up in your media server for
# the whole keep window. A leading dot is what those scanners skip, and it is
# the spelling the music side already settled on for the same reason
# (core/repair_jobs/base.py: deleted_quarantine_root).
TRASH_DIRNAME = ".deleted"

# Where each entry came from. A book lives at <root>/Author/Series/Title, and
# the trash entry keeps only the last segment — so without this, restoring puts
# the book back at <root>/Title with the author and series folders gone. The
# video bin records the original path for exactly this reason.
MANIFEST_NAME = ".soulsync_recycle.json"

DEFAULT_KEEP_DAYS = 7

_STAMP_RE = re.compile(r"^(\d{8})_(\d{6})_")


def keep_days() -> float:
    """How long a deleted book stays recoverable. 0 disables the bin."""
    try:
        from core.settings import config_manager
        return max(0.0, float(config_manager.get(
            "audiobooks.recycle_keep_days", DEFAULT_KEEP_DAYS)))
    except Exception:                                       # noqa: BLE001
        return DEFAULT_KEEP_DAYS


def recycling_enabled() -> bool:
    """Whether deletes go to the bin at all."""
    try:
        from core.settings import config_manager
        return bool(config_manager.get("audiobooks.recycle_deletes", True))
    except Exception:                                       # noqa: BLE001
        return True


def trash_dir() -> str:
    """Where deleted books go: ``<audiobook library>/.deleted``."""
    from core.audiobook_organizer import library_root
    return os.path.join(str(library_root()), TRASH_DIRNAME)


def _manifest_read(trash: Path) -> Dict[str, Any]:
    """The bin's record of where each entry came from. Never raises."""
    try:
        with open(trash / MANIFEST_NAME, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _manifest_write(trash: Path, data: Dict[str, Any]) -> None:
    """Written via a temp file and renamed, so a crash cannot truncate it."""
    temporary = trash / (MANIFEST_NAME + ".tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
        os.replace(str(temporary), str(trash / MANIFEST_NAME))
    except OSError as exc:
        logger.warning("Could not write the recycle manifest: %s", exc)


def _manifest_record(trash: Path, entry: str, original: str, reason: str) -> None:
    data = _manifest_read(trash)
    data[entry] = {"original": original, "reason": reason, "at": time.time()}
    _manifest_write(trash, data)


def _manifest_forget(trash: Path, entry: str) -> None:
    data = _manifest_read(trash)
    if data.pop(entry, None) is not None:
        _manifest_write(trash, data)


def _stamp(now: Optional[float] = None) -> str:
    when = datetime.fromtimestamp(time.time() if now is None else now)
    return when.strftime("%Y%m%d_%H%M%S")


def entry_age_seconds(name: str, now: Optional[float] = None) -> Optional[float]:
    """How long ago this entry was recycled, or None when it is not ours.

    From the name stamp, never mtime — see the module docstring. No stamp means
    something else put it there, so it is left alone.
    """
    match = _STAMP_RE.match(str(name or ""))
    if not match:
        return None
    try:
        when = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").timestamp()
    except ValueError:
        return None
    return max(0.0, (time.time() if now is None else now) - when)


def discard(path: str, reason: str = "") -> Dict[str, Any]:
    """Move one book folder to the bin. ``{ok, moved_to, permanent, error}``.

    With the bin turned off, the selected book is deleted outright. A failed
    recycle move leaves the book in place and reports an error to the caller.
    """
    source = Path(str(path or ""))
    result: Dict[str, Any] = {"ok": False, "moved_to": "", "permanent": False, "error": ""}

    if not source.exists():
        # Already gone is the outcome the caller wanted. Reporting failure here
        # makes a cleanup pass retry something that is finished.
        result.update(ok=True, error="")
        return result

    if not recycling_enabled():
        return _delete_outright(source, result)

    try:
        destination_root = Path(trash_dir())
        destination_root.mkdir(parents=True, exist_ok=True)

        # Two books deleted in the same second with the same folder name — a
        # series where every volume is "Book One" under a different author —
        # would otherwise collide, and shutil.move puts a directory INSIDE an
        # existing one rather than failing. That silently buries the first book
        # inside the second.
        stamp = _stamp()
        target = destination_root / f"{stamp}_{source.name}"
        attempt = 2
        while target.exists():
            target = destination_root / f"{stamp}_({attempt})_{source.name}"
            attempt += 1

        original = str(source.resolve())
        shutil.move(str(source), str(target))
        # Recorded AFTER the move succeeds, so the manifest never claims to
        # hold something that is still in the library.
        _manifest_record(destination_root, target.name, original, reason)
        result.update(ok=True, moved_to=str(target))
        logger.info("Recycled %s%s", source.name, f" ({reason})" if reason else "")
    except OSError as exc:
        # LEFT WHERE IT IS. A failed move is usually a share that blinked, and
        # turning that into a permanent delete would destroy the very book the
        # bin exists to protect. The caller sees ok=False and can retry.
        logger.warning("Could not recycle %s, leaving it in place: %s", source, exc)
        result["error"] = str(exc)
        return result

    # Housekeeping, never a failure: the scheduled purge is the one that
    # matters, this just keeps the bin tidy for anyone deleting a few books.
    try:
        purge_old()
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Opportunistic recycle purge failed: %s", exc)

    return result


def _delete_outright(source: Path, result: Dict[str, Any]) -> Dict[str, Any]:
    try:
        shutil.rmtree(str(source)) if source.is_dir() else source.unlink()
        result.update(ok=True, permanent=True)
        logger.info("Deleted %s permanently", source.name)
    except OSError as exc:
        # Left in place on purpose: never half-delete a book.
        result["error"] = str(exc)
        logger.warning("Could not delete %s: %s", source, exc)
    return result


def list_bin() -> List[Dict[str, Any]]:
    """What is currently recoverable, newest first."""
    root = Path(trash_dir())
    if not root.is_dir():
        return []
    manifest = _manifest_read(root)
    entries = []
    for child in root.iterdir():
        age = entry_age_seconds(child.name)
        if age is None:
            continue
        recorded = manifest.get(child.name) or {}
        entries.append({
            "name": child.name,
            "path": str(child),
            # The name it had before it was recycled.
            "original": _STAMP_RE.sub("", child.name),
            # And where it will go back to, which is not the same thing.
            "original_path": str(recorded.get("original") or ""),
            "reason": str(recorded.get("reason") or ""),
            "age_days": round(age / 86400, 1),
        })
    return sorted(entries, key=lambda e: e["age_days"])


def restore(name: str) -> Dict[str, Any]:
    """Put a recycled book back under the library root."""
    root = Path(trash_dir())
    entry = root / str(name or "")
    result = {"ok": False, "restored_to": "", "error": ""}

    if not str(name or "") or entry_age_seconds(entry.name) is None or not entry.exists():
        result["error"] = "That is not something this bin put here."
        return result

    from core.audiobook_organizer import library_root

    # Back exactly where it came from, folder structure and all. The entry name
    # holds only the last segment, so a book from
    # <root>/Author/Series/Title would otherwise reappear at <root>/Title.
    recorded = str((_manifest_read(root).get(entry.name) or {}).get("original") or "")
    target = Path(recorded) if recorded else \
        Path(str(library_root())) / _STAMP_RE.sub("", entry.name)

    if target.exists():
        result["error"] = f"{target.name} is already back in your library."
        return result
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry), str(target))
        _manifest_forget(root, entry.name)
        result.update(ok=True, restored_to=str(target))
        return result
    except OSError as exc:
        result["error"] = str(exc)
        return result


def purge_entry(name: str) -> Dict[str, Any]:
    """Erase one recycled book now, without waiting for the keep window."""
    root = Path(trash_dir())
    entry = root / str(name or "")
    result = {"ok": False, "freed_bytes": 0, "error": ""}

    if not str(name or "") or entry_age_seconds(entry.name) is None or not entry.exists():
        result["error"] = "That is not something this bin put here."
        return result

    size = _folder_size(entry)
    try:
        shutil.rmtree(str(entry)) if entry.is_dir() else entry.unlink()
        _manifest_forget(root, entry.name)
        result.update(ok=True, freed_bytes=size)
    except OSError as exc:
        result["error"] = str(exc)
    return result


def empty_bin() -> Dict[str, Any]:
    """Erase everything the bin holds, regardless of age.

    Deliberately separate from purge_old: this is a person deciding, and the
    keep window is not their business at that point. Still only touches what
    carries our stamp.
    """
    summary = {"removed": 0, "freed_bytes": 0}
    for entry in list_bin():
        outcome = purge_entry(entry["name"])
        if outcome["ok"]:
            summary["removed"] += 1
            summary["freed_bytes"] += outcome["freed_bytes"]
    return summary


def restore_all() -> Dict[str, Any]:
    """Put everything back. Reports what could not go."""
    summary = {"restored": 0, "failed": 0}
    for entry in list_bin():
        if restore(entry["name"])["ok"]:
            summary["restored"] += 1
        else:
            summary["failed"] += 1
    return summary


def purge_old(days: Optional[float] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Unlink bin entries past the keep window. ``{removed, freed_bytes, kept}``.

    Nothing without our name stamp is touched: another tool's files living in
    the same folder are not ours to delete.
    """
    summary = {"removed": 0, "freed_bytes": 0, "kept": 0}
    root = Path(trash_dir())
    if not root.is_dir():
        return summary

    window = (keep_days() if days is None else float(days)) * 86400
    if window <= 0:
        # 0 means the bin is off; purging everything on that basis would delete
        # what was recycled while it was still on.
        return summary

    for child in list(root.iterdir()):
        age = entry_age_seconds(child.name, now)
        if age is None:
            continue  # the manifest itself, or something we did not put here
        if age < window:
            summary["kept"] += 1
            continue
        size = _folder_size(child)
        try:
            if child.is_dir():
                shutil.rmtree(str(child))
            else:
                child.unlink()
            summary["removed"] += 1
            summary["freed_bytes"] += size
            _manifest_forget(root, child.name)
            logger.info("Purged %s from the audiobook recycle bin", child.name)
        except OSError as exc:
            logger.warning("Could not purge %s: %s", child, exc)

    return summary


def _folder_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError:
        return 0
