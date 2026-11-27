"""the marker tables in video.db (schema v79)."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

from .audio import Print, from_blob, to_blob

ANALYZER_VERSION = 1


@contextmanager
def connect(db):
    conn = db._get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_marker(r) -> Dict[str, Any]:
    try:
        extra = json.loads(r[6]) if r[6] else {}
    except ValueError:
        extra = {}
    return {"kind": r[0], "start": float(r[1]), "end": float(r[2]), "source": r[3],
            "confidence": r[4], "edited_by": r[5], "extra": extra}


def segments(db, file_id: int) -> List[Dict[str, Any]]:
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT kind, start_s, end_s, source, confidence, edited_by, extra FROM video_segments "
            "WHERE media_file_id = ? ORDER BY start_s", (int(file_id),)).fetchall()
    return [_row_marker(r) for r in rows]


def suppressed(db, file_id: int) -> List[str]:
    with connect(db) as conn:
        return [r[0] for r in conn.execute(
            "SELECT kind FROM video_segment_suppressions WHERE media_file_id = ?", (int(file_id),))]


def replace_source(db, file_id: int, source: str, markers: Iterable[Dict[str, Any]]) -> bool:
    """the file's markers from one source are these now. True when that
    changed anything."""
    new = []
    seen = set()
    for m in markers:
        if m["kind"] in seen:
            continue        # one per kind and source (the table's key)
        seen.add(m["kind"])
        new.append((m["kind"], round(float(m["start"]), 3), round(float(m["end"]), 3),
                    m.get("confidence"), json.dumps(m.get("extra") or {}, sort_keys=True)))
    with connect(db) as conn:
        old = conn.execute(
            "SELECT kind, start_s, end_s, confidence, COALESCE(extra, '{}') FROM video_segments "
            "WHERE media_file_id = ? AND source = ? ORDER BY kind", (int(file_id), source)).fetchall()
        if sorted(tuple(r) for r in old) == sorted(new):
            return False
        conn.execute("DELETE FROM video_segments WHERE media_file_id = ? AND source = ?",
                     (int(file_id), source))
        conn.executemany(
            "INSERT INTO video_segments (media_file_id, kind, start_s, end_s, source, confidence, extra) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(int(file_id), k, s, e, source, c, x) for k, s, e, c, x in new])
    return True


def set_user(db, file_id: int, kind: str, start: float, end: float, profile_id: Optional[int],
             extra: Optional[Dict[str, Any]] = None) -> None:
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO video_segments (media_file_id, kind, start_s, end_s, source, edited_by, extra) "
            "VALUES (?, ?, ?, ?, 'user', ?, ?) ON CONFLICT(media_file_id, kind, source) DO UPDATE SET "
            "start_s = excluded.start_s, end_s = excluded.end_s, edited_by = excluded.edited_by, "
            "extra = excluded.extra, created_at = CURRENT_TIMESTAMP",
            (int(file_id), kind, round(float(start), 3), round(float(end), 3), profile_id,
             json.dumps(extra or {}, sort_keys=True)))


def remove(db, file_id: int, kind: str) -> None:
    """no marker of this kind for the file: the hand-set one goes and every
    automatic one is held back from now on"""
    with connect(db) as conn:
        conn.execute("DELETE FROM video_segments WHERE media_file_id = ? AND kind = ? AND source = 'user'",
                     (int(file_id), kind))
        conn.execute("INSERT OR IGNORE INTO video_segment_suppressions (media_file_id, kind) VALUES (?, ?)",
                     (int(file_id), kind))


def reset(db, file_id: int, kind: str) -> None:
    """back to what soulsync, the sidecar or the server found"""
    with connect(db) as conn:
        conn.execute("DELETE FROM video_segments WHERE media_file_id = ? AND kind = ? AND source = 'user'",
                     (int(file_id), kind))
        conn.execute("DELETE FROM video_segment_suppressions WHERE media_file_id = ? AND kind = ?",
                     (int(file_id), kind))


# ── fingerprints ─────────────────────────────────────────────────────────

def save_print(db, file_id: int, region: str, p: Print) -> None:
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO video_audio_fingerprints (media_file_id, region, algo, offset_s, step_s, fp, "
            "envelope, analyzer_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(media_file_id, region) DO UPDATE SET algo = excluded.algo, "
            "offset_s = excluded.offset_s, step_s = excluded.step_s, fp = excluded.fp, "
            "envelope = excluded.envelope, analyzer_version = excluded.analyzer_version, "
            "created_at = CURRENT_TIMESTAMP",
            (int(file_id), region, p.algo, float(p.offset), float(p.step), to_blob(p.fp, "<u4"),
             to_blob(p.env, "u1"), ANALYZER_VERSION))


def prints(db, file_ids: Iterable[int]) -> Dict[int, Dict[str, Print]]:
    ids = [int(i) for i in file_ids]
    out: Dict[int, Dict[str, Print]] = {}
    if not ids:
        return out
    with connect(db) as conn:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(
                    "SELECT media_file_id, region, algo, offset_s, step_s, fp, envelope "  # noqa: S608
                    f"FROM video_audio_fingerprints WHERE media_file_id IN ({marks}) "
                    "AND analyzer_version = ?", (*chunk, ANALYZER_VERSION)):
                fp = from_blob(r[5], "<u4")
                if fp is None:
                    continue
                out.setdefault(int(r[0]), {})[r[1]] = Print(
                    fp.astype("uint32"), float(r[3]), float(r[4]), r[2], from_blob(r[6], "u1"))
    return out


# ── the job's own state ──────────────────────────────────────────────────

def state(db, file_id: int, task: str = "markers") -> Optional[Dict[str, Any]]:
    with connect(db) as conn:
        r = conn.execute(
            "SELECT status, attempts, priority, last_error, result, file_sig, analyzer_version, analyzed_at "
            "FROM video_analysis_state WHERE media_file_id = ? AND task = ?", (int(file_id), task)).fetchone()
    if not r:
        return None
    try:
        result = json.loads(r[4]) if r[4] else {}
    except ValueError:
        result = {}
    return {"status": r[0], "attempts": r[1], "priority": r[2], "last_error": r[3], "result": result,
            "file_sig": r[5], "analyzer_version": r[6], "analyzed_at": r[7]}


def results(db, file_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    ids = [int(i) for i in file_ids]
    out: Dict[int, Dict[str, Any]] = {}
    with connect(db) as conn:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            for fid, raw in conn.execute(
                    f"SELECT media_file_id, result FROM video_analysis_state WHERE task = 'markers' "  # noqa: S608
                    f"AND status = 'done' AND media_file_id IN ({marks})", chunk):
                try:
                    out[int(fid)] = json.loads(raw) if raw else {}
                except ValueError:
                    out[int(fid)] = {}
    return out


def mark(db, file_id: int, task: str, status: str, *, error: Optional[str] = None,
         result: Optional[Dict[str, Any]] = None, file_sig: Optional[str] = None,
         count_attempt: bool = False) -> None:
    now = time.time()
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO video_analysis_state (media_file_id, task, status, attempts, last_error, result, "
            "file_sig, analyzer_version, heartbeat, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "CASE WHEN ? IN ('done', 'failed') THEN CURRENT_TIMESTAMP END) "
            "ON CONFLICT(media_file_id, task) DO UPDATE SET status = excluded.status, "
            "attempts = video_analysis_state.attempts + ?, last_error = excluded.last_error, "
            "result = COALESCE(excluded.result, video_analysis_state.result), "
            "file_sig = COALESCE(excluded.file_sig, video_analysis_state.file_sig), "
            "analyzer_version = excluded.analyzer_version, heartbeat = excluded.heartbeat, "
            "analyzed_at = COALESCE(excluded.analyzed_at, video_analysis_state.analyzed_at), "
            "priority = CASE WHEN excluded.status IN ('done', 'failed') THEN 0 "
            "ELSE video_analysis_state.priority END",
            (int(file_id), task, status, 1 if count_attempt else 0, error,
             json.dumps(result) if result is not None else None, file_sig, ANALYZER_VERSION, now,
             status, 1 if count_attempt else 0))


def bump(db, file_ids: Iterable[int], priority: int, *, redo: bool = False) -> int:
    """put files at the front of the queue (``redo``: even ones already done,
    which re-runs the match; fingerprints still current are reused)"""
    ids = [int(i) for i in file_ids]
    n = 0
    with connect(db) as conn:
        for fid in ids:
            cur = conn.execute(
                "INSERT INTO video_analysis_state (media_file_id, task, status, priority) "
                "VALUES (?, 'markers', 'pending', ?) ON CONFLICT(media_file_id, task) DO UPDATE SET "
                "priority = MAX(video_analysis_state.priority, excluded.priority), "
                "status = CASE WHEN ? OR video_analysis_state.status = 'pending' THEN 'pending' "
                "ELSE video_analysis_state.status END, "
                "attempts = CASE WHEN ? THEN 0 ELSE video_analysis_state.attempts END "
                "WHERE ? OR video_analysis_state.status = 'pending'",
                (fid, int(priority), 1 if redo else 0, 1 if redo else 0, 1 if redo else 0))
            n += cur.rowcount or 0
    return n


def recover(db) -> int:
    """a job that was running when the server stopped goes back in the queue"""
    with connect(db) as conn:
        return conn.execute("UPDATE video_analysis_state SET status = 'pending' "
                            "WHERE status = 'running'").rowcount or 0


# ── .edl files soulsync wrote ────────────────────────────────────────────

def written_sidecar(db, file_id: int) -> Optional[Dict[str, Any]]:
    with connect(db) as conn:
        r = conn.execute("SELECT path, size_bytes, mtime FROM video_marker_sidecars WHERE media_file_id = ?",
                         (int(file_id),)).fetchone()
    return {"path": r[0], "size": r[1], "mtime": r[2]} if r else None


def note_sidecar(db, file_id: int, path: str, size: int, mtime: float) -> None:
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO video_marker_sidecars (media_file_id, path, size_bytes, mtime) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(media_file_id) DO UPDATE SET path = excluded.path, size_bytes = excluded.size_bytes, "
            "mtime = excluded.mtime, written_at = CURRENT_TIMESTAMP",
            (int(file_id), path, int(size), float(mtime)))


def forget_sidecar(db, file_id: int) -> None:
    with connect(db) as conn:
        conn.execute("DELETE FROM video_marker_sidecars WHERE media_file_id = ?", (int(file_id),))
