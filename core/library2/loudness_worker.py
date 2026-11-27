"""measures the loudness of library files whose tags have none, in the
background, so every song can be levelled.

most of a library already has ReplayGain (SoulSync writes it on import, and
other taggers do too). those files are never measured: their tags are read
back and kept. the rest (a Lidarr import, a folder dropped in by hand) are
measured with ffmpeg ebur128, an album at a time so the album gets an album
gain too, and kept in lib2_track_analysis, the working copy.

analysis lives with the files when there's a standard for it: with "Apply
ReplayGain tags" on (post_processing.replaygain_enabled, the same switch the
import uses) a new measurement is also written into the file as ReplayGain
tags (R128 for Opus) through core.replaygain.write_replaygain_tags, and the
file's tags are read back into the tag cache, as a rescan would. off, nothing
is written. a hand-tagged file, or any file on an album with one, is never
written.

how it stays out of the way:
- one file at a time, ffmpeg on one thread under nice 19 / idle io
- a scan or a sync pauses it (core/background_pause), as it does the
  enrichment workers
- it walks the files by id with a cursor, so a 300k library is one cheap
  pass, then it sleeps; a failed file is tried 3 times, a day apart
- a song someone plays with no loudness jumps the queue (``bump``): its
  album is measured next, so the next play is levelled
- no ffmpeg, or 'loudness.measure_missing' off: it does nothing
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.loudness_worker")

CONFIG_KEY = "loudness.measure_missing"
MAX_ATTEMPTS = 3
RETRY_AFTER_S = 24 * 3600
IDLE_SLEEP_S = 30 * 60
BETWEEN_FILES_S = 0.5
MEASURE_TIMEOUT_S = 600

MeasureFn = Callable[[str], Dict[str, Any]]
WriteFn = Callable[..., bool]
WRITE_KEY = "post_processing.replaygain_enabled"


def _default_measure(path: str) -> Dict[str, Any]:
    from core.replaygain import measure_loudness
    return measure_loudness(path, timeout=MEASURE_TIMEOUT_S, low_priority=True)


def _default_resolve(stored: str) -> Optional[str]:
    from core.library2.paths import resolve_lib2_path
    return resolve_lib2_path(stored)


def _live_file_sql(f: str = "f") -> str:
    return (f"COALESCE({f}.file_state, 'active') NOT IN ('deleted', 'missing_confirmed') "
            f"AND COALESCE({f}.import_status, 'imported') = 'imported' AND {f}.track_id IS NOT NULL")


def _needs_sql(a: str = "a") -> str:
    """a file with nothing yet, or a failure due another go"""
    return (f"({a}.file_id IS NULL OR ({a}.source = 'failed' AND {a}.attempts < {MAX_ATTEMPTS} "
            f"AND COALESCE({a}.analyzed_at, 0) < :retry_before))")


def next_backlog_file(conn, after_id: int, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """the next file past ``after_id`` with no loudness anywhere: not in its
    tag cache, not measured. walks the primary key, so it's cheap"""
    from core.loudness import TABLE
    row = conn.execute(
        f"""SELECT f.id, f.track_id, t.album_id FROM lib2_track_files f
              JOIN lib2_tracks t ON t.id = f.track_id
              LEFT JOIN {TABLE} a ON a.file_id = f.id
             WHERE f.id > :after AND {_live_file_sql('f')} AND {_needs_sql('a')}
               AND json_extract(CASE WHEN json_valid(f.tags_json) THEN f.tags_json END,
                                '$.replaygain_track_gain') IS NULL
             ORDER BY f.id LIMIT 1""",
        {"after": int(after_id), "retry_before": (now or time.time()) - RETRY_AFTER_S}).fetchone()
    return dict(row) if row else None


def album_files(conn, album_id: int) -> List[Dict[str, Any]]:
    """each track's primary file on the album, for its album gain"""
    tracks = conn.execute(
        "SELECT id, duration FROM lib2_tracks WHERE album_id = ? ORDER BY disc_number, track_number, id",
        (int(album_id),)).fetchall()
    if not tracks:
        return []
    from core.library2.track_files import primary_file_rows
    files = primary_file_rows(conn, [int(r["id"]) for r in tracks])
    out = []
    for r in tracks:
        f = files.get(int(r["id"]))
        if not f or not f.get("path") or f.get("file_state") == "missing_confirmed":
            continue
        if (f.get("import_status") or "imported") != "imported":
            continue
        out.append({"file_id": int(f["id"]), "track_id": int(r["id"]), "path": f["path"],
                    "duration_s": (r["duration"] or 0) / 1000.0, "tags_json": f.get("tags_json")})
    return out


def measure_album(db: Any, album_id: int, *, extra_file_id: Optional[int] = None,
                  measure: Optional[MeasureFn] = None, resolve: Optional[Callable] = None,
                  reference: Optional[float] = None, should_stop: Callable[[], bool] = lambda: False,
                  pace: Callable[[], None] = lambda: None, write_tags: bool = False,
                  write: Optional[WriteFn] = None) -> Dict[str, int]:
    """measure what an album lacks, then give the album its album gain.

    ffmpeg runs with no connection held; each result is written in its own
    short transaction. ``write_tags``: what was measured here also goes into
    the files as ReplayGain tags (see the module notes). returns counts."""
    import json

    from core import loudness as L
    from core.replaygain import read_replaygain_tags

    measure = measure or _default_measure
    resolve = resolve or _default_resolve
    ref = L.reference_lufs() if reference is None else reference
    stats = {"measured": 0, "from_tags": 0, "failed": 0, "album": 0}
    # what this pass measured: file_id -> (local path, stored path, track id, gain, true peak dB)
    fresh: Dict[int, tuple] = {}

    with db._get_connection() as conn:
        files = album_files(conn, album_id)
        ids = [f["file_id"] for f in files]
        if extra_file_id is not None and int(extra_file_id) not in ids:
            row = conn.execute(
                "SELECT f.id, f.track_id, f.path, f.tags_json, t.duration FROM lib2_track_files f "
                "JOIN lib2_tracks t ON t.id = f.track_id WHERE f.id = ?", (int(extra_file_id),)).fetchone()
            if row and row["path"]:
                files.append({"file_id": int(row["id"]), "track_id": int(row["track_id"]), "path": row["path"],
                              "duration_s": (row["duration"] or 0) / 1000.0, "tags_json": row["tags_json"],
                              "not_on_album": True})
        known = L.for_files(conn, [f["file_id"] for f in files], reference=ref)
        failed_rows = {int(r[0]): int(r[1] or 0) for r in conn.execute(
            f"SELECT file_id, attempts FROM {L.TABLE} WHERE source = 'failed' AND file_id IN "
            f"(SELECT value FROM json_each(?))", (json.dumps([f["file_id"] for f in files]),))}

    # each file's integrated loudness, for the album's: measured, or worked
    # back from its track gain (loudness = reference - gain)
    lufs: Dict[int, float] = {}
    peaks: Dict[int, float] = {}
    for f in files:
        if should_stop():
            return stats
        fid = f["file_id"]
        have = known.get(fid)
        if have and have.get("track_gain") is not None:
            lufs[fid] = float(have["reference_lufs"]) - float(have["track_gain"])
            if have.get("track_peak"):
                peaks[fid] = float(have["track_peak"])
            continue
        if failed_rows.get(fid, 0) >= MAX_ATTEMPTS:
            continue
        local = resolve(f["path"])
        if not local:
            continue  # not reachable from here right now: the next pass tries again
        # the file's own tags first (a tag cache that never filled): never
        # measure what's already there
        try:
            tagged = L.from_tags(read_replaygain_tags(local))
        except Exception:  # noqa: BLE001
            tagged = {}
        if tagged.get("track_gain") is not None:
            with db._get_connection() as conn:
                L.remember_tags(conn, fid, tagged, ref)
                conn.commit()
            stats["from_tags"] += 1
            lufs[fid] = ref - tagged["track_gain"]
            if tagged.get("track_peak"):
                peaks[fid] = tagged["track_peak"]
            continue
        try:
            result = measure(local)
            integrated = float(result["integrated_lufs"])
            tp_db = float(result.get("true_peak_db") if result.get("true_peak_db") is not None else 0.0)
        except Exception as e:  # noqa: BLE001 - kept as a failure, tried again later
            with db._get_connection() as conn:
                conn.execute(
                    f"""INSERT INTO {L.TABLE} (file_id, track_id, analyzed_at, source, attempts, error)
                        VALUES (?, ?, ?, 'failed', 1, ?)
                        ON CONFLICT(file_id) DO UPDATE SET attempts = {L.TABLE}.attempts + 1,
                            analyzed_at = excluded.analyzed_at, error = excluded.error, source = 'failed'""",
                    (fid, f["track_id"], time.time(), str(e)[:300]))
                conn.commit()
            stats["failed"] += 1
            logger.debug("loudness: file %s couldn't be measured: %s", fid, e)
            pace()
            continue
        peak = round(10 ** (tp_db / 20.0), 6)
        with db._get_connection() as conn:
            conn.execute(
                f"""INSERT INTO {L.TABLE} (file_id, track_id, analyzer_version, analyzed_at, integrated_lufs,
                                           true_peak_db, track_gain, track_peak, reference_lufs, source,
                                           attempts, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'measured', 0, NULL)
                    ON CONFLICT(file_id) DO UPDATE SET analyzer_version = excluded.analyzer_version,
                        analyzed_at = excluded.analyzed_at, integrated_lufs = excluded.integrated_lufs,
                        true_peak_db = excluded.true_peak_db, track_gain = excluded.track_gain,
                        track_peak = excluded.track_peak, reference_lufs = excluded.reference_lufs,
                        source = 'measured', error = NULL""",
                (fid, f["track_id"], L.ANALYZER_VERSION, time.time(), integrated, tp_db,
                 round(ref - integrated, 2), peak, ref))
            conn.commit()
        stats["measured"] += 1
        fresh[fid] = (local, f["path"], f["track_id"], round(ref - integrated, 2), tp_db)
        lufs[fid] = integrated
        peaks[fid] = peak
        pace()

    # the album's gain, once every track on it is known. a file whose tags
    # carry an album gain keeps its own (tags first, in core.loudness)
    on_album = [f for f in files if not f.get("not_on_album")]
    album_gain = album_peak = None
    if len(on_album) > 1 and all(f["file_id"] in lufs for f in on_album):
        album_lufs = L.album_loudness([lufs[f["file_id"]] for f in on_album],
                                      [f["duration_s"] for f in on_album])
        if album_lufs is not None:
            album_gain = round(ref - album_lufs, 2)
            album_peak = max((peaks[f["file_id"]] for f in on_album if f["file_id"] in peaks), default=None)
            with db._get_connection() as conn:
                for f in on_album:
                    cur = conn.execute(
                        f"""UPDATE {L.TABLE} SET album_gain = ?, album_peak = ?, reference_lufs = ?
                             WHERE file_id = ? AND source IN ('measured', 'tags')
                               AND COALESCE(reference_lufs, ?) = ?""",
                        (album_gain, album_peak, ref, f["file_id"], ref, ref))
                    if not cur.rowcount and (known.get(f["file_id"]) or {}).get("album_gain") is None:
                        # tagged in the tag cache only: keep the album numbers beside it
                        have = known.get(f["file_id"]) or {}
                        L.remember_tags(conn, f["file_id"], {
                            "track_gain": have.get("track_gain"), "track_peak": have.get("track_peak"),
                            "album_gain": album_gain, "album_peak": album_peak}, ref)
                    stats["album"] += 1
                conn.commit()
    if write_tags and fresh:
        album_ids = {f["file_id"] for f in on_album}
        stats["written"] = write_measured_tags(
            db, fresh, album_gain=album_gain, album_peak=album_peak, album_files=album_ids,
            write=write)
    return stats


def _peak_db(linear: Optional[float]) -> Optional[float]:
    import math
    if linear is None or linear <= 0:
        return None
    return 20.0 * math.log10(linear)


def write_measured_tags(db: Any, fresh: Dict[int, tuple], *, album_gain: Optional[float] = None,
                        album_peak: Optional[float] = None, album_files=(),
                        write: Optional[WriteFn] = None) -> int:
    """write what was just measured into the files as ReplayGain tags, then
    read the files' tags back into the tag cache. a hand-tagged file, or a
    file on an album with one, is left alone. returns how many were written"""
    from core.metadata.common import get_file_lock
    from core.repair_jobs.base import hand_tagged_path_keys, is_hand_tagged_path

    if write is None:
        from core.replaygain import write_replaygain_tags as write
    keys = hand_tagged_path_keys(db)
    album_peak_db = _peak_db(album_peak)
    written = 0
    for fid, (local, stored, track_id, gain, tp_db) in fresh.items():
        if is_hand_tagged_path(stored, keys) or is_hand_tagged_path(local, keys):
            continue
        try:
            from core.library2.validation import album_is_hand_tagged
            with db._get_connection() as conn:
                if album_is_hand_tagged(conn, int(track_id)):
                    continue
            with_album = album_gain is not None and fid in album_files
            with get_file_lock(local):
                ok = write(local, gain, tp_db,
                           album_gain if with_album else None,
                           album_peak_db if with_album else None)
            if ok is False:
                logger.debug("loudness: couldn't write replaygain into file %s", fid)
                continue
            written += 1
            # the file's tags now carry it: read them back, as a rescan would
            from core.library2.tag_cache import read_and_persist_tag_cache
            with db._get_connection() as conn:
                read_and_persist_tag_cache(conn, fid, local)
                conn.commit()
        except Exception as e:  # noqa: BLE001 - the db keeps the measurement either way
            logger.debug("loudness: writing replaygain into file %s failed: %s", fid, e)
    return written


class LoudnessWorker:
    """the daemon loop. pause()/resume()/paused like the enrichment workers,
    so background_pause can hold it for a scan"""

    def __init__(self, db: Any, config_manager: Any = None, *, measure: Optional[MeasureFn] = None,
                 resolve: Optional[Callable] = None, ffmpeg_ok: Optional[Callable[[], bool]] = None):
        self.db = db
        self.config_manager = config_manager
        self._measure = measure
        self._resolve = resolve
        self._ffmpeg_ok = ffmpeg_ok
        self.running = False
        self.paused = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._priority: "collections.OrderedDict[int, Optional[int]]" = collections.OrderedDict()
        self._cursor = 0
        self.current: Optional[int] = None
        self.stats = {"measured": 0, "from_tags": 0, "failed": 0, "albums": 0, "written": 0}

    # ── lifecycle ──
    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="loudness-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.running = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False
        self._wake.set()

    def bump(self, album_id: Optional[int], file_id: Optional[int] = None) -> None:
        """a song with no loudness was played: its album is next"""
        if album_id is None:
            return
        with self._lock:
            self._priority[int(album_id)] = file_id
            self._priority.move_to_end(int(album_id), last=False)
            while len(self._priority) > 200:
                self._priority.popitem(last=True)
        self._wake.set()

    def status(self) -> Dict[str, Any]:
        return {"running": self.running, "paused": self.paused, "enabled": self._enabled(),
                "current_album": self.current, "cursor": self._cursor, **self.stats}

    # ── the loop ──
    def _enabled(self) -> bool:
        try:
            return bool(self.config_manager.get(CONFIG_KEY, True)) if self.config_manager else True
        except Exception:  # noqa: BLE001
            return True

    def _write_tags(self) -> bool:
        """the import's "Apply ReplayGain tags" switch: off unless turned on"""
        try:
            return bool(self.config_manager.get(WRITE_KEY, False)) if self.config_manager else False
        except Exception:  # noqa: BLE001
            return False

    def _have_ffmpeg(self) -> bool:
        if self._ffmpeg_ok is not None:
            return self._ffmpeg_ok()
        from core.replaygain import is_ffmpeg_available
        return is_ffmpeg_available()

    def _sleep(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _next(self):
        with self._lock:
            if self._priority:
                return self._priority.popitem(last=False)
        with self.db._get_connection() as conn:
            row = next_backlog_file(conn, self._cursor)
        if row is None:
            return None
        self._cursor = int(row["id"])
        return int(row["album_id"]), int(row["id"])

    def _run(self) -> None:
        # let the server finish starting before the first ffmpeg
        self._sleep(60)
        ffmpeg_checked = None
        while not self._stop.is_set():
            try:
                if self.paused or not self._enabled():
                    self._sleep(30)
                    continue
                if ffmpeg_checked is None:
                    ffmpeg_checked = self._have_ffmpeg()
                if not ffmpeg_checked:
                    logger.info("loudness: no ffmpeg here, so songs without ReplayGain aren't measured")
                    self._sleep(IDLE_SLEEP_S * 12)
                    ffmpeg_checked = None
                    continue
                job = self._next()
                if job is None:
                    # the whole library walked: start over later (new files, retries)
                    self._cursor = 0
                    self._sleep(IDLE_SLEEP_S)
                    continue
                album_id, file_id = job
                self.current = album_id
                # every profile's files: a background job reads the whole library
                from core.library_scope import library_scope
                with library_scope(None):
                    out = measure_album(self.db, album_id, extra_file_id=file_id, measure=self._measure,
                                        resolve=self._resolve,
                                        should_stop=lambda: self._stop.is_set() or self.paused,
                                        pace=lambda: self._stop.wait(BETWEEN_FILES_S),
                                        write_tags=self._write_tags())
                self.current = None
                self.stats["albums"] += 1
                for k in ("measured", "from_tags", "failed", "written"):
                    self.stats[k] += out.get(k, 0)
                self._stop.wait(BETWEEN_FILES_S)
            except Exception:  # noqa: BLE001 - a bad album never stops the worker
                logger.exception("loudness worker: an album failed")
                self.current = None
                self._sleep(60)
        self.running = False


_worker: Optional[LoudnessWorker] = None


def get_worker() -> Optional[LoudnessWorker]:
    return _worker


def start_worker(db: Any, config_manager: Any = None) -> LoudnessWorker:
    """made once at server start; deferred past a library upgrade like the others"""
    global _worker
    if _worker is None:
        _worker = LoudnessWorker(db, config_manager)
        from core.library2.migration_gate import defer_or_start
        defer_or_start(_worker, db)
    return _worker


def bump_track(conn, track_id: int, file_id: Optional[int]) -> None:
    """a played song with no loudness: measure its album next (no-op when
    the worker isn't running)"""
    worker = _worker
    if worker is None or not worker.running:
        return
    try:
        row = conn.execute("SELECT album_id FROM lib2_tracks WHERE id = ?", (int(track_id),)).fetchone()
        if row:
            worker.bump(int(row[0]), file_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("loudness bump failed: %s", e)


__all__ = ["LoudnessWorker", "album_files", "bump_track", "get_worker", "measure_album",
           "next_backlog_file", "start_worker", "write_measured_tags"]
