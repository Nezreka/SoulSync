"""Album-level ReplayGain analysis for Library v2.

Reuses the proven analysis + tag-writing primitives from ``core/replaygain.py``
(the same ones the legacy Enhanced View uses) and only owns the Library-v2
orchestration: pick the album's present files, resolve them through the lib2
path resolver, run the two-pass album-gain computation, and write track- and
album-level ReplayGain tags under the shared per-file lock.

The FFmpeg analysis and mutagen write functions are injectable so the
orchestration is unit-testable without invoking real FFmpeg; production callers
use the defaults from ``core/replaygain.py``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.replaygain")

AnalyzeFn = Callable[[str], Tuple[float, float]]
WriteFn = Callable[..., bool]
ResolveFn = Callable[[str], Optional[str]]
ProgressFn = Callable[[int, int, str], None]


def _present_track_files(conn, album_id: int) -> List[Tuple[int, int, str, str]]:
    """(track_id, file_id, title, path) for tracks that own a primary file."""
    track_rows = conn.execute(
        "SELECT id, title FROM lib2_tracks WHERE album_id=? "
        "ORDER BY disc_number, track_number, id",
        (album_id,),
    ).fetchall()
    if not track_rows:
        return []
    from core.library2.track_files import primary_file_rows

    files = primary_file_rows(conn, [int(r["id"]) for r in track_rows])
    out: List[Tuple[int, int, str, str]] = []
    for row in track_rows:
        file_row = files.get(int(row["id"]))
        path = file_row["path"] if file_row and file_row.get("path") else None
        if not path:
            continue
        if file_row.get("file_state") == "missing_confirmed":
            continue
        out.append((int(row["id"]), int(file_row["id"]), row["title"] or path, path))
    return out


def analyze_album_replaygain(
    conn,
    album_id: int,
    *,
    config_manager: Any = None,
    analyze_fn: Optional[AnalyzeFn] = None,
    write_fn: Optional[WriteFn] = None,
    resolve_fn: Optional[ResolveFn] = None,
    progress: Optional[ProgressFn] = None,
) -> Dict[str, Any]:
    """Analyze every present file in an album and write ReplayGain tags.

    Returns ``{total, analyzed, failed, album_gain_db, errors}``. Never raises
    for a single-file failure — those are collected in ``errors``.
    """
    from core.replaygain import (
        RG_REFERENCE_LUFS,
        analyze_track as _default_analyze,
        write_replaygain_tags as _default_write,
    )

    analyze = analyze_fn or _default_analyze
    write = write_fn or _default_write
    if resolve_fn is not None:
        resolve = resolve_fn
    else:
        from core.library2.paths import resolve_lib2_path

        def resolve(path: str) -> Optional[str]:
            return resolve_lib2_path(path, config_manager)

    entries = _present_track_files(conn, album_id)
    total = len(entries)
    stats: Dict[str, Any] = {
        "total": total,
        "analyzed": 0,
        "failed": 0,
        "album_gain_db": None,
        "errors": [],
    }

    # Pass 1: analyze every resolvable file.
    lufs_values: List[float] = []
    peak_values: List[float] = []
    # (file_id, path, track_gain_db, peak) — file_id travels with the entry so
    # pass 2 never has to re-look-up the row by path (see G2: the stored path
    # is the media-server view, not the resolved filesystem path, so a
    # path-mapped setup would silently match nothing).
    analyzed: List[Tuple[int, str, float, float]] = []
    for index, (_track_id, file_id, title, stored_path) in enumerate(entries, start=1):
        if progress is not None:
            progress(index, total, title)
        resolved = resolve(stored_path)
        if not resolved:
            stats["failed"] += 1
            stats["errors"].append({"track": title, "error": "File not found on disk"})
            continue
        try:
            lufs, peak_dbfs = analyze(resolved)
        except Exception as e:  # noqa: BLE001 — collected, never fatal
            stats["failed"] += 1
            stats["errors"].append({"track": title, "error": str(e)})
            continue
        lufs_values.append(lufs)
        peak_values.append(peak_dbfs)
        analyzed.append((file_id, resolved, RG_REFERENCE_LUFS - lufs, peak_dbfs))

    album_gain_db: Optional[float] = None
    album_peak_dbfs: Optional[float] = None
    if lufs_values:
        album_gain_db = RG_REFERENCE_LUFS - (sum(lufs_values) / len(lufs_values))
        album_peak_dbfs = max(peak_values)
    stats["album_gain_db"] = album_gain_db

    # Pass 2: write track + album tags for everything that analyzed.
    from core.metadata.common import get_file_lock

    for file_id, path, track_gain_db, peak_dbfs in analyzed:
        try:
            with get_file_lock(path):
                write(path, track_gain_db, peak_dbfs, album_gain_db, album_peak_dbfs)
            stats["analyzed"] += 1

            # Rescan tags into database cache
            try:
                from core.library2.tag_cache import read_and_persist_tag_cache
                read_and_persist_tag_cache(conn, file_id, path)
                conn.commit()
            except Exception as scan_err:
                logger.debug("Failed to rescan file tags after album ReplayGain write for %s: %s", path, scan_err)
        except Exception as e:  # noqa: BLE001
            stats["failed"] += 1
            stats["errors"].append({"track": path, "error": str(e)})

    logger.info(
        "Library v2 ReplayGain album %s: %d analyzed, %d failed (album gain %s)",
        album_id, stats["analyzed"], stats["failed"], album_gain_db,
    )
    return stats


def analyze_track_replaygain(
    conn,
    track_id: int,
    *,
    config_manager: Any = None,
    analyze_fn: Optional[AnalyzeFn] = None,
    write_fn: Optional[WriteFn] = None,
    resolve_fn: Optional[ResolveFn] = None,
) -> Dict[str, Any]:
    """Analyze one track's file and write track-level ReplayGain tags.

    No album gain is computed (single track). Returns
    ``{analyzed: bool, track_gain_db: float|None, error: str|None}``.
    """
    from core.replaygain import RG_REFERENCE_LUFS, analyze_track as _default_analyze
    from core.replaygain import write_replaygain_tags as _default_write

    analyze = analyze_fn or _default_analyze
    write = write_fn or _default_write
    if resolve_fn is not None:
        resolve = resolve_fn
    else:
        from core.library2.paths import resolve_lib2_path

        def resolve(path: str) -> Optional[str]:
            return resolve_lib2_path(path, config_manager)

    from core.library2.track_files import primary_file_row

    file_row = primary_file_row(conn, track_id)
    stored_path = file_row["path"] if file_row and file_row.get("path") else None
    if not stored_path:
        return {"analyzed": False, "track_gain_db": None, "error": "Track has no file"}
    resolved = resolve(stored_path)
    if not resolved:
        return {"analyzed": False, "track_gain_db": None, "error": "File not found on disk"}
    try:
        lufs, peak_dbfs = analyze(resolved)
    except Exception as e:  # noqa: BLE001
        return {"analyzed": False, "track_gain_db": None, "error": str(e)}
    track_gain_db = RG_REFERENCE_LUFS - lufs
    from core.metadata.common import get_file_lock

    try:
        with get_file_lock(resolved):
            write(resolved, track_gain_db, peak_dbfs, None, None)

        # Rescan tags into database cache
        try:
            from core.library2.tag_cache import read_and_persist_tag_cache
            read_and_persist_tag_cache(conn, file_row["id"], resolved)
            conn.commit()
        except Exception as scan_err:
            logger.debug("Failed to rescan file tags after track ReplayGain write for %s: %s", resolved, scan_err)
    except Exception as e:  # noqa: BLE001
        return {"analyzed": False, "track_gain_db": None, "error": str(e)}
    return {"analyzed": True, "track_gain_db": track_gain_db, "error": None}


__all__ = ["analyze_album_replaygain", "analyze_track_replaygain"]
