"""The work that finishes a migration after the catalogue rows are committed.

Importing ``lib2_*`` rows is only the first half of making an upgraded
installation usable. Missing track titles still have to be resolved from cached
tracklists, and file tags still have to be read into the tag cache — without
those, a freshly migrated library shows albums whose tracks it cannot name and
a Tags tab that is empty for every release.

This used to live inline in the manual ``POST /api/library/v2/import`` handler
only, so an installation that upgraded and never opened the page (which is now
the normal case: the migration runs by itself) ended up with rows but none of
this. Both callers share this module so "the button" and "the automatic
migration" cannot drift apart again.

Presentation caches are best effort. Artist identity preparation precedes them;
if that stage cannot run, the migration barrier must remain in place.
"""

from __future__ import annotations

import threading
from contextlib import closing
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.post_import")

ProgressCb = Optional[Callable[..., None]]


class ArtistPreparationError(RuntimeError):
    """Identity preparation failed; do not release the migration barrier."""


def prepare_imported_artists(database, config_manager=None, *, progress=None):
    """Resolve/split credits synchronously before releasing enrichment workers."""
    try:
        return _prepare_imported_artists(database, config_manager, progress=progress)
    except Exception as exc:
        raise ArtistPreparationError(str(exc)) from exc


def _prepare_imported_artists(database, config_manager=None, *, progress=None):
    from core.library2.native_enrich import reconcile_unmapped_native_artists
    from core.library2.unmapped_trigger import DEFAULT_COOLDOWN_HOURS
    from core.library2.worker_queue import invalidate_pending_cache

    cooldown = (config_manager.get('library_v2.unmapped_reconcile.cooldown_hours',
                                   DEFAULT_COOLDOWN_HOURS)
                if config_manager is not None and hasattr(config_manager, 'get')
                else DEFAULT_COOLDOWN_HOURS)
    with closing(database._get_connection()) as conn:
        def report(current, total):
            conn.commit()
            if progress:
                progress('artists', current, total)
        if progress:
            progress('artists', 0, 0)
        # after the upgrade only the combined credits wait on this pass; the
        # enrichment workers identify single names themselves
        stats = reconcile_unmapped_native_artists(conn, progress=report,
                                                  cooldown_hours=cooldown,
                                                  combined_only=True)
        conn.commit()
    invalidate_pending_cache()
    logger.info('Pre-enrichment artist reconciliation: %s', stats)
    return stats


def run_post_import_precache(database: Any, config_manager: Any, *,
                             progress: ProgressCb = None) -> Dict[str, str]:
    """Prepare artist identities, resolve tracklists, then read file tags.

    Returns one status per stage (``"ok"`` or the error text) so a caller can
    log or surface what did not complete.
    """
    results: Dict[str, str] = {}
    prepare_imported_artists(database, config_manager, progress=progress)
    results['artists'] = 'ok'

    def _stage(name: str, run: Callable[[], Any]) -> None:
        if progress:
            progress(name, 0, 0)
        try:
            run()
            results[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - presentation data, not the catalogue
            logger.debug("Library %s precache failed: %s", name, exc)
            results[name] = str(exc)

    def _tracklists() -> None:
        from core.library2.completeness import precache_tracklists
        precache_tracklists(database, config_manager, progress=progress)

    def _tags() -> None:
        from core.library2.tag_cache import precache_tag_cache
        precache_tag_cache(database, config_manager, progress=progress)

    def _artist_rollup() -> None:
        """Warm the artist list's ordering key while we are already here.

        Otherwise the first person to sort by album or track count after a
        migration pays for the whole build on the request thread.
        """
        from core.library2.artist_rollup import refresh_artist_rollup
        with closing(database._get_connection()) as conn:
            refresh_artist_rollup(conn)

    # Tracklists first: they turn "3 of 12 tracks known" into real, monitorable
    # rows, which is what the user sees on the page. Tag reading is local file
    # I/O and only enriches rows that already exist.
    _stage("tracklists", _tracklists)
    _stage("tags", _tags)
    _stage("artist_rollup", _artist_rollup)
    return results


__all__ = ["run_post_import_precache", "prepare_imported_artists", "ArtistPreparationError",
           "full_preparation_pending", "start_background_preparation"]


_background_lock = threading.Lock()
_background_thread: Optional[threading.Thread] = None
# what the background preparation is doing, for the library banner: the
# library works while it runs, but pages are slower until it's done
# ``full``: the one-time preparation after an upgrade, not the credit pass a
# restart repeats (the banner only calls the first "finishing your upgrade")
_preparation_state: Dict[str, Any] = {"active": False, "full": False, "stage": None,
                                      "current": 0, "total": 0}


def preparation_state() -> Dict[str, Any]:
    """A copy of the background preparation's stage and counts."""
    return dict(_preparation_state)


# set when a full preparation starts, cleared when every stage finished: a
# restart in the middle used to run only the short credit pass from then on,
# so the tracklists and tags of an upgrade were never prepared
_FULL_PENDING = "post_import_full_preparation_pending"


def _set_full_pending(database: Any, pending: bool) -> None:
    try:
        with closing(database._get_connection()) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS lib2_upgrade_repairs "
                         "(name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            if pending:
                conn.execute("INSERT OR IGNORE INTO lib2_upgrade_repairs(name) VALUES(?)",
                             (_FULL_PENDING,))
            else:
                conn.execute("DELETE FROM lib2_upgrade_repairs WHERE name=?", (_FULL_PENDING,))
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - worst case the next start runs the short pass
        logger.debug("preparation marker not written: %s", exc)


def full_preparation_pending(database: Any) -> bool:
    """Whether a full post-upgrade preparation started and never finished."""
    try:
        with closing(database._get_connection()) as conn:
            return conn.execute("SELECT 1 FROM lib2_upgrade_repairs WHERE name=?",
                                (_FULL_PENDING,)).fetchone() is not None
    except Exception:  # noqa: BLE001 - no table: nothing pending
        return False


def start_background_preparation(database: Any, config_manager: Any, *, full: bool,
                                 after: Optional[Callable[[], Any]] = None
                                 ) -> Optional[threading.Thread]:
    """Run the post-upgrade preparation behind a library that already works.

    It used to run inside the upgrade barrier, so every worker stayed stopped
    and every library change answered 503 until it was done. On a 300k-track
    library that is the network pass over ~29k imported credits plus a
    tracklist for every album: hours, on every restart for the credit pass.
    None of it has to hold the library: the worker queues keep an unresolved
    combined credit out on their own (artist_credit_gate), whether or not
    this pass has reached it, and the caches are best effort.

    ``full`` runs everything a fresh migration needs (credits, tracklists,
    tags); otherwise only the credit pass a restart repeats. ``after`` runs
    when it finishes. One run at a time; a second call while one is alive is
    a no-op.
    """
    global _background_thread

    import time
    last = [0.0]

    def progress(stage, current=0, total=0, *_rest):
        _preparation_state.update(stage=stage, current=int(current or 0), total=int(total or 0))
        # one line every 30 s, so the log shows it moving without flooding
        now = time.monotonic()
        if total and current < total and now - last[0] < 30:
            return
        last[0] = now
        if total:
            logger.info("Library preparation: %s %s/%s (%d%%)", stage, f"{current:,}",
                        f"{total:,}", int(current * 100 / total))

    def run():
        _preparation_state.update(active=True, full=bool(full), stage=None, current=0, total=0)
        try:
            if full:
                _set_full_pending(database, True)
                results = run_post_import_precache(database, config_manager, progress=progress)
                if all(status == "ok" for status in results.values()):
                    _set_full_pending(database, False)
            else:
                prepare_imported_artists(database, config_manager, progress=progress)
        except Exception as exc:  # noqa: BLE001 - best effort, the next start retries
            logger.warning("Library background preparation stopped: %s", exc)
        finally:
            _preparation_state.update(active=False)
        if after is not None:
            try:
                after()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Library post-preparation step failed: %s", exc)

    with _background_lock:
        if _background_thread is not None and _background_thread.is_alive():
            return None
        _background_thread = threading.Thread(target=run, name="Lib2Preparation", daemon=True)
        _background_thread.start()
        return _background_thread
