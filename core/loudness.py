"""loudness for the players: what each file measures, in one shape.

SoulSync measures loudness with ffmpeg ebur128 and writes it into files as
ReplayGain (core/replaygain.py). the players never used it. this module is
the one place that turns what a file has (its tags, or a measurement kept in
lib2_track_analysis when the file has none) into the numbers a player
needs, for the web player, the Android app and the Subsonic API alike.

the shape (``public``) is what every client gets:

    {"track_gain": -7.31, "track_peak": 0.988, "album_gain": -8.02,
     "album_peak": 1.0, "reference_lufs": -18.0, "source": "tags"}

gains are dB to add to reach ``reference_lufs``; peaks are linear (1.0 is
full scale). any of the four can be null. a player levelling to its own
target adds ``target - reference_lufs``.

precedence: the file's own tags (what every other player of the file
uses), then SoulSync's measurement, then the file read on the spot (a
library whose tag cache never filled), which is kept so it's read once.
"""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Dict, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("loudness")

TABLE = "lib2_track_analysis"
ANALYZER_VERSION = 1

# a gain past this is a broken tag (the +52 dB of the old summary parser)
MAX_ABS_GAIN_DB = 30.0

_GAIN = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(?:db)?\s*$", re.IGNORECASE)


def parse_gain(value: Any) -> Optional[float]:
    """'-7.31 dB', '+1.5', -7.31 -> dB, or None (blank, junk, absurd)"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        gain = float(value)
    else:
        m = _GAIN.match(str(value).replace(",", "."))
        if not m:
            return None
        gain = float(m.group(1))
    if not math.isfinite(gain) or abs(gain) > MAX_ABS_GAIN_DB:
        return None
    return round(gain, 2)


def parse_peak(value: Any) -> Optional[float]:
    """'0.988123' -> 0.988123 (linear), or None. a peak past 0 dBFS is real
    (true peak over a hot master); one past +12 dBFS is a broken tag"""
    if value is None or isinstance(value, bool):
        return None
    try:
        peak = float(str(value).strip().replace(",", ".")) if not isinstance(value, (int, float)) else float(value)
    except ValueError:
        return None
    if not math.isfinite(peak) or peak <= 0 or peak > 4.0:
        return None
    return round(peak, 6)


def reference_lufs(config_manager: Any = None) -> float:
    """the loudness the library's gains are written against (#1060)"""
    from core.replaygain import get_target_lufs
    if config_manager is None:
        try:
            from core.settings import config_manager as cm
            config_manager = cm
        except Exception:  # noqa: BLE001
            config_manager = None
    return get_target_lufs(config_manager)


def from_tags(tags: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """the four numbers out of a tag snapshot (tags_json, read_file_tags
    output, or read_replaygain_tags output)"""
    tags = tags or {}

    def pick(*keys):
        for k in keys:
            if tags.get(k) not in (None, ""):
                return tags.get(k)
        return None

    return {
        "track_gain": parse_gain(pick("replaygain_track_gain", "track_gain")),
        "track_peak": parse_peak(pick("replaygain_track_peak", "track_peak")),
        "album_gain": parse_gain(pick("replaygain_album_gain", "album_gain")),
        "album_peak": parse_peak(pick("replaygain_album_peak", "album_peak")),
    }


def public(values: Optional[Dict[str, Any]], reference: float, source: Optional[str]) -> Optional[Dict[str, Any]]:
    """the client shape, or None when nothing is known"""
    if not values or all(values.get(k) is None for k in ("track_gain", "album_gain")):
        return None
    return {
        "track_gain": values.get("track_gain"),
        "track_peak": values.get("track_peak"),
        "album_gain": values.get("album_gain"),
        "album_peak": values.get("album_peak"),
        "reference_lufs": reference,
        "source": source,
    }


def merge(tags: Dict[str, Optional[float]], measured: Optional[Dict[str, Any]]) -> tuple:
    """the file's tags first, the measurement filling what they lack.
    returns (values, source)"""
    out = dict(tags)
    used_tags = out.get("track_gain") is not None or out.get("album_gain") is not None
    used_measured = False
    if measured:
        for k in ("track_gain", "track_peak", "album_gain", "album_peak"):
            if out.get(k) is None and measured.get(k) is not None:
                out[k] = measured[k]
                used_measured = True
    source = "tags" if used_tags else ("measured" if used_measured else None)
    return out, source


# ── the table ──────────────────────────────────────────────────────────

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    file_id          INTEGER PRIMARY KEY,   -- lib2_track_files.id: loudness is a file's, not a track's
    track_id         INTEGER,
    analyzer_version INTEGER NOT NULL DEFAULT 1,
    analyzed_at      REAL,
    integrated_lufs  REAL,                  -- ebur128 integrated loudness
    true_peak_db     REAL,                  -- ebur128 true peak, dBFS
    track_gain       REAL,                  -- dB to reference_lufs
    track_peak       REAL,                  -- linear
    album_gain       REAL,
    album_peak       REAL,
    reference_lufs   REAL,
    source           TEXT,                  -- 'measured' | 'tags' (read off the file) | 'failed'
    attempts         INTEGER NOT NULL DEFAULT 0,
    error            TEXT
)"""


def ensure_schema(cursor: Any) -> None:
    """additive: a new table, created on start like the rest of lib2"""
    cursor.execute(DDL)
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_track ON {TABLE}(track_id)")
    # a file gone, or another file at the row (an upgrade, a re-import):
    # what was measured was another file's
    cursor.execute(f"""CREATE TRIGGER IF NOT EXISTS trg_{TABLE}_file_gone
        AFTER DELETE ON lib2_track_files BEGIN
            DELETE FROM {TABLE} WHERE file_id = OLD.id;
        END""")
    cursor.execute(f"""CREATE TRIGGER IF NOT EXISTS trg_{TABLE}_file_moved
        AFTER UPDATE OF path, size ON lib2_track_files
        WHEN OLD.path IS NOT NEW.path OR OLD.size IS NOT NEW.size BEGIN
            DELETE FROM {TABLE} WHERE file_id = OLD.id;
        END""")


def _row_values(row: Any, reference: float) -> Optional[Dict[str, Any]]:
    """a measured row's gains, moved to today's reference if it changed since"""
    if not row or row["source"] == "failed":
        return None
    shift = 0.0
    if row["reference_lufs"] is not None:
        shift = reference - float(row["reference_lufs"])

    def g(v):
        return None if v is None else round(float(v) + shift, 2)

    return {"track_gain": g(row["track_gain"]), "track_peak": row["track_peak"],
            "album_gain": g(row["album_gain"]), "album_peak": row["album_peak"]}


def for_files(conn: Any, file_ids: Iterable[int], *, reference: Optional[float] = None) -> Dict[int, Dict[str, Any]]:
    """{file_id: public shape} for the files SoulSync knows loudness for
    (its tag cache or its measurement). files with nothing are left out."""
    ids = sorted({int(i) for i in file_ids if i is not None})
    if not ids:
        return {}
    ref = reference_lufs() if reference is None else reference
    out: Dict[int, Dict[str, Any]] = {}
    has_table = _has_table(conn)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" for _ in chunk)
        join = f"LEFT JOIN {TABLE} a ON a.file_id = f.id" if has_table else ""
        cols = ("a.source, a.track_gain, a.track_peak, a.album_gain, a.album_peak, a.reference_lufs"
                if has_table else
                "NULL AS source, NULL AS track_gain, NULL AS track_peak, NULL AS album_gain, "
                "NULL AS album_peak, NULL AS reference_lufs")
        for row in conn.execute(
                f"SELECT f.id AS fid, f.tags_json, {cols} FROM lib2_track_files f {join} "
                f"WHERE f.id IN ({marks})", chunk):
            try:
                tags = json.loads(row["tags_json"] or "{}")
            except (TypeError, ValueError):
                tags = {}
            values, source = merge(from_tags(tags if isinstance(tags, dict) else {}),
                                   _row_values(row, ref))
            shaped = public(values, ref, source)
            if shaped:
                out[int(row["fid"])] = shaped
    return out


def _has_table(conn: Any) -> bool:
    try:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                 (TABLE,)).fetchone())
    except Exception:  # noqa: BLE001
        return False


def for_file(conn: Any, file_id: Optional[int], path: Optional[str] = None,
             *, reference: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """one file's loudness: the db first, else its tags read now (and kept,
    so a library whose tag cache never filled is read once, not every play)"""
    if file_id is None:
        return None
    ref = reference_lufs() if reference is None else reference
    hit = for_files(conn, [file_id], reference=ref).get(int(file_id))
    if hit or not path:
        return hit
    try:
        from core.replaygain import read_replaygain_tags
        values = from_tags(read_replaygain_tags(path))
    except Exception as e:  # noqa: BLE001
        logger.debug("loudness: reading tags of file %s failed: %s", file_id, e)
        return None
    shaped = public(values, ref, "tags")
    if shaped and _has_table(conn):
        try:
            remember_tags(conn, int(file_id), values, ref)
            conn.commit()
        except Exception as e:  # noqa: BLE001 - a read-only moment: next play reads again
            logger.debug("loudness: keeping file %s tags failed: %s", file_id, e)
    return shaped


def for_tracks(conn: Any, track_ids: Iterable[int], *, reference: Optional[float] = None) -> Dict[int, Optional[Dict[str, Any]]]:
    """{track_id: shape or None} for the file the player would play for each
    track (the same pick as core/player/sources/music: lossless first, then
    bitrate), for a queue's worth of songs at once"""
    ids = sorted({int(i) for i in track_ids if i is not None})[:500]
    if not ids:
        return {}
    picked: Dict[int, int] = {}
    marks = ",".join("?" for _ in ids)
    for row in conn.execute(
            f"""SELECT f.track_id, f.id FROM lib2_track_files f
                 WHERE f.track_id IN ({marks}) AND COALESCE(f.import_status, 'imported') = 'imported'
                   AND COALESCE(f.file_state, 'active') <> 'deleted'
                 ORDER BY f.track_id, CASE WHEN f.quality_tier = 'lossless' THEN 0 ELSE 1 END,
                          COALESCE(f.bitrate, 0) DESC""", ids):
        picked.setdefault(int(row[0]), int(row[1]))
    found = for_files(conn, picked.values(), reference=reference)
    return {tid: found.get(picked[tid]) if tid in picked else None for tid in ids}


def remember_tags(conn: Any, file_id: int, values: Dict[str, Any], reference: float) -> None:
    """the gains a file's tags carry, kept so it's never measured"""
    conn.execute(
        f"""INSERT INTO {TABLE} (file_id, track_id, analyzer_version, analyzed_at, track_gain, track_peak,
                                 album_gain, album_peak, reference_lufs, source, attempts, error)
            VALUES (?, (SELECT track_id FROM lib2_track_files WHERE id = ?), ?, ?, ?, ?, ?, ?, ?, 'tags', 0, NULL)
            ON CONFLICT(file_id) DO UPDATE SET track_gain = excluded.track_gain,
                track_peak = excluded.track_peak, album_gain = excluded.album_gain,
                album_peak = excluded.album_peak, reference_lufs = excluded.reference_lufs,
                source = 'tags', analyzed_at = excluded.analyzed_at, error = NULL""",
        (file_id, file_id, ANALYZER_VERSION, time.time(), values.get("track_gain"), values.get("track_peak"),
         values.get("album_gain"), values.get("album_peak"), reference))


def headers(loud: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """the same numbers as response headers on a track's bytes, so a client
    that fetched a song ahead of time (the web player's pre-cache) has them"""
    if not loud:
        return {}
    out = {"X-Loudness-Reference": f"{loud['reference_lufs']:.2f}"}
    for key, name in (("track_gain", "Track-Gain"), ("track_peak", "Track-Peak"),
                      ("album_gain", "Album-Gain"), ("album_peak", "Album-Peak")):
        if loud.get(key) is not None:
            out[f"X-ReplayGain-{name}"] = f"{loud[key]:.6f}" if key.endswith("peak") else f"{loud[key]:.2f}"
    return out


# ── the gain a player applies (the same math the web and Android use) ───

def album_loudness(lufs_values: Iterable[float], durations: Optional[Iterable[float]] = None) -> Optional[float]:
    """an album's loudness from its tracks': the energy mean, weighted by
    length (a 40 second interlude counts less than a 9 minute song), which
    is what measuring the album as one long file gives, near enough"""
    values = [float(v) for v in lufs_values]
    if not values:
        return None
    weights = [max(float(d), 1.0) for d in durations] if durations is not None else [1.0] * len(values)
    if len(weights) != len(values):
        weights = [1.0] * len(values)
    energy = sum(w * 10 ** (v / 10.0) for v, w in zip(values, weights, strict=True)) / sum(weights)
    return round(10 * math.log10(energy), 2) if energy > 0 else None


UNKNOWN_SONG_DB = -6.0
LIMIT_DB = 24.0


def level_db(loud: Optional[Dict[str, Any]], *, mode: str, album_context: bool, target: float,
             preamp: float = 0.0, prevent_clipping: bool = True, unknown_db: float = UNKNOWN_SONG_DB) -> float:
    """the dB a player applies for this file (the web player's songLevelDb,
    the Android app's, the contract's "Loudness" section: keep them equal).

    mode off | track | album | smart. smart uses album gain when the item
    plays as part of its album (``album_context``), track gain otherwise.
    a missing album gain falls back to track gain and the other way round.
    nothing known: ``unknown_db`` under the target, never louder than the
    file (an unmeasured song is usually a loud modern master). clipping
    prevention caps the gain where the peak reaches full scale; with no
    peak known, a song is never turned up."""
    if mode == "off":
        return 0.0
    loud = loud or {}
    ref = loud.get("reference_lufs")
    shift = target - (float(ref) if ref is not None else -18.0)
    use_album = mode == "album" or (mode == "smart" and album_context)
    gain = loud.get("album_gain") if use_album else loud.get("track_gain")
    peak = loud.get("album_peak") if use_album else loud.get("track_peak")
    if gain is None:
        gain = loud.get("track_gain") if use_album else loud.get("album_gain")
        peak = loud.get("track_peak") if use_album else loud.get("album_peak")
    if gain is not None:
        db = float(gain) + shift + preamp
    else:
        db = min(0.0, unknown_db + shift + preamp)
        peak = None
    if prevent_clipping:
        ceiling = -20 * math.log10(float(peak)) if peak else 0.0
        db = min(db, ceiling)
    return round(max(-LIMIT_DB, min(LIMIT_DB, db)), 2)


__all__ = [
    "TABLE", "album_loudness", "ensure_schema", "for_file", "for_files", "for_tracks", "from_tags", "headers",
    "level_db", "merge", "parse_gain", "parse_peak", "public", "reference_lufs", "remember_tags",
]
