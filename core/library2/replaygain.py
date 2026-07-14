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


def _present_track_files(conn, album_id: int) -> List[Tuple[int, str, str]]:
    """(track_id, title, path) for the album's tracks that own a primary file."""
    track_rows = conn.execute(
        "SELECT id, title FROM lib2_tracks WHERE album_id=? "
        "ORDER BY disc_number, track_number, id",
        (album_id,),
    ).fetchall()
    if not track_rows:
        return []
    from core.library2.track_files import primary_file_rows

    files = primary_file_rows(conn, [int(r["id"]) for r in track_rows])
    out: List[Tuple[int, str, str]] = []
    for row in track_rows:
        file_row = files.get(int(row["id"]))
        path = file_row["path"] if file_row and file_row.get("path") else None
        if not path:
            continue
        if file_row.get("file_state") == "missing_confirmed":
            continue
        out.append((int(row["id"]), row["title"] or path, path))
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
    analyzed: List[Tuple[str, float, float]] = []  # (path, track_gain_db, peak)
    for index, (_track_id, title, stored_path) in enumerate(entries, start=1):
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
        analyzed.append((resolved, RG_REFERENCE_LUFS - lufs, peak_dbfs))

    album_gain_db: Optional[float] = None
    album_peak_dbfs: Optional[float] = None
    if lufs_values:
        album_gain_db = RG_REFERENCE_LUFS - (sum(lufs_values) / len(lufs_values))
        album_peak_dbfs = max(peak_values)
    stats["album_gain_db"] = album_gain_db

    # Pass 2: write track + album tags for everything that analyzed.
    from core.metadata.common import get_file_lock

    for path, track_gain_db, peak_dbfs in analyzed:
        try:
            with get_file_lock(path):
                write(path, track_gain_db, peak_dbfs, album_gain_db, album_peak_dbfs)
            stats["analyzed"] += 1
        except Exception as e:  # noqa: BLE001
            stats["failed"] += 1
            stats["errors"].append({"track": path, "error": str(e)})

    logger.info(
        "Library v2 ReplayGain album %s: %d analyzed, %d failed (album gain %s)",
        album_id, stats["analyzed"], stats["failed"], album_gain_db,
    )
    return stats


__all__ = ["analyze_album_replaygain"]
