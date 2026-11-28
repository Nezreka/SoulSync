"""Live TV DVR: series & one-off recording rules, tuner conflict detection,
raw stream recording straight into library folders, and .nfo / .edl sidecars.

Files are the source of truth: recordings land directly in the target TV/Movie
folder with a standard Kodi/Jellyfin .nfo sidecar and (when commercials are
detected) a non-destructive .edl type-3 commercial skip sidecar.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape

from core.player.live import store
from utils.logging_config import get_logger

logger = get_logger("player.live.dvr")

RULES_TABLE = "live_dvr_rules"
RECORDINGS_TABLE = "live_dvr_recordings"

_ready: set = set()


class DvrError(ValueError):
    pass


def ensure_dvr_schema(db: Any) -> None:
    store.ensure_schema(db)
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {RULES_TABLE} (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                channel_id TEXT,
                title_match TEXT NOT NULL,
                new_only INTEGER NOT NULL DEFAULT 0,
                pad_before_min INTEGER NOT NULL DEFAULT 1,
                pad_after_min INTEGER NOT NULL DEFAULT 3,
                keep_limit INTEGER NOT NULL DEFAULT 0,
                library_type TEXT NOT NULL DEFAULT 'tv',
                target_dir TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {RECORDINGS_TABLE} (
                id TEXT PRIMARY KEY,
                rule_id TEXT,
                channel_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                subtitle TEXT,
                episode_num TEXT,
                description TEXT,
                start_ts REAL NOT NULL,
                end_ts REAL NOT NULL,
                pad_before_s INTEGER NOT NULL DEFAULT 60,
                pad_after_s INTEGER NOT NULL DEFAULT 180,
                status TEXT NOT NULL DEFAULT 'scheduled',
                file_path TEXT,
                nfo_path TEXT,
                edl_path TEXT,
                error TEXT,
                created_at REAL NOT NULL
            )
        """)
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{RECORDINGS_TABLE}_window "
            f"ON {RECORDINGS_TABLE} (status, start_ts, end_ts)"
        )
        conn.commit()
    _ready.add(key)


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(text or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120] or "Recording"


# ── rules CRUD ───────────────────────────────────────────────────────────

def add_rule(
    db: Any,
    *,
    name: str,
    title_match: str,
    channel_id: Optional[str] = None,
    new_only: bool = False,
    pad_before_min: int = 1,
    pad_after_min: int = 3,
    keep_limit: int = 0,
    library_type: str = "tv",
    target_dir: str = "",
) -> Dict[str, Any]:
    ensure_dvr_schema(db)
    tm = str(title_match or "").strip()
    if not tm:
        raise DvrError("title_match is required")
    lt = str(library_type or "tv").strip().lower()
    if lt not in ("tv", "movie"):
        raise DvrError("library_type must be 'tv' or 'movie'")
    rid = f"dvr_rule_{uuid.uuid4().hex[:10]}"
    now = time.time()
    with db._get_connection() as conn:
        conn.execute(
            f"""
            INSERT INTO {RULES_TABLE}
                (id, name, channel_id, title_match, new_only, pad_before_min,
                 pad_after_min, keep_limit, library_type, target_dir, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                rid,
                str(name or tm).strip(),
                str(channel_id).strip() if channel_id else None,
                tm,
                1 if new_only else 0,
                max(0, min(60, int(pad_before_min or 0))),
                max(0, min(120, int(pad_after_min or 0))),
                max(0, int(keep_limit or 0)),
                lt,
                str(target_dir or "").strip(),
                now,
            ),
        )
        conn.commit()
        row = conn.execute(f"SELECT * FROM {RULES_TABLE} WHERE id = ?", (rid,)).fetchone()
        return dict(row)


def list_rules(db: Any) -> List[Dict[str, Any]]:
    ensure_dvr_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(f"SELECT * FROM {RULES_TABLE} ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def delete_rule(db: Any, rule_id: str) -> bool:
    ensure_dvr_schema(db)
    with db._get_connection() as conn:
        cur = conn.execute(f"DELETE FROM {RULES_TABLE} WHERE id = ?", (str(rule_id),))
        conn.commit()
        return cur.rowcount > 0


# ── recordings & conflict detection ──────────────────────────────────────

def _tuner_limit_for_source(db: Any, source_id: str) -> int:
    src = store.get_source(db, source_id)
    if not src:
        return 0
    return int(src.get("tuner_count") or 0)


def _count_overlapping(
    conn,
    source_id: str,
    start_ts: float,
    end_ts: float,
    *,
    exclude_channel_id: Optional[str] = None,
) -> int:
    """Count distinct channels on ``source_id`` already scheduled/recording in [start_ts, end_ts]."""
    rows = conn.execute(
        f"""
        SELECT DISTINCT channel_id FROM {RECORDINGS_TABLE}
        WHERE source_id = ?
          AND status IN ('scheduled', 'recording')
          AND (start_ts - pad_before_s) < ?
          AND (end_ts + pad_after_s) > ?
        """,
        (source_id, end_ts, start_ts),
    ).fetchall()
    chans = {r["channel_id"] for r in rows}
    if exclude_channel_id:
        chans.discard(exclude_channel_id)
    return len(chans)


def schedule_recording(
    db: Any,
    *,
    channel_id: str,
    title: str,
    start_ts: float,
    end_ts: float,
    subtitle: Optional[str] = None,
    episode_num: Optional[str] = None,
    description: Optional[str] = None,
    rule_id: Optional[str] = None,
    pad_before_min: int = 1,
    pad_after_min: int = 3,
) -> Dict[str, Any]:
    ensure_dvr_schema(db)
    ch = store.get_channel(db, channel_id)
    if not ch:
        raise DvrError("Channel not found")
    if float(end_ts) <= float(start_ts):
        raise DvrError("end_ts must be after start_ts")

    pad_b = max(0, min(3600, int(pad_before_min or 0) * 60))
    pad_a = max(0, min(7200, int(pad_after_min or 0) * 60))
    eff_start = float(start_ts) - pad_b
    eff_end = float(end_ts) + pad_a
    source_id = ch["source_id"]
    tuner_limit = _tuner_limit_for_source(db, source_id)

    rec_id = f"dvr_rec_{uuid.uuid4().hex[:10]}"
    now = time.time()
    with db._get_connection() as conn:
        # deduplicate exact same channel + start_ts
        dup = conn.execute(
            f"""
            SELECT * FROM {RECORDINGS_TABLE}
            WHERE channel_id = ? AND ABS(start_ts - ?) < 60
              AND status IN ('scheduled', 'recording', 'completed')
            """,
            (channel_id, float(start_ts)),
        ).fetchone()
        if dup:
            return dict(dup)

        status = "scheduled"
        error = None
        if tuner_limit > 0:
            busy = _count_overlapping(conn, source_id, eff_start, eff_end, exclude_channel_id=channel_id)
            if busy >= tuner_limit:
                status = "conflict"
                error = f"All {tuner_limit} tuners on this source are booked during this window."

        conn.execute(
            f"""
            INSERT INTO {RECORDINGS_TABLE}
                (id, rule_id, channel_id, source_id, title, subtitle, episode_num,
                 description, start_ts, end_ts, pad_before_s, pad_after_s,
                 status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec_id,
                rule_id,
                channel_id,
                source_id,
                str(title or "Recording").strip(),
                subtitle,
                episode_num,
                description,
                float(start_ts),
                float(end_ts),
                pad_b,
                pad_a,
                status,
                error,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(f"SELECT * FROM {RECORDINGS_TABLE} WHERE id = ?", (rec_id,)).fetchone()
        return dict(row)


def list_recordings(db: Any, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_dvr_schema(db)
    with db._get_connection() as conn:
        if status:
            rows = conn.execute(
                f"SELECT * FROM {RECORDINGS_TABLE} WHERE status = ? ORDER BY start_ts ASC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM {RECORDINGS_TABLE} ORDER BY start_ts DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]


def cancel_recording(db: Any, rec_id: str, *, delete_files: bool = False) -> bool:
    ensure_dvr_schema(db)
    with db._get_connection() as conn:
        row = conn.execute(f"SELECT * FROM {RECORDINGS_TABLE} WHERE id = ?", (str(rec_id),)).fetchone()
        if not row:
            return False
        if delete_files:
            for col in ("file_path", "nfo_path", "edl_path"):
                fp = row[col]
                if fp and os.path.isfile(fp):
                    try:
                        os.remove(fp)
                    except OSError as exc:
                        logger.debug("dvr delete %s failed: %s", fp, exc)
        conn.execute(f"DELETE FROM {RECORDINGS_TABLE} WHERE id = ?", (str(rec_id),))
        conn.commit()
        return True


def evaluate_rules(db: Any, *, now: Optional[float] = None, lookahead_hours: int = 72) -> Dict[str, int]:
    """Match upcoming EPG programs against enabled DVR rules and schedule recordings."""
    ensure_dvr_schema(db)
    ts = now if now is not None else time.time()
    horizon = ts + lookahead_hours * 3600.0
    rules = [r for r in list_rules(db) if r.get("enabled")]
    channels = store.list_channels(db, include_disabled=False)
    by_id = {c["id"]: c for c in channels}

    scheduled = 0
    conflicts = 0
    for rule in rules:
        needle = str(rule["title_match"]).strip().lower()
        target_chans = [by_id[rule["channel_id"]]] if rule.get("channel_id") in by_id else channels
        for ch in target_chans:
            epg_id = ch.get("effective_epg_id") or ch.get("tvg_id")
            if not epg_id:
                continue
            with db._get_connection() as conn:
                progs = conn.execute(
                    f"""
                    SELECT * FROM {store.PROGRAMS}
                    WHERE epg_channel_id = ? AND end_ts > ? AND start_ts < ?
                    ORDER BY start_ts ASC
                    """,
                    (epg_id, ts, horizon),
                ).fetchall()
            for p in progs:
                if needle not in str(p["title"] or "").lower():
                    continue
                if rule.get("new_only") and not p["is_new"]:
                    continue
                rec = schedule_recording(
                    db,
                    channel_id=ch["id"],
                    title=p["title"],
                    subtitle=p["subtitle"],
                    episode_num=p["episode_num"],
                    description=p["description"],
                    start_ts=float(p["start_ts"]),
                    end_ts=float(p["end_ts"]),
                    rule_id=rule["id"],
                    pad_before_min=int(rule.get("pad_before_min") or 0),
                    pad_after_min=int(rule.get("pad_after_min") or 0),
                )
                if rec.get("status") == "conflict":
                    conflicts += 1
                elif rec.get("status") == "scheduled":
                    scheduled += 1
    return {"scheduled": scheduled, "conflicts": conflicts}


# ── sidecar writers (.nfo and .edl) ──────────────────────────────────────

def write_recording_nfo(rec: Dict[str, Any], media_path: str, *, is_movie: bool = False) -> str:
    """Write a standard Kodi/Jellyfin/Plex .nfo sidecar beside the recorded file."""
    nfo_path = str(Path(media_path).with_suffix(".nfo"))
    title = xml_escape(str(rec.get("title") or "Recording"))
    subtitle = xml_escape(str(rec.get("subtitle") or ""))
    plot = xml_escape(str(rec.get("description") or ""))
    ep = str(rec.get("episode_num") or "").strip()

    if is_movie:
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            "<movie>\n"
            f"  <title>{title}</title>\n"
            f"  <plot>{plot}</plot>\n"
            "  <source>SoulSync DVR</source>\n"
            "</movie>\n"
        )
    else:
        ep_title = subtitle or title
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            "<episodedetails>\n"
            f"  <title>{ep_title}</title>\n"
            f"  <showtitle>{title}</showtitle>\n"
            f"  <plot>{plot}</plot>\n"
            + (f"  <displayepisode>{xml_escape(ep)}</displayepisode>\n" if ep else "")
            + "  <source>SoulSync DVR</source>\n"
            "</episodedetails>\n"
        )
    Path(nfo_path).write_text(xml, encoding="utf-8")
    return nfo_path


def write_commercial_edl(media_path: str, breaks: List[tuple[float, float]]) -> Optional[str]:
    """Write a non-destructive Kodi/Jellyfin .edl sidecar (action 3 = commercial break)."""
    valid = [(float(s), float(e)) for s, e in breaks if float(e) > float(s) >= 0.0]
    if not valid:
        return None
    edl_path = str(Path(media_path).with_suffix(".edl"))
    lines = [f"{s:.2f}\t{e:.2f}\t3" for s, e in valid]
    Path(edl_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return edl_path


def run_comskip_if_available(media_path: str) -> Optional[str]:
    """Run comskip when installed on PATH to produce a non-destructive .edl beside the video."""
    comskip = shutil.which("comskip")
    if not comskip or not os.path.isfile(media_path):
        return None
    out_dir = str(Path(media_path).parent)
    try:
        subprocess.run(  # noqa: S603
            [comskip, "--output", out_dir, media_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("comskip failed for %s: %s", media_path, exc)
        return None
    edl = Path(media_path).with_suffix(".edl")
    return str(edl) if edl.is_file() else None


def build_recording_path(rec: Dict[str, Any], target_dir: str, *, is_movie: bool = False) -> str:
    """Compute destination path inside the library folder."""
    base = Path(target_dir or "data/dvr").resolve()
    title = _safe_filename(rec.get("title") or "Recording")
    ep = _safe_filename(rec.get("episode_num") or "") if rec.get("episode_num") else ""
    sub = _safe_filename(rec.get("subtitle") or "") if rec.get("subtitle") else ""
    stamp = time.strftime("%Y-%m-%d_%H%M", time.localtime(float(rec.get("start_ts") or time.time())))
    if is_movie:
        folder = base / title
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder / f"{title} ({stamp}).ts")
    folder = base / title
    folder.mkdir(parents=True, exist_ok=True)
    parts = [title]
    if ep:
        parts.append(ep)
    if sub:
        parts.append(sub)
    if not ep and not sub:
        parts.append(stamp)
    return str(folder / (" - ".join(parts) + ".ts"))


def record_now(
    db: Any,
    rec_id: str,
    *,
    target_dir: str = "data/dvr",
    is_movie: bool = False,
    runner: Optional[Callable[[List[str], str], bool]] = None,
) -> Dict[str, Any]:
    """Execute one scheduled recording synchronously (called by the DVR worker thread)."""
    ensure_dvr_schema(db)
    with db._get_connection() as conn:
        row = conn.execute(f"SELECT * FROM {RECORDINGS_TABLE} WHERE id = ?", (str(rec_id),)).fetchone()
        if not row:
            raise DvrError("Recording not found")
        rec = dict(row)
    ch = store.get_channel(db, rec["channel_id"])
    if not ch:
        raise DvrError("Channel not found")

    out_path = build_recording_path(rec, target_dir, is_movie=is_movie)
    duration_s = max(10, int((rec["end_ts"] + rec["pad_after_s"]) - max(time.time(), rec["start_ts"] - rec["pad_before_s"])))

    from core.player.transcode import ffmpeg_path
    ff = ffmpeg_path() or "ffmpeg"
    cmd = [
        ff, "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-i", str(ch["stream_url"]),
        "-t", str(duration_s),
        "-c", "copy",
        "-y", out_path,
    ]
    with db._get_connection() as conn:
        conn.execute(f"UPDATE {RECORDINGS_TABLE} SET status = 'recording', file_path = ? WHERE id = ?", (out_path, rec_id))
        conn.commit()

    ok = False
    err = None
    try:
        if runner is not None:
            ok = bool(runner(cmd, out_path))
        else:
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)  # noqa: S603
            ok = proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0
            if not ok:
                err = (proc.stderr or b"").decode("utf-8", errors="ignore")[-300:] or f"ffmpeg exited {proc.returncode}"
    except Exception as exc:  # noqa: BLE001
        err = str(exc)

    nfo_path = None
    edl_path = None
    if ok and os.path.isfile(out_path):
        nfo_path = write_recording_nfo(rec, out_path, is_movie=is_movie)
        edl_path = run_comskip_if_available(out_path)
        status = "completed"
    else:
        status = "failed"

    with db._get_connection() as conn:
        conn.execute(
            f"UPDATE {RECORDINGS_TABLE} SET status = ?, nfo_path = ?, edl_path = ?, error = ? WHERE id = ?",
            (status, nfo_path, edl_path, err, rec_id),
        )
        conn.commit()
        updated = conn.execute(f"SELECT * FROM {RECORDINGS_TABLE} WHERE id = ?", (rec_id,)).fetchone()
        return dict(updated)
