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

Every stage is best effort: the catalogue is already committed, and a provider
being unreachable must never turn a successful migration into a failed one.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.post_import")

ProgressCb = Optional[Callable[..., None]]


def run_post_import_precache(database: Any, config_manager: Any, *,
                             progress: ProgressCb = None) -> Dict[str, str]:
    """Resolve tracklists, then read file tags. Never raises.

    Returns one status per stage (``"ok"`` or the error text) so a caller can
    log or surface what did not complete.
    """
    results: Dict[str, str] = {}

    def _stage(name: str, run: Callable[[], Any]) -> None:
        if progress:
            progress(name, 0, 0)
        try:
            run()
            results[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - presentation data, not the catalogue
            logger.debug("Library v2 %s precache failed: %s", name, exc)
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
        conn = database._get_connection()
        try:
            refresh_artist_rollup(conn)
        finally:
            conn.close()

    # Tracklists first: they turn "3 of 12 tracks known" into real, monitorable
    # rows, which is what the user sees on the page. Tag reading is local file
    # I/O and only enriches rows that already exist.
    _stage("tracklists", _tracklists)
    _stage("tags", _tags)
    _stage("artist_rollup", _artist_rollup)
    return results


__all__ = ["run_post_import_precache"]
