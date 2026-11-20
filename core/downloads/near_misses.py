"""Near-misses: quarantined files a task left behind on its way to a result.

A download that fails verification is quarantined and the task tries its
next candidate. When a later candidate imports fine, the earlier files are
noise in Review: the track is in the library. And an automatic quality
upgrade that finds nothing good enough should never ask anyone to review
anything: the file it would have replaced is still there.

Cleared entries are not deleted. The file and its sidecar move to the
deleted-files folder (the Downloads recycle bin), where restoring puts them
back in Review.

Settings (under ``post_processing.``, beside the retry settings):
  * ``clear_near_misses_on_success`` (default on): a task that imports clears
    the near-misses it quarantined on the way.
  * ``review_auto_reject_days`` (default 0, off): Review items older than this
    are moved to the recycle bin.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("downloads.near_misses")

TASK_KEY = "_near_miss_entries"
RECYCLE_SOURCE = "near_miss"
REJECT_SOURCE = "review_auto_reject"

# intent origins that a person asked for: their near-misses still go to Review
_PERSON_ORIGINS = ("manual_grab", "scoped_automatic_search")


def remember(task: Dict[str, Any], entry_id: Optional[str]) -> None:
    """Note a quarantined entry on its task before the task moves on."""
    if not entry_id:
        return
    seen = task.get(TASK_KEY)
    if not isinstance(seen, list):
        seen = []
    if entry_id not in seen:
        seen.append(str(entry_id))
    task[TASK_KEY] = seen


def is_automatic_upgrade(task: Dict[str, Any], batch: Optional[Dict[str, Any]]) -> bool:
    """A quality upgrade nobody clicked: an upgrade intent on a task the
    automatic Wishlist run started."""
    try:
        from core.imports.upgrade_intent import CONTEXT_KEY, is_upgrade_intent
    except Exception:  # noqa: BLE001
        return False
    intent = task.get(CONTEXT_KEY)
    if not is_upgrade_intent(intent):
        return False
    if str(getattr(intent, "origin", "")) in _PERSON_ORIGINS:
        return False
    return bool((batch or {}).get("auto_initiated"))


def _setting(config_manager: Any, key: str, default: Any) -> Any:
    if config_manager is None:
        try:
            from core.settings import config_manager as cfg
            config_manager = cfg
        except Exception:  # noqa: BLE001
            return default
    try:
        value = config_manager.get(key, default)
    except Exception:  # noqa: BLE001
        return default
    return default if value is None else value


def take_entries_to_clear(task: Dict[str, Any], batch: Optional[Dict[str, Any]], success: bool,
                          config_manager: Any = None) -> List[str]:
    """The entries a finished task's near-misses leave for clearing, taken
    off the task (so a second completion call finds none). Call under
    tasks_lock with the live task dict."""
    if success:
        if _setting(config_manager, "post_processing.clear_near_misses_on_success", True) is False:
            return []
        return list(task.pop(TASK_KEY, None) or [])
    if not is_automatic_upgrade(task, batch):
        return []
    # an automatic upgrade that ended without a file: nothing goes to Review,
    # the current file stays. the last quarantine is a near-miss too
    ids = list(task.pop(TASK_KEY, None) or [])
    last = task.pop("quarantine_entry_id", None)
    if last and last not in ids:
        ids.append(str(last))
    return ids


def quarantine_dir(config_manager: Any = None) -> str:
    from core.imports.paths import docker_resolve_path
    return os.path.join(
        docker_resolve_path(_setting(config_manager, "soulseek.download_path", "./downloads")),
        "ss_quarantine")


def transfer_folder(config_manager: Any = None) -> str:
    from core.imports.paths import docker_resolve_path
    return docker_resolve_path(_setting(config_manager, "soulseek.transfer_path", "./Transfer"))


def recycle_entries(entry_ids: Iterable[str], *, qdir: str, transfer: str,
                    source: str = RECYCLE_SOURCE) -> int:
    """Move each entry's file and sidecar to the recycle bin. Returns how
    many entries moved. Never raises; an entry already gone (approved or
    deleted meanwhile) is skipped."""
    from core.imports.quarantine import _resolve_entry_paths
    from core.library.deleted_quarantine import quarantine_mover
    move = quarantine_mover(transfer, source)
    moved = 0
    for entry_id in entry_ids or []:
        try:
            file_path, sidecar_path = _resolve_entry_paths(qdir, str(entry_id))
        except Exception as e:  # noqa: BLE001
            logger.debug("near-miss %s lookup failed: %s", entry_id, e)
            continue
        if not file_path:
            continue
        try:
            move(file_path)
            if sidecar_path and os.path.isfile(sidecar_path):
                move(sidecar_path)
            moved += 1
        except Exception as e:  # noqa: BLE001 - one stuck file leaves the rest
            logger.warning("Could not move near-miss %s to the recycle bin: %s", entry_id, e)
    return moved


def clear_after_task(entry_ids: List[str], config_manager: Any = None) -> int:
    """Recycle a finished task's near-misses (outside tasks_lock)."""
    if not entry_ids:
        return 0
    moved = recycle_entries(entry_ids, qdir=quarantine_dir(config_manager),
                            transfer=transfer_folder(config_manager))
    if moved:
        logger.info("Moved %d near-miss file%s to the recycle bin (the track imported, or an "
                    "automatic upgrade kept the current file)", moved, "" if moved == 1 else "s")
    return moved


def _entry_age_seconds(qdir: str, entry_id: str, now: float) -> Optional[float]:
    sidecar = os.path.join(qdir, f"{entry_id}.json")
    try:
        with open(sidecar, encoding="utf-8") as fh:
            stamp = (json.load(fh) or {}).get("timestamp")
        if stamp:
            return now - datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except Exception as e:  # noqa: BLE001 - fall back to the sidecar's own age
        logger.debug("review item %s has no readable timestamp: %s", entry_id, e)
    try:
        return now - os.path.getmtime(sidecar)
    except OSError:
        return None


def auto_reject_old(config_manager: Any = None, *, now: Optional[float] = None,
                    qdir: Optional[str] = None, transfer: Optional[str] = None) -> int:
    """Recycle Review items older than ``post_processing.review_auto_reject_days``.
    0 (the default) keeps everything. Returns how many moved."""
    try:
        days = float(_setting(config_manager, "post_processing.review_auto_reject_days", 0) or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return 0
    qdir = qdir or quarantine_dir(config_manager)
    if not os.path.isdir(qdir):
        return 0
    from core.imports.quarantine import entry_id_from_quarantined_filename
    now = now or time.time()
    cutoff = days * 86400
    old = []
    for name in os.listdir(qdir):
        if not name.endswith(".quarantined"):
            continue
        entry_id = entry_id_from_quarantined_filename(name)
        age = _entry_age_seconds(qdir, entry_id, now)
        if age is not None and age >= cutoff:
            old.append(entry_id)
    if not old:
        return 0
    moved = recycle_entries(old, qdir=qdir, transfer=transfer or transfer_folder(config_manager),
                            source=REJECT_SOURCE)
    if moved:
        logger.info("Review: moved %d item%s older than %g days to the recycle bin",
                    moved, "" if moved == 1 else "s", days)
    return moved


_last_reject_run = 0.0
REJECT_EVERY_SECONDS = 600


def auto_reject_old_throttled(config_manager: Any = None, **kwargs) -> int:
    """``auto_reject_old`` at most every ten minutes: the Review badge polls
    often, and a sweep reads every sidecar."""
    global _last_reject_run
    now = time.time()
    if now - _last_reject_run < REJECT_EVERY_SECONDS:
        return 0
    _last_reject_run = now
    try:
        return auto_reject_old(config_manager, now=now, **kwargs)
    except Exception as e:  # noqa: BLE001 - housekeeping never breaks the badge
        logger.debug("review auto-reject sweep failed: %s", e)
        return 0
