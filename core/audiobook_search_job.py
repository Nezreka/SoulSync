"""Background release searches, so a modal can show results as they land.

An audiobook search is slow by construction: up to three query variants, each
one a real fan-out to every configured indexer, then Soulseek after them. Run
as one blocking call it returns nothing at all until every source has finished,
which reads as a hang on the one screen where the user is waiting.

This is the video side's contract, not a new one. ``api/video/downloads.py``
exposes ``search/start`` and ``search/poll``: start kicks the work off and hands
back an id, poll returns the ranked rows so far, and the client re-renders the
whole list each tick. Audiobooks needed the job store that video gets for free
from slskd (which holds the in-flight search itself), because Prowlarr answers
one query at a time and the accumulating has to happen somewhere.

Every poll returns the WHOLE ranked pool, never a delta. Ranking is global: a
Soulseek folder with the right narrator has to be able to land above a torrent
with the wrong one that arrived two queries earlier, and appending would pin it
to the bottom forever.

Jobs are in-process and disposable. A restart loses them, which costs a user
one re-click on a search that was going to be re-run anyway.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_search_job")

# How long a finished job stays readable. Long enough for the last poll of a
# slow client, short enough that nothing accumulates.
JOB_TTL_SECONDS = 300

# A hard stop so one wedged indexer cannot leave a thread running forever.
JOB_MAX_SECONDS = 180

_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _prune() -> None:
    """Drop finished jobs nobody came back for. Called on every start."""
    cutoff = _now() - JOB_TTL_SECONDS
    with _lock:
        stale = [key for key, job in _jobs.items()
                 if job["complete"] and job["updated_at"] < cutoff]
        for key in stale:
            _jobs.pop(key, None)


def _publish(job_id: str, **fields: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.update(fields)
        job["updated_at"] = _now()


def _run_search(job_id: str, book: Dict[str, Any], narrator_mode: str, limit: int) -> None:
    """Drive both sources, publishing the ranked pool after each step."""
    from core.audiobook_release_search import (
        configured_chain,
        deduplicate,
        iter_prowlarr_releases,
        rank_releases,
    )

    started = _now()
    collected: List[Any] = []
    failures: List[str] = []
    chain = configured_chain()

    def republish(stage: str, complete: bool = False) -> None:
        ranked = rank_releases(deduplicate(collected), book, 0.5, narrator_mode)[:limit]
        _publish(job_id, stage=stage, releases=[r.to_dict() for r in ranked],
                 complete=complete)

    try:
        if {"torrent", "usenet"} & set(chain):
            try:
                for step in iter_prowlarr_releases(
                    book, limit=limit, narrator_mode=narrator_mode,
                ):
                    collected = list(step["releases"])
                    republish(f"Searching indexers — {step['query']}")
                    if _now() - started > JOB_MAX_SECONDS:
                        break
            except Exception as exc:                        # noqa: BLE001
                logger.warning("Prowlarr leg of job %s failed: %s", job_id, exc)
                failures.append(str(exc))

        want_soulseek = False
        if "soulseek" in chain:
            try:
                from core.audiobook_soulseek import is_available
                want_soulseek = is_available()
            except Exception as exc:                        # noqa: BLE001
                logger.debug("Could not check Soulseek availability: %s", exc)

        if want_soulseek and _now() - started <= JOB_MAX_SECONDS:
            republish("Asking Soulseek peers")
            try:
                from core.audiobook_soulseek import search as search_soulseek
                collected.extend(search_soulseek(
                    book, limit=limit, narrator_mode=narrator_mode,
                ))
            except Exception as exc:                        # noqa: BLE001
                logger.warning("Soulseek leg of job %s failed: %s", job_id, exc)
                failures.append(str(exc))

        # Same rule the blocking search uses: a source that ERRORED did not
        # answer "no", so an empty pool plus a failure is an error, not an
        # empty shelf. Anything found still wins.
        error = ""
        if failures and not collected:
            error = failures[0]

        republish("", complete=True)
        if error:
            _publish(job_id, error=error)

    except Exception as exc:                                # noqa: BLE001
        logger.warning("Audiobook search job %s died: %s", job_id, exc, exc_info=True)
        _publish(job_id, complete=True, error=str(exc))
    finally:
        # Belt and braces. A thread that exits without setting this leaves the
        # modal spinning on a search that will never report, and `except
        # Exception` does not catch every way out of a thread.
        with _lock:
            job = _jobs.get(job_id)
            if job is not None and not job["complete"]:
                job["complete"] = True
                job["updated_at"] = _now()
                if not job["error"]:
                    job["error"] = "The search stopped unexpectedly."


def start(book: Dict[str, Any], narrator_mode: str = "exact", limit: int = 25) -> str:
    """Begin a search and return its id. Never blocks on the search itself."""
    _prune()
    job_id = uuid.uuid4().hex

    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "asin": str(book.get("asin") or ""),
            "stage": "Starting the search",
            "releases": [],
            "complete": False,
            "error": "",
            "created_at": _now(),
            "updated_at": _now(),
        }

    # Daemon so a wedged indexer can never hold the process open at shutdown.
    thread = threading.Thread(
        target=_run_search, args=(job_id, book, narrator_mode, limit),
        name=f"audiobook-search-{job_id[:8]}", daemon=True,
    )
    thread.start()
    return job_id


def poll(job_id: str) -> Optional[Dict[str, Any]]:
    """The ranked pool so far, or None when the id is unknown or expired."""
    with _lock:
        job = _jobs.get(str(job_id or ""))
        if job is None:
            return None
        return {
            "id": job["id"],
            "stage": job["stage"],
            "releases": list(job["releases"]),
            "complete": job["complete"],
            "error": job["error"],
        }


def forget(job_id: str) -> bool:
    """Drop a job the user walked away from."""
    with _lock:
        return _jobs.pop(str(job_id or ""), None) is not None
