"""Video recycle bin — deletes move to trash instead of unlinking (Radarr-style).

Every path that destroys a user's media file (upgrade-replace, YouTube
retention, dismissed imports — and future watched-cleanup / duplicate deletes)
routes through :func:`discard`. With ``recycle_deletes`` on (the default) the
file moves into an ``ss_recycle`` folder — the video sibling of the music
side's ``ss_quarantine`` convention — named ``<YYYYMMDD_HHMMSS>_<original>``,
and entries older than ``recycle_keep_days`` are purged opportunistically on
each discard.

Trash location: ``recycle_path`` when set; otherwise ``<library root>/ss_recycle``
for whichever configured library root (movies/tv/youtube) contains the file.
A file under NO known root falls back to a permanent delete (logged) — refusing
to delete would silently wedge retention/cleanup semantics and fill the disk.

Failure discipline: if the trash move itself fails, the file is LEFT IN PLACE
and ``{"ok": False}`` comes back — callers keep their existing "couldn't
delete → retry later / non-fatal" behaviour. discard never half-deletes.
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("video.recycle")

TRASH_DIRNAME = "ss_recycle"

_ROOT_SETTINGS = ("movies_path", "tv_path", "youtube_path")


def _library_roots(db) -> list:
    roots = []
    for key in _ROOT_SETTINGS:
        try:
            v = str(db.get_setting(key) or "").strip()
        except Exception:   # noqa: BLE001
            v = ""
        if v:
            roots.append(v)
    return roots


def _root_for(path: str, roots) -> Optional[str]:
    """The configured library root that contains ``path`` (deepest wins)."""
    ap = os.path.abspath(path)
    best = None
    for r in roots:
        ar = os.path.abspath(r)
        try:
            if os.path.commonpath([ap, ar]) == ar:
                if best is None or len(ar) > len(best):
                    best = ar
        except ValueError:   # different drives (Windows) → not an ancestor
            continue
    return best


def trash_dir_for(path: str, settings: Dict[str, Any], db) -> Optional[str]:
    """Where ``path`` would be recycled to, or None (→ permanent delete)."""
    override = str((settings or {}).get("recycle_path") or "").strip()
    if override:
        return override
    root = _root_for(path, _library_roots(db))
    return os.path.join(root, TRASH_DIRNAME) if root else None


def discard(path: str, settings: Dict[str, Any], db, *, reason: str = "") -> Dict[str, Any]:
    """Delete-or-recycle one file per the organization settings.

    Returns ``{"ok": bool, "recycled": bool, "trash_path": str|None}``.
    ``ok`` False = the file is still where it was (caller retries later)."""
    if not path or not os.path.exists(path):
        return {"ok": True, "recycled": False, "trash_path": None}   # already gone = done
    if not (settings or {}).get("recycle_deletes", True):
        return _unlink(path)
    trash = trash_dir_for(path, settings, db)
    if not trash:
        logger.warning("recycle: %s is under no configured library root — deleting permanently", path)
        return _unlink(path)
    try:
        os.makedirs(trash, exist_ok=True)
        stamp = datetime.fromtimestamp(time.time()).strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(trash, f"{stamp}_{os.path.basename(path)}")
        n = 2
        while os.path.exists(dest):   # same second, same name → suffix like quarantine
            dest = os.path.join(trash, f"{stamp}_({n})_{os.path.basename(path)}")
            n += 1
        shutil.move(path, dest)
        logger.info("recycled %s -> %s%s", path, dest, f" ({reason})" if reason else "")
    except OSError:
        logger.exception("recycle: could not move %s to trash — leaving it in place", path)
        return {"ok": False, "recycled": False, "trash_path": None}
    try:
        purge_old(settings, db, roots_hint=[trash])
    except Exception:   # noqa: BLE001 - the purge is housekeeping, never a failure
        logger.exception("recycle: purge pass failed")
    return {"ok": True, "recycled": True, "trash_path": dest}


def _unlink(path: str) -> Dict[str, Any]:
    try:
        os.remove(path)
        return {"ok": True, "recycled": False, "trash_path": None}
    except OSError:
        logger.exception("recycle: permanent delete failed for %s", path)
        return {"ok": False, "recycled": False, "trash_path": None}


def purge_old(settings: Dict[str, Any], db, roots_hint=None) -> int:
    """Unlink trash entries older than ``recycle_keep_days``. Returns count."""
    keep_days = int((settings or {}).get("recycle_keep_days") or 7)
    cutoff = time.time() - keep_days * 86400
    dirs = list(roots_hint or [])
    if not dirs:
        override = str((settings or {}).get("recycle_path") or "").strip()
        dirs = [override] if override else [
            os.path.join(r, TRASH_DIRNAME) for r in _library_roots(db)]
    removed = 0
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            fp = os.path.join(d, name)
            try:
                if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
                    removed += 1
            except OSError:   # noqa: PERF203 - per-file resilience
                logger.exception("recycle purge: could not remove %s", fp)
    if removed:
        logger.info("recycle purge: removed %d expired file(s)", removed)
    return removed


def discarder(db, settings: Dict[str, Any]) -> Callable[[str], Dict[str, Any]]:
    """A bound ``discard(path)`` for injection into pure pipelines (the importer's
    upgrade-replace, retention's delete seam)."""
    def _discard(path: str, *, reason: str = "") -> Dict[str, Any]:
        return discard(path, settings, db, reason=reason)
    return _discard
