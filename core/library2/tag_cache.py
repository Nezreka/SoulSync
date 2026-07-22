"""Persist Library-v2 file-tag snapshots using the existing tag reader."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

from utils.logging_config import get_logger

from .status import EXPECTED_TAGS

logger = get_logger("library2.tag_cache")


def _present(value: Any) -> bool:
    return value not in (None, "", [], {}, False)


def normalized_tag_snapshot(file_tags: Dict[str, Any]) -> Dict[str, Any]:
    """Map ``core.tag_writer.read_file_tags`` output to the UI cache shape."""
    res = {
        "title": file_tags.get("title"),
        "artist": file_tags.get("artist"),
        "album": file_tags.get("album"),
        "albumartist": file_tags.get("album_artist"),
        "track_number": file_tags.get("track_number"),
        "disc_number": file_tags.get("disc_number"),
        "year": file_tags.get("year"),
        "genre": file_tags.get("genre"),
        "cover": bool(file_tags.get("has_cover_art")),
    }
    for k in (
        "lyrics",
        "replaygain_track_gain",
        "replaygain_track_peak",
        "replaygain_album_gain",
        "replaygain_album_peak",
    ):
        if file_tags.get(k) is not None:
            res[k] = file_tags[k]
    return res


def persist_tag_cache(conn, file_id: int, file_tags: Dict[str, Any]) -> bool:
    """Persist a successful read, or invalidate stale cache on read failure.

    JSON ``null`` is the explicit unknown sentinel for list caches. It makes
    ``compute_metadata_gaps`` fall back to DB knowledge rather than treating an
    unreadable file as either gap-free or as its previous stale snapshot.
    """
    if file_tags.get("error"):
        conn.execute(
            """UPDATE lib2_track_files
                  SET tags_json='{}', missing_tags_json='null',
                      metadata_gaps_json='null', updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (int(file_id),),
        )
        return False

    snapshot = normalized_tag_snapshot(file_tags)
    missing = [tag for tag in EXPECTED_TAGS if not _present(snapshot.get(tag))]
    conn.execute(
        """UPDATE lib2_track_files
              SET tags_json=?, missing_tags_json=?, metadata_gaps_json=?,
                  updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
        (
            json.dumps(snapshot, sort_keys=True),
            json.dumps(missing),
            json.dumps(missing),
            int(file_id),
        ),
    )
    return True


def read_tag_snapshot(path: str) -> Dict[str, Any]:
    """Read tags through the canonical engine without holding a DB handle."""
    from core.tag_writer import read_file_tags

    try:
        return read_file_tags(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc) or exc.__class__.__name__}


def read_and_persist_tag_cache(conn, file_id: int, path: str) -> bool:
    """Read through the canonical tag engine and persist its snapshot."""
    return persist_tag_cache(conn, file_id, read_tag_snapshot(path))


def _precache_max_workers(config_manager, default: int = 3) -> int:
    """Shared pool-size knob with ``core.auto_import_worker``/the artwork and
    tracklist precache stages — same config key, same default."""
    if config_manager is None:
        return default
    try:
        return max(1, int(config_manager.get("auto_import.max_workers", default)))
    except Exception:  # noqa: BLE001
        return default


def precache_tag_cache(database, config_manager, *, progress=None) -> Dict[str, int]:
    """Populate ``tags_json`` (has_replaygain/has_lyrics/etc.) for files the
    importer just seeded but never read.

    The importer only knows format/bitrate/size from the legacy DB (see
    ``core.library2.importer``) — it never opens a file, so ``tags_json``
    stays at its schema default ``'{}'`` and has_replaygain/has_lyrics read
    as False until a manual "Refresh & Scan" (``core.library2.scan.rescan_files``)
    probes them. This runs that same tag read right after import, bounded by
    a ``ThreadPoolExecutor`` (same pattern/config key as
    ``precache_all_artwork``/``precache_tracklists``) so a library of
    thousands of tracks doesn't block import on a serial file-by-file loop.
    Only never-scanned files (``tags_json`` still at its default) are
    touched, so re-running after a real scan is a no-op.
    """
    from core.library2.paths import resolve_lib2_path

    counts = {"scanned": 0, "updated": 0}
    try:
        conn = database._get_connection()
    except Exception:  # noqa: BLE001
        return counts
    try:
        rows = conn.execute(
            "SELECT id, path FROM lib2_track_files "
            "WHERE path IS NOT NULL AND path <> '' AND tags_json = '{}'"
        ).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.debug("tag cache precache query failed: %s", e)
        return counts
    finally:
        conn.close()

    pending = [(int(r["id"]), r["path"]) for r in rows]
    total = len(pending)
    progress_lock = threading.Lock()
    done = [0]
    if progress:
        progress("tags", 0, total)

    def _read_one(file_id: int, raw_path: str) -> bool:
        path = resolve_lib2_path(raw_path)
        if not path:
            return False
        try:
            thread_conn = database._get_connection()
        except Exception:  # noqa: BLE001
            return False
        try:
            updated = read_and_persist_tag_cache(thread_conn, file_id, path)
            thread_conn.commit()
            return updated
        except Exception as e:  # noqa: BLE001
            logger.debug("tag cache precache read failed (%s): %s", file_id, e)
            return False
        finally:
            thread_conn.close()

    try:
        if pending:
            max_workers = _precache_max_workers(config_manager)
            with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="Lib2TagCache"
            ) as executor:
                futures = {
                    executor.submit(_read_one, fid, p): fid for fid, p in pending
                }
                for future in as_completed(futures):
                    counts["scanned"] += 1
                    if future.result():
                        counts["updated"] += 1
                    with progress_lock:
                        done[0] += 1
                        if progress and (done[0] % 50 == 0 or done[0] == total):
                            progress("tags", done[0], total)
    except Exception as e:  # noqa: BLE001
        logger.debug("tag cache precache error: %s", e)
    if progress:
        progress("tags", total, total)
    logger.info("Library v2 tag-cache precache: %s", counts)
    return counts


__all__ = [
    "normalized_tag_snapshot",
    "persist_tag_cache",
    "precache_tag_cache",
    "read_and_persist_tag_cache",
    "read_tag_snapshot",
]
