"""Remove Completed: what happens to a usenet job once SoulSync has imported it.

SoulSync never took a finished job out of the usenet client. Every grab stayed
in SABnzbd/NZBGet history forever, and on the music side the whole release
stayed on disk too: album and track grabs COPY their audio out of the client's
folder and never touch it again. The video importer already reclaims its own
file, so there it is mostly the history entry (plus any nfo or sample).

One setting per side, default off, because managing someone's download client
is opt-in (same rule as the seed sweep):

  · ``off``     : leave the job in the client (the old behavior)
  · ``history`` : remove the job from the client's history, files untouched
  · ``files``   : remove it and delete what is left in its folder

Callers decide WHEN a job counts as imported; this module only knows how to
ask the client. A failed removal is logged and never raises: the import has
already succeeded, and a job left in the client is the old behavior.

Pure at import time (the video download config imports ``normalize`` on the
sidebar status path): the client package and the logger load only when a job
is actually removed.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

OFF = "off"
HISTORY = "history"
FILES = "files"
MODES = (OFF, HISTORY, FILES)


def normalize(value: Any) -> str:
    """One of MODES; anything unknown (unset, blank, a typo) is ``off``."""
    mode = str(value or "").strip().lower()
    return mode if mode in MODES else OFF


def remove_imported_job(job_id: Any, mode: Any, *, run: Callable[[Any], Any],
                        adapter: Optional[Any] = None, label: str = "") -> bool:
    """Take an imported usenet job out of the client per ``mode``.

    ``run`` executes the adapter's coroutine (each side passes the runner it
    already uses for client calls). ``adapter`` defaults to the active usenet
    client. Returns True only when the client confirmed the removal."""
    mode = normalize(mode)
    if mode == OFF or not job_id:
        return False
    from utils.logging_config import get_logger
    logger = get_logger("usenet.remove_completed")
    what = "%s (%s)" % (label, job_id) if label else str(job_id)
    try:
        if adapter is None:
            from core.usenet_clients import get_active_adapter
            adapter = get_active_adapter()
        if adapter is None:
            return False
        removed = bool(run(adapter.remove(str(job_id), delete_files=(mode == FILES))))
    except Exception:  # noqa: BLE001 - external client boundary; the import already landed
        logger.warning("usenet: couldn't remove imported job %s from the client", what,
                       exc_info=True)
        return False
    if removed:
        logger.info("usenet: removed imported job %s from the client (%s)", what,
                    "and its leftover files" if mode == FILES else "history only")
    else:
        logger.info("usenet: the client did not remove imported job %s", what)
    return removed
