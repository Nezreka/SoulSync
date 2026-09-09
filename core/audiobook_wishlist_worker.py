"""Background pass that keeps looking for wishlisted audiobooks.

A book someone wants is often not on any indexer the day they ask for it. The
wishlist exists so that "not yet" turns into "got it" without anyone having to
remember to search again.

Scheduling
----------
NOT here. The pass is driven by the shared automation engine as the
``audiobook_process_wishlist`` system automation, exactly as music drains its
wishlist through ``process_wishlist`` and video through
``video_process_movie_wishlist``.

This module first shipped with its own daemon thread and timer, which was a
mistake worth naming: an automation appears on the Automations page, can be
paused, rescheduled or run by hand, and obeys the master switch. A private
thread is none of those, and a user who wanted it to stop had nowhere to say so.

Pacing
------
Two separate limits, and both matter.

The automation decides how often a pass runs and takes only a handful of items
each time. Each item then has its own backoff, so one unfindable book cannot be
searched every pass forever while newer entries wait behind it.

Underneath both, every search still goes through the shared Prowlarr throttle —
the same budget the music and video sides spend. That is the limit that actually
protects the indexers, and this worker is deliberately quieter than it needs to
be so a long audiobook wishlist can never out-shout a music wishlist drain.
(Boulder, Aug 2026, on the music side doing exactly that: "it will search one
item on torrent, less than 5 seconds later is doing a different search".)

Isolation
---------
Nothing here reaches the music download batches, the music worker pool or the
music wishlist. Wanted books live in the audiobook database, releases are found
through the audiobook release search, and grabs go to the shared download client
under the audiobook category.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_wishlist_worker")

# The system automation that drives this. Named here so the module and the
# engine cannot disagree about which action_type owns the wishlist pass.
AUTOMATION_ACTION = "audiobook_process_wishlist"

# How many books one pass looks at. Small on purpose: a pass that searched the
# whole wishlist would put a hundred searches on the indexers in one burst.
# The INTERVAL is not here — the automation owns that.
DEFAULT_BATCH_SIZE = 5

# How long before one book is tried again. A book that is not out yet will not
# appear because it was asked for more often.
DEFAULT_RETRY_AFTER_SECONDS = 6 * 60 * 60


def _setting(key: str, default: Any) -> Any:
    try:
        from core.settings import config_manager
        value = config_manager.get(key, default)
        return default if value is None else value
    except Exception:                                       # noqa: BLE001
        return default


def retry_after_seconds() -> float:
    try:
        return max(60.0, float(_setting("audiobooks.wishlist_retry_after_seconds",
                                        DEFAULT_RETRY_AFTER_SECONDS)))
    except (TypeError, ValueError):
        return float(DEFAULT_RETRY_AFTER_SECONDS)


def batch_size() -> int:
    try:
        return max(1, min(25, int(_setting("audiobooks.wishlist_batch_size",
                                           DEFAULT_BATCH_SIZE))))
    except (TypeError, ValueError):
        return DEFAULT_BATCH_SIZE


def _book_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild the shape the release search expects from a stored wishlist row."""
    series = []
    if row.get("series_title"):
        series = [{"title": row["series_title"], "sequence": row.get("series_sequence") or ""}]
    return {
        "asin": row.get("asin"),
        "title": row.get("title"),
        "author_names": row.get("authors") or [],
        "narrator_names": row.get("narrators") or [],
        "runtime_minutes": row.get("runtime_minutes") or 0,
        "release_date": row.get("release_date") or "",
        "series": series,
    }


def process_one(row: Dict[str, Any], db: Any = None, auto_grab: bool = True) -> Dict[str, Any]:
    """Search for one wishlisted book, and grab it when something good turns up.

    Returns ``{asin, found, grabbed, error}``.

    Every outcome writes a status back, including "nothing found", because the
    attempt count is what drives the backoff — a pass that failed silently would
    leave the book due again immediately and turn the wishlist into a spin loop.
    """
    from core.audiobook_database import (
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_GRABBED,
        STATUS_SEARCHING,
        get_audiobook_db,
    )
    from core.audiobook_release_search import search_all_sources

    database = db if db is not None else get_audiobook_db()
    asin = str(row.get("asin") or "")
    outcome = {"asin": asin, "found": 0, "grabbed": False, "error": "", "owned": False}
    if not asin:
        return outcome

    # Already on disk — the usual reason is that it was imported by hand, or the
    # library scan found a copy. Searching indexers for a book we have is waste,
    # and grabbing it would be worse.
    try:
        if database.is_owned(asin):
            database.mark_wishlist_status(asin, STATUS_DONE)
            outcome["owned"] = True
            return outcome
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not check whether %s is owned: %s", asin, exc)

    book = _book_from_row(row)
    # Claimed before the search so a second pass cannot pick up the same book.
    database.mark_wishlist_status(asin, STATUS_SEARCHING)

    try:
        # The narrator choice is the listener's, made when they wished for the
        # book, and the automatic path must honour it exactly as the manual one
        # does — a book is always one narrator, never a mixture.
        releases = search_all_sources(
            book, limit=10, narrator_mode=row.get("narrator_mode") or "exact",
        )
    except Exception as exc:                                # noqa: BLE001
        logger.warning("Audiobook wishlist search failed for %s: %s", asin, exc)
        database.mark_wishlist_status(asin, STATUS_FAILED, error=str(exc), count_attempt=True)
        outcome["error"] = str(exc)
        return outcome

    outcome["found"] = len(releases)
    if not releases:
        database.mark_wishlist_status(
            asin, STATUS_FAILED, error="No releases found", count_attempt=True,
        )
        return outcome

    if not auto_grab:
        database.mark_wishlist_status(
            asin, STATUS_FAILED, error="Found, not grabbed", count_attempt=True,
        )
        return outcome

    from core.audiobook_grab import grab_release

    best = releases[0]
    result = grab_release(best)
    if result.get("ok"):
        # Recorded so the download monitor can follow it to completion. Without
        # this the worker would grab books that nobody ever files into the
        # library.
        ref = str(result.get("ref") or "")
        # Torrents and NZBs are one job, so the handle IS the ref. A Soulseek
        # folder is many transfers and carries its own.
        client_ref = str(result.get("client_ref") or ref)
        if ref:
            # An automatic grab belongs on the Downloads page just as much as a
            # manual one — a book appearing in the library with no card ever
            # having shown is indistinguishable from a bug.
            from core.audiobook_download_state import register_download

            register_download(
                task_id=ref,
                title=str(row.get("title") or ""),
                author=(row.get("authors") or [""])[0],
                series=str(row.get("series_title") or ""),
                artwork_url=str(row.get("cover_url") or ""),
                protocol=str(getattr(best, "protocol", "") or ""),
                size_bytes=int(getattr(best, "size_bytes", 0) or 0),
            )
            database.record_download(
                download_id=ref,
                asin=asin,
                title=str(row.get("title") or ""),
                source=str(getattr(best, "protocol", "") or ""),
                client_id=client_ref,
                release_title=str(getattr(best, "title", "") or ""),
                release_guid=str(getattr(best, "guid", "") or ""),
                indexer=str(getattr(best, "indexer", "") or ""),
                author=(row.get("authors") or [""])[0],
                bytes_total=int(getattr(best, "size_bytes", 0) or 0),
                # The wishlist row already holds everything the import needs, so
                # nothing has to be asked of the catalogue again later.
                book=book,
            )
        database.mark_wishlist_status(asin, STATUS_GRABBED, count_attempt=True)
        outcome["grabbed"] = True
        logger.info("Audiobook wishlist grabbed %s for %s", best.title, asin)
    else:
        error = str(result.get("error") or "Grab failed")
        database.mark_wishlist_status(asin, STATUS_FAILED, error=error, count_attempt=True)
        outcome["error"] = error
    return outcome


def run_pass(db: Any = None, limit: Optional[int] = None) -> Dict[str, Any]:
    """One sweep over the books that are due.

    Safe to call by hand — the "search now" button on the wishlist page runs
    exactly this, so the manual and automatic paths cannot drift apart.
    """
    from core.audiobook_database import get_audiobook_db

    database = db if db is not None else get_audiobook_db()
    summary = {"checked": 0, "found": 0, "grabbed": 0, "errors": 0, "freed": 0}

    # Self-healing, at the start of every pass rather than only at boot: a pass
    # that died mid-search leaves rows claimed as "searching", and the retry
    # query never looks at those again. Waiting for a restart to notice would
    # mean a book silently stops being searched for until someone reboots.
    try:
        summary["freed"] = database.reset_stale_searching()
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not free stale searching rows: %s", exc)

    # Every profile, not just the first. There is no request behind a timed
    # pass, so the profile has to come from the rows — and sweeping only
    # profile 1 meant a second person's wishlist was never searched at all.
    #
    # The batch size is PER PROFILE deliberately: it exists to pace indexer
    # load per pass, and one busy profile must not starve another of its turn.
    try:
        profiles = database.profiles_with_rows()
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not list audiobook profiles: %s", exc)
        profiles = [1]

    due = []
    for profile_id in profiles:
        try:
            due.extend(database.get_wishlist_due(
                profile_id=profile_id,
                retry_after_seconds=retry_after_seconds(),
                limit=limit if limit is not None else batch_size(),
            ))
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Could not read profile %s's audiobook wishlist: %s",
                           profile_id, exc)

    for row in due:
        result = process_one(row, db=database)
        summary["checked"] += 1
        summary["found"] += result["found"]
        summary["grabbed"] += 1 if result["grabbed"] else 0
        summary["errors"] += 1 if result["error"] else 0

    if summary["checked"]:
        logger.info("Audiobook wishlist pass: %s", summary)
    return summary


def schedule_status() -> Dict[str, Any]:
    """What the wishlist page can honestly say about this pass.

    Only the knobs this module actually owns. The SCHEDULE is not among them —
    it belongs to the ``audiobook_process_wishlist`` system automation, and the
    engine is constructed behind web_server, which this subsystem is not allowed
    to import. Rather than duplicate the interval here and let the page claim a
    cadence that has drifted from the real one, the page names the automation
    and points at the Automations page to change it.
    """
    return {
        "retry_after_seconds": retry_after_seconds(),
        "batch_size": batch_size(),
        "action_type": AUTOMATION_ACTION,
        "automation_name": "Auto-Process Audiobook Wishlist",
    }
