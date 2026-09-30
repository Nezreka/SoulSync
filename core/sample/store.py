"""Sample Studio persistence — analysis rows + peaks-file cache.

The ``sample_analysis`` table is created by MusicDatabase._initialize_database
(CREATE TABLE IF NOT EXISTS, same as every other table); column additions ride
the same PRAGMA/ALTER pattern. Peaks JSON lives on disk next to the database
(``<db_dir>/sample-studio/peaks/``) — in Docker that is /app/data/sample-studio,
on the persistent volume.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from database.music_database import get_database
from utils.logging_config import get_logger

from .analyze import ANALYZER_VERSION
from .fx import RenderFx, fx_from_row

_FX_COLUMNS = ("normalize", "fade_ms", "reverse", "space", "delay_json")

logger = get_logger("sample.store")


def sample_data_dir() -> str:
    """Directory for derived Sample Studio artifacts (peaks JSON, later chops)."""
    db = get_database()
    db_dir = os.path.dirname(os.path.abspath(str(db.database_path)))
    path = os.path.join(db_dir, "sample-studio")
    os.makedirs(path, exist_ok=True)
    return path


def source_signature(path: Optional[str]) -> Optional[str]:
    """size + mtime of an audio file. changes when the file gets replaced
    (an upgrade, a re-tag, a new rip), which is when cached results go stale."""
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{st.st_size}:{st.st_mtime_ns}"


def _sig_tag(sig: Optional[str]) -> str:
    import hashlib

    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:10] if sig else ""


def peaks_path(track_id: int, buckets: int = 1500, stem: str | None = None,
               sig: Optional[str] = None) -> str:
    """cache file for one waveform. the source signature is part of the name,
    so a replaced file never serves the old file's waveform."""
    d = os.path.join(sample_data_dir(), "peaks")
    os.makedirs(d, exist_ok=True)
    suffix = f"_{stem}" if stem else ""
    tag = f"_{_sig_tag(sig)}" if sig else ""
    return os.path.join(d, f"{int(track_id)}_{int(buckets)}{suffix}{tag}.json")


def drop_stale_peaks(track_id: int, buckets: int, stem: str | None, keep: str) -> None:
    """remove older cache files for the same waveform once a new one is written."""
    d = os.path.dirname(keep)
    suffix = f"_{stem}" if stem else ""
    base = f"{int(track_id)}_{int(buckets)}{suffix}"
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        if not name.endswith(".json") or os.path.join(d, name) == keep:
            continue
        stem_part = name[: -len(".json")]
        # exact legacy name, or base + a 10-hex signature tag. never a longer
        # stem name that happens to share the prefix (1_1500 vs 1_1500_drums)
        tail = stem_part[len(base):]
        if stem_part.startswith(base) and (tail == "" or (len(tail) == 11 and tail[0] == "_"
                                                          and all(ch in "0123456789abcdef" for ch in tail[1:]))):
            try:
                os.unlink(os.path.join(d, name))
            except OSError:
                pass


def get_analysis(track_id: int) -> Optional[Dict[str, Any]]:
    """Return the cached analysis row, or None when the track was never analyzed."""
    db = get_database()
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM sample_analysis WHERE track_id = ?",
            (int(track_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    keys = set(row.keys())
    key = None
    if "key_name" in keys and row["key_name"]:
        key = {"name": row["key_name"], "confidence": float(row["key_confidence"] or 0)}
    return {
        "track_id": row["track_id"],
        "bpm": row["bpm"],
        "onsets": json.loads(row["onsets_json"] or "[]"),
        "duration_s": row["duration_s"],
        "analyzed_at": row["analyzed_at"],
        "analyzer_version": row["analyzer_version"],
        "key": key,
        "source_sig": row["source_sig"] if "source_sig" in keys else None,
    }


def is_current(track_id: int, row: Optional[Dict[str, Any]] = None) -> bool:
    """True when the row was made by the current analyzer from the file that's
    on disk now. a replaced file (upgrade, new rip) makes it stale.

    when the file can't be reached we can't tell, so the row stands.
    """
    if row is None:
        row = get_analysis(track_id)
    if not row or int(row.get("analyzer_version") or 0) < ANALYZER_VERSION:
        return False
    from .worker import track_source

    _, sig = track_source(track_id)
    return sig is None or row.get("source_sig") == sig


def save_analysis(track_id: int, result: Dict[str, Any], source_sig: Optional[str] = None) -> None:
    """Upsert an analyze_track() result. Idempotent by track_id."""
    key = result.get("key") or {}
    db = get_database()
    conn = db._get_connection()
    try:
        conn.execute(
            """INSERT INTO sample_analysis
                   (track_id, bpm, onsets_json, duration_s, analyzed_at, analyzer_version,
                    key_name, key_confidence, source_sig)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(track_id) DO UPDATE SET
                   bpm = excluded.bpm,
                   onsets_json = excluded.onsets_json,
                   duration_s = excluded.duration_s,
                   analyzed_at = excluded.analyzed_at,
                   analyzer_version = excluded.analyzer_version,
                   key_name = excluded.key_name,
                   key_confidence = excluded.key_confidence,
                   source_sig = excluded.source_sig""",
            (
                int(track_id),
                float(result.get("bpm") or 0),
                json.dumps(result.get("onsets") or []),
                float(result.get("duration_s") or 0),
                time.time(),
                int(result.get("analyzer_version") or ANALYZER_VERSION),
                key.get("name"),
                float(key["confidence"]) if key.get("confidence") is not None else None,
                source_sig,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_track_file_path(track_id: int) -> Optional[str]:
    """file_path for a library track, or None when the id is unknown."""
    db = get_database()
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT file_path FROM tracks WHERE id = ?", (int(track_id),)).fetchone()
    finally:
        conn.close()
    if row is None or not row["file_path"]:
        return None
    return str(row["file_path"])


# ── Stems (Phase 4) ──────────────────────────────────────────────────────

def stems_dir() -> str:
    d = os.path.join(sample_data_dir(), "stems")
    os.makedirs(d, exist_ok=True)
    return d


def save_stems(track_id: int, stem_paths: Dict[str, str], backend: str,
               method: str = "demucs", source_sig: Optional[str] = None) -> None:
    """Upsert one row per output of `method`. Idempotent per (track_id, stem)."""
    from .stems import METHOD_STEMS, SEPARATOR_VERSION

    db = get_database()
    conn = db._get_connection()
    try:
        for stem in METHOD_STEMS[method]:
            conn.execute(
                """INSERT INTO sample_stems
                       (track_id, stem, file_path, status, backend,
                        separator_version, created_at, source_sig)
                   VALUES (?, ?, ?, 'done', ?, ?, ?, ?)
                   ON CONFLICT(track_id, stem) DO UPDATE SET
                       file_path = excluded.file_path,
                       status = 'done',
                       backend = excluded.backend,
                       separator_version = excluded.separator_version,
                       created_at = excluded.created_at,
                       source_sig = excluded.source_sig""",
                (
                    int(track_id),
                    stem,
                    str(stem_paths.get(stem) or ""),
                    str(backend),
                    SEPARATOR_VERSION,
                    time.time(),
                    source_sig,
                ),
            )
        conn.commit()
    finally:
        conn.close()


_UNSET = object()


def get_stems(track_id: int, method: str = "demucs", source_sig: Any = _UNSET) -> Optional[Dict[str, Any]]:
    """{'stems': {name: file_path}, 'backend': ...} when every output of
    `method` exists on disk and was cut from the file that's there now."""
    from .stems import METHOD_STEMS, SEPARATOR_VERSION

    wanted = METHOD_STEMS.get(method)
    if not wanted:
        return None
    db = get_database()
    conn = db._get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM sample_stems WHERE track_id = ?",
            (int(track_id),),
        ).fetchall()
    finally:
        conn.close()
    by_stem = {r["stem"]: r for r in rows}
    if not all(s in by_stem for s in wanted):
        return None
    if any(int(by_stem[s]["separator_version"] or 0) < SEPARATOR_VERSION for s in wanted):
        return None
    if any(not by_stem[s]["file_path"] or not os.path.isfile(by_stem[s]["file_path"]) for s in wanted):
        return None
    if source_sig is _UNSET:
        from .worker import track_source

        _, source_sig = track_source(track_id)
    if source_sig is not None:
        for s in wanted:
            row_sig = by_stem[s]["source_sig"] if "source_sig" in by_stem[s].keys() else None
            # rows from before signatures existed are trusted, a mismatch isn't
            if row_sig is not None and row_sig != source_sig:
                return None
    return {
        "stems": {s: str(by_stem[s]["file_path"]) for s in wanted},
        "backend": by_stem[wanted[0]]["backend"],
    }


def stems_complete(track_id: int, method: str = "demucs") -> bool:
    return get_stems(track_id, method) is not None


def stem_file_path(track_id: int, stem: str) -> Optional[str]:
    from .stems import method_for_stem

    method = method_for_stem(stem)
    if method is None:
        return None
    info = get_stems(track_id, method)
    if not info:
        return None
    return info["stems"].get(stem)


# ── Stash ─────────────────────────────────────────────────────────────

def previews_dir() -> str:
    d = os.path.join(sample_data_dir(), "previews")
    os.makedirs(d, exist_ok=True)
    return d


def _row_to_entry(row) -> Dict[str, Any]:
    # rows may predate the Phase 4 `stem` / Phase 6 `folder` columns on DBs
    # initialized before the tolerant ALTERs ran — .get-style access via
    # keys() keeps reads safe.
    keys = set(row.keys())
    return {
        "id": row["id"],
        "name": row["name"],
        "tags": json.loads(row["tags_json"] or "[]"),
        "track_id": row["track_id"],
        "track_title": row["track_title"],
        "artist_name": row["artist_name"],
        "start_s": row["start_s"],
        "end_s": row["end_s"],
        "pitch_st": row["pitch_st"],
        "target_bpm": row["target_bpm"],
        "format": row["format"],
        "file_path": row["file_path"],
        "created_at": row["created_at"],
        "stem": row["stem"] if "stem" in keys else None,
        "folder": row["folder"] if "folder" in keys else None,
        **fx_from_row({k: row[k] for k in _FX_COLUMNS if k in keys}).as_entry_fields(),
    }


_STASH_SELECT = """
    SELECT s.id, s.name, s.tags_json, s.track_id,
           COALESCE(t.title, '') AS track_title,
           COALESCE(a.name, '') AS artist_name,
           s.start_s, s.end_s, s.pitch_st, s.target_bpm,
           s.format, s.file_path, s.created_at, s.stem, s.folder,
           s.normalize, s.fade_ms, s.reverse, s.space, s.delay_json
    FROM sample_stash s
    LEFT JOIN tracks t ON t.id = s.track_id
    LEFT JOIN artists a ON a.id = t.artist_id
"""


def get_track_metadata(track_id: int) -> Dict[str, str]:
    """title/artist/album for a library track (for templates + tags)."""
    db = get_database()
    conn = db._get_connection()
    try:
        row = conn.execute(
            """SELECT t.title AS title, a.name AS artist, al.title AS album
               FROM tracks t
               LEFT JOIN artists a ON a.id = t.artist_id
               LEFT JOIN albums al ON al.id = t.album_id
               WHERE t.id = ?""",
            (int(track_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"title": "", "artist": "", "album": ""}
    return {
        "title": str(row["title"] or ""),
        "artist": str(row["artist"] or ""),
        "album": str(row["album"] or ""),
    }


def create_stash_entry(
    name: str,
    tags: list,
    track_id: int,
    start_s: float,
    end_s: float,
    pitch_st: float,
    target_bpm: Optional[float],
    format: str,
    file_path: str,
    stem: Optional[str] = None,
    folder: Optional[str] = None,
    fx: Optional[RenderFx] = None,
) -> Dict[str, Any]:
    """Insert a stash row (file + bookmark + fx recipe). Returns the full entry."""
    fx = fx or RenderFx()
    db = get_database()
    conn = db._get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO sample_stash
                   (name, tags_json, track_id, start_s, end_s, pitch_st,
                    target_bpm, format, file_path, created_at, stem, folder,
                    normalize, fade_ms, reverse, space, delay_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                json.dumps([str(t) for t in (tags or [])]),
                int(track_id),
                float(start_s),
                float(end_s),
                float(pitch_st or 0),
                float(target_bpm) if target_bpm else None,
                format,
                file_path,
                time.time(),
                stem,
                folder,
                "peak" if fx.normalize else None,
                float(fx.fade_ms),
                1 if fx.reverse else 0,
                fx.space,
                json.dumps(fx.delay.as_dict()) if fx.delay else None,
            ),
        )
        entry_id = cur.lastrowid
        conn.commit()
        row = conn.execute(_STASH_SELECT + "WHERE s.id = ?", (entry_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_entry(row)


def list_stash(limit: int = 500) -> list:
    """All stash entries, newest first."""
    db = get_database()
    conn = db._get_connection()
    try:
        rows = conn.execute(_STASH_SELECT + "ORDER BY s.id DESC LIMIT ?", (int(limit),)).fetchall()
    finally:
        conn.close()
    return [_row_to_entry(r) for r in rows]


def get_stash_entry(entry_id: int) -> Optional[Dict[str, Any]]:
    db = get_database()
    conn = db._get_connection()
    try:
        row = conn.execute(_STASH_SELECT + "WHERE s.id = ?", (int(entry_id),)).fetchone()
    finally:
        conn.close()
    return _row_to_entry(row) if row else None


def delete_stash_entry(entry_id: int) -> bool:
    """Delete the row AND the rendered file. Returns True when something was deleted."""
    entry = get_stash_entry(entry_id)
    if entry is None:
        return False
    db = get_database()
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM sample_stash WHERE id = ?", (int(entry_id),))
        conn.commit()
    finally:
        conn.close()
    try:
        if entry["file_path"] and os.path.isfile(entry["file_path"]):
            os.unlink(entry["file_path"])
    except OSError as e:
        logger.warning("could not delete chop file %s: %s", entry["file_path"], e)
    return True


def cleanup_previews(max_age_s: float = 3600) -> int:
    """Delete preview files older than max_age_s. Returns the count removed."""
    d = previews_dir()
    now = time.time()
    removed = 0
    for name in os.listdir(d):
        if not name.endswith(".wav"):
            continue
        path = os.path.join(d, name)
        try:
            if now - os.path.getmtime(path) > max_age_s:
                os.unlink(path)
                removed += 1
        except OSError:
            pass
    return removed
