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

``files`` deletes the job's folder itself. Asking the client is not enough:
SABnzbd's ``del_files`` only deletes the files of FAILED jobs, so a completed
job's whole release stayed on disk. The folder comes from the client's own
report for the job (read before the job is removed) and is deleted only when
it provably belongs to that job: named after it (or SAB's ``<job>.N`` for a
repeat name), a real folder and not a link, and not one of the caller's
protected roots or a folder holding one. Anything else is left alone.

Pure at import time (the video download config imports ``normalize`` on the
sidebar status path): the client package and the logger load only when a job
is actually removed.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Iterable, Optional

OFF = "off"
HISTORY = "history"
FILES = "files"
MODES = (OFF, HISTORY, FILES)


def normalize(value: Any) -> str:
    """One of MODES; anything unknown (unset, blank, a typo) is ``off``."""
    mode = str(value or "").strip().lower()
    return mode if mode in MODES else OFF


def matches_job_name(folder_name: Any, job_name: Any) -> bool:
    """True when a folder is named after the job: the job name itself, or
    SAB's ``<job>.1``, ``<job>.2``, ... for a job whose name already has a
    folder in the completed folder. Case-insensitive."""
    folder = str(folder_name or "").strip().casefold()
    job = str(job_name or "").strip().casefold()
    if not folder or not job:
        return False
    if folder == job:
        return True
    return folder.startswith(job + ".") and folder[len(job) + 1:].isdigit()


def job_folder(save_path: Any, job_name: Any, *,
               resolve: Optional[Callable[[str], str]] = None,
               protected: Iterable[Any] = (),
               libraries: Iterable[Any] = ()) -> Optional[str]:
    """The job's own folder from what the client reported for it, or None.

    ``save_path`` is the client's final path for the job (a folder, or for a
    single-file job the file itself, whose folder is its parent -- also once
    the importer has taken that file and only its leftovers remain). ``resolve``
    maps the client's path to this process's mount. None unless the folder is
    named after the job (see ``matches_job_name``), is a real folder (not a
    link), and is neither a protected root nor a folder that holds one -- so a
    category folder that holds jobs without folders of their own, the
    download folder and the library are never returned. Nor anything INSIDE a
    ``libraries`` root (the library and transfer folders): a client that
    completes straight into the library, with a naming template that files
    the album under the job's own name, would have the imported album itself
    deleted."""
    if not save_path or not job_name:
        return None
    try:
        path = resolve(str(save_path)) if resolve else str(save_path)
        if not path:
            return None
        path = os.path.abspath(str(path))
        if (os.path.isfile(path) and not os.path.islink(path)) or not os.path.lexists(path):
            path = os.path.dirname(path)     # the file, or where it was
        if not os.path.isdir(path) or os.path.islink(path):
            return None
        if not matches_job_name(os.path.basename(path), job_name):
            return None
        real = os.path.realpath(path)
        if len([part for part in real.split(os.sep) if part]) < 2:
            return None
        for root in protected or ():
            if not root:
                continue
            guard = os.path.realpath(os.path.abspath(str(root)))
            try:
                if os.path.commonpath([real, guard]) == real:
                    return None          # the root itself, or a folder holding it
            except ValueError:
                continue                 # different drives: cannot hold it
        for root in libraries or ():
            if not root:
                continue
            library = os.path.realpath(os.path.abspath(str(root)))
            try:
                common = os.path.commonpath([real, library])
            except ValueError:
                continue                 # different drives
            if common in (real, library):
                return None              # holds the library, or lies inside it
        return path
    except (OSError, TypeError, ValueError):
        return None


def _reported_path_gone(save_path: Any, resolve: Optional[Callable[[str], str]]) -> bool:
    """True when the client named a final path and nothing is there any more
    (the importer took the file and removed the folder it left empty)."""
    if not save_path:
        return False
    try:
        path = resolve(str(save_path)) if resolve else str(save_path)
        return bool(path) and not os.path.lexists(os.path.abspath(str(path)))
    except (OSError, TypeError, ValueError):
        return False


def _delete_job_folder(path: str, what: str, logger) -> bool:
    import shutil
    try:
        shutil.rmtree(path)
    except OSError as e:
        logger.warning("usenet: couldn't delete the folder of imported job %s (%s): %s",
                       what, os.path.basename(path), e)
        return False
    logger.info("usenet: deleted the folder of imported job %s: %s", what,
                os.path.basename(path))
    return True


def remove_imported_job(job_id: Any, mode: Any, *, run: Callable[[Any], Any],
                        adapter: Optional[Any] = None, label: str = "",
                        resolve: Optional[Callable[[str], str]] = None,
                        protected: Iterable[Any] = (),
                        libraries: Iterable[Any] = ()) -> bool:
    """Take an imported usenet job out of the client per ``mode``.

    ``run`` executes the adapter's coroutine (each side passes the runner it
    already uses for client calls). ``adapter`` defaults to the active usenet
    client. For ``files`` the job's folder is read from the client BEFORE the
    removal (the report goes with the job) and deleted only after the client
    confirmed it; ``resolve`` and ``protected`` are as in ``job_folder``.
    Returns True only when the client confirmed the removal."""
    mode = normalize(mode)
    if mode == OFF or not job_id:
        return False
    from utils.logging_config import get_logger
    logger = get_logger("usenet.remove_completed")
    what = "%s (%s)" % (label, job_id) if label else str(job_id)
    folder = None
    gone = False                 # the client named a path that no longer exists
    try:
        if adapter is None:
            from core.usenet_clients import get_active_adapter
            adapter = get_active_adapter()
        if adapter is None:
            return False
        if mode == FILES:
            try:
                status = run(adapter.get_status(str(job_id)))
            except Exception:  # noqa: BLE001 - no report means no folder to delete
                status = None
            if status is not None:
                reported = getattr(status, "save_path", None)
                folder = job_folder(reported, getattr(status, "name", None),
                                    resolve=resolve, protected=protected,
                                    libraries=libraries)
                gone = folder is None and _reported_path_gone(reported, resolve)
        removed = bool(run(adapter.remove(str(job_id), delete_files=(mode == FILES))))
    except Exception:  # noqa: BLE001 - external client boundary; the import already landed
        logger.warning("usenet: couldn't remove imported job %s from the client", what,
                       exc_info=True)
        return False
    if not removed:
        logger.info("usenet: the client did not remove imported job %s", what)
        return False
    logger.info("usenet: removed imported job %s from the client (%s)", what,
                "and its leftover files" if mode == FILES else "history only")
    if mode == FILES:
        if folder is None and gone:
            # the importer already took the file and removed the emptied folder
            logger.info("usenet: nothing left of the folder of imported job %s", what)
        elif folder is None:
            logger.info("usenet: kept the folder of imported job %s -- the client's report "
                        "did not name a folder of its own", what)
        elif os.path.isdir(folder):
            _delete_job_folder(folder, what, logger)
    return removed
