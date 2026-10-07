"""one-time repair of the listening history: one clock, no echoes.

the history's rule is that a stored time is utc ("YYYY-MM-DD HH:MM:SS").
two writers broke it: plex plays and web-player plays were stored in the
server's LOCAL time, iso with a T. so the scrobble soulsync sent to last.fm
or listenbrainz came back on the next import as a second play: a different
format and 7 hours off (pacific), never matched. boulder's history had
9,499 of 9,787 plex plays doubled this way, and his kids' plays came back
into his own pile through his last.fm.

this runs once, in the app (local time is the app's local time, the same
clock the rows were written with):

1. back up the two listening tables to a small side file
2. move plex / web-player local times to utc; reformat any other T row
3. merge each last.fm / listenbrainz copy into the play it echoes (same
   song, within the import matcher's tolerance): the copy's import links
   move to the real play so it can never be imported again, then it goes

nothing is merged across a song or outside the tolerance, and every step
is logged with its counts.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from core.listening_import.dedup import (
    CANONICAL,
    CROSS_SOURCE_TOLERANCE_SECONDS,
    _text,
    canonical_played_at,
)
from utils.logging_config import get_logger

logger = get_logger("listening_import.history_repair")

DONE_KEY = "listening_history_one_clock_repair_v1"
# writers that stored the app's local time with no zone
LOCAL_TIME_SOURCES = ("plex", "soulsync_web")
ECHO_SOURCES = ("lastfm", "listenbrainz")


def _epoch(canonical: str) -> int:
    return int(datetime.strptime(canonical, CANONICAL).replace(tzinfo=timezone.utc).timestamp())


def backup_listening_tables(db_path: str) -> Optional[str]:
    """copy listening_history + listening_import_events to a side file next to
    the database. small (the listening tables, not the whole library)"""
    folder = os.path.dirname(os.path.abspath(db_path))
    target = os.path.join(folder, f"listening_backup_{time.strftime('%Y%m%d_%H%M%S')}.db")
    src = sqlite3.connect(db_path, timeout=30)
    try:
        src.execute("ATTACH DATABASE ? AS bak", (target,))
        for table in ("listening_history", "listening_import_events"):
            exists = src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists:
                src.execute(f"CREATE TABLE bak.{table} AS SELECT * FROM main.{table}")
        src.commit()
        src.execute("DETACH DATABASE bak")
    finally:
        src.close()
    return target


def normalize_times(conn) -> Dict[str, int]:
    """every row onto the one clock. returns {converted, dropped}"""
    rows = conn.execute(
        "SELECT id, played_at, server_source FROM listening_history WHERE played_at LIKE '%T%'").fetchall()
    converted = dropped = 0
    for row_id, played_at, source in rows:
        new = canonical_played_at(played_at, naive_is_local=(source in LOCAL_TIME_SOURCES))
        if not new or new == played_at:
            continue
        cur = conn.execute("UPDATE OR IGNORE listening_history SET played_at = ? WHERE id = ?", (new, row_id))
        if cur.rowcount:
            converted += 1
            # a same-source link recorded this play under its old clock
            conn.execute(
                "UPDATE OR IGNORE listening_import_events SET listened_at = ? "
                "WHERE history_id = ? AND source = ?", (_epoch(new), row_id, source))
        else:
            # the same play already exists at the right time: this was a copy.
            # its import links go to that play, then the copy goes
            twin = conn.execute(
                "SELECT h.id FROM listening_history h JOIN listening_history o ON o.id = ? "
                "WHERE h.played_at = ? AND h.track_id IS o.track_id AND h.server_source = o.server_source "
                "AND h.profile_id = o.profile_id AND h.id != o.id", (row_id, new)).fetchone()
            if twin is None:
                continue
            conn.execute("UPDATE OR IGNORE listening_import_events SET history_id = ? WHERE history_id = ?",
                         (twin[0], row_id))
            conn.execute("DELETE FROM listening_import_events WHERE history_id = ?", (row_id,))
            conn.execute("DELETE FROM listening_history WHERE id = ?", (row_id,))
            dropped += 1
    return {"converted": converted, "dropped": dropped}


def merge_echoes(conn) -> int:
    """fold each last.fm / listenbrainz copy into the play it echoes. the
    copy can sit in another pile than the play (a kid's play echoed through
    the admin's last.fm), so piles aren't a boundary here; song + time are"""
    plays: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
    for row_id, title, artist, played_at in conn.execute(
            "SELECT id, title, artist, played_at FROM listening_history "
            f"WHERE server_source NOT IN ({','.join('?' * len(ECHO_SOURCES))})", ECHO_SOURCES).fetchall():
        try:
            plays.setdefault((_text(title), _text(artist)), []).append((_epoch(played_at), row_id))
        except ValueError:
            continue
    if not plays:
        return 0
    merged = 0
    claimed = set()  # (play id, echo source): a play absorbs one copy per source
    for echo_id, title, artist, played_at, source in conn.execute(
            "SELECT id, title, artist, played_at, server_source FROM listening_history "
            f"WHERE server_source IN ({','.join('?' * len(ECHO_SOURCES))}) ORDER BY id", ECHO_SOURCES).fetchall():
        name = (_text(title), _text(artist))
        if not name[1] or name not in plays:
            continue  # no artist: not enough to call two plays one
        try:
            ts = _epoch(played_at)
        except ValueError:
            continue
        best = None
        for play_ts, play_id in plays[name]:
            delta = abs(play_ts - ts)
            if delta <= CROSS_SOURCE_TOLERANCE_SECONDS and (play_id, source) not in claimed:
                if best is None or delta < best[0]:
                    best = (delta, play_id)
        if best is None:
            continue
        play_id = best[1]
        claimed.add((play_id, source))
        # the copy's import links now point at the real play, so the next
        # import knows it and never brings the copy back
        conn.execute("UPDATE OR IGNORE listening_import_events SET history_id = ? WHERE history_id = ?",
                     (play_id, echo_id))
        conn.execute(f"UPDATE listening_history SET scrobbled_{source} = 1 WHERE id = ?", (play_id,))
        if conn.execute("SELECT 1 FROM listening_import_events WHERE history_id = ?", (echo_id,)).fetchone():
            # the play already holds a link from that service, so this one
            # couldn't move. the copy stays: dropping it with its link would
            # let the next import bring it back
            continue
        conn.execute("DELETE FROM listening_history WHERE id = ?", (echo_id,))
        merged += 1
    return merged


def repair_once(database, db_path: Optional[str] = None) -> Optional[Dict[str, int]]:
    """the whole repair, once per install. returns the counts, or None when
    it already ran. a failure rolls back and is retried next time"""
    if database.get_metadata(DONE_KEY):
        return None
    path = db_path or getattr(database, "database_path", None)
    backup = None
    if path:
        backup = backup_listening_tables(str(path))
        logger.info("Listening history repair: backed up the listening tables to %s", backup)
    conn = database._get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        times = normalize_times(conn)
        merged = merge_echoes(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    database.set_metadata(DONE_KEY, "1")
    counts = {**times, "merged": merged}
    logger.info("Listening history repair: %s (backup: %s)", counts, backup)
    return counts
