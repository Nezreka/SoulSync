"""you and one artist: your own plays of them, for the artist page.

read from the pile the caller's profile reads (core.listening_scope), drawn
as stats, so a history kept for taste only stays out like it does on the
stats page.

a play belongs to the artist when it names them (their name, or the name of
an artist linked to them as an alias) or when it is linked to one of their
library tracks. the link is lib2_track_id, the catalogue id, never the media
server's db_track_id: that one can be another server's row or a stale id
after a rescan.
"""

from __future__ import annotations

import bisect
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.listening_scope import listening_owner, stats_owner_clause, stats_view
from utils.logging_config import get_logger

logger = get_logger("library2.artist_listening")

TOP_TRACKS = 5
MONTHS = 12

# your artists by plays, per pile, for the rank. one pass over the pile, kept
# a few minutes so paging through artists doesn't repeat it
_RANK_TTL = 600.0
_rank_cache: Dict[Tuple[Any, int, str], Tuple[float, List[int]]] = {}
_rank_lock = threading.Lock()


def _artist_group(conn, artist_id: int) -> Tuple[List[int], List[str]]:
    """the artist and every row linked to it as an alias: (ids, names)"""
    row = conn.execute("SELECT id, name, canonical_artist_id FROM lib2_artists WHERE id = ?",
                       (int(artist_id),)).fetchone()
    if row is None:
        return [], []
    root = int(row[2] or row[0])
    rows = conn.execute(
        "SELECT id, name FROM lib2_artists WHERE id = ? OR canonical_artist_id = ?",
        (root, root)).fetchall()
    ids = sorted({int(r[0]) for r in rows} | {int(artist_id)})
    names = sorted({str(r[1]).strip() for r in rows if r[1] and str(r[1]).strip()}
                   | {str(row[1]).strip()})
    return ids, names


def _counts_asc(database, conn, owner: int, scope: str) -> List[int]:
    """every artist's play count in the pile, ascending, for bisect"""
    key = (getattr(database, "database_path", None) or id(database), owner, scope)
    now = time.time()
    with _rank_lock:
        hit = _rank_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    counts = sorted(int(r[0]) for r in conn.execute(
        f"SELECT COUNT(*) FROM listening_history WHERE {scope} "
        f"AND artist IS NOT NULL AND artist != '' GROUP BY LOWER(artist)").fetchall())
    with _rank_lock:
        _rank_cache[key] = (now + _RANK_TTL, counts)
    return counts


def clear_rank_cache() -> None:
    with _rank_lock:
        _rank_cache.clear()


def _month_keys(today: datetime) -> List[str]:
    keys = []
    for back in range(MONTHS - 1, -1, -1):
        y, m = divmod(today.year * 12 + today.month - 1 - back, 12)
        keys.append(f"{y:04d}-{m + 1:02d}")
    return keys


def artist_listening(database, conn, artist_id: int, profile_id: Optional[int],
                     *, today: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """{play_count, first_played, last_played, plays_30d, rank, artists_played,
    months, top_tracks} for the caller and this artist, or None when they have
    never played them."""
    ids, names = _artist_group(conn, artist_id)
    if not ids:
        return None
    owner = listening_owner(database, profile_id)
    with stats_view(database):
        scope = stats_owner_clause(owner, "h")
        rank_scope = stats_owner_clause(owner)
    id_marks = ",".join("?" * len(ids))
    name_marks = ",".join("?" * len(names))
    # each half walks its own index (idx_listening_artist, idx_listening_lib2_track)
    mine = f"""
        WITH mine AS MATERIALIZED (
            SELECT id FROM listening_history WHERE artist IN ({name_marks})
            UNION
            SELECT id FROM listening_history WHERE lib2_track_id IN (
                SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
                WHERE al.primary_artist_id IN ({id_marks}))
        )
    """
    args = [*names, *ids]
    rows = conn.execute(f"""
        {mine}
        SELECT MIN(h.title), COUNT(*), MAX(h.played_at), MIN(h.played_at),
               MAX(h.lib2_track_id), MAX(h.album),
               SUM(CASE WHEN h.played_at >= datetime('now', '-30 days') THEN 1 ELSE 0 END)
        FROM listening_history h JOIN mine m ON m.id = h.id
        WHERE {scope} AND h.title IS NOT NULL AND h.title != ''
        GROUP BY LOWER(h.title)
        ORDER BY COUNT(*) DESC, MAX(h.played_at) DESC
    """, args).fetchall()
    if not rows:
        return None
    plays = sum(int(r[1]) for r in rows)
    last_played = max(r[2] for r in rows if r[2])
    first_played = min(r[3] for r in rows if r[3])
    plays_30d = sum(int(r[6] or 0) for r in rows)

    # plays per month, the last 12, oldest first; local months like every
    # other listening bucket
    per_month = dict(conn.execute(f"""
        {mine}
        SELECT strftime('%Y-%m', h.played_at, 'localtime') AS ym, COUNT(*)
        FROM listening_history h JOIN mine m ON m.id = h.id
        WHERE {scope}
          AND h.played_at >= datetime('now', 'localtime', 'start of month', '-11 months', 'utc')
        GROUP BY ym
    """, args).fetchall())
    months = [{"month": k, "plays": int(per_month.get(k, 0))}
              for k in _month_keys(today or datetime.now())]

    # a top song's library track: the play's own link when it is this
    # artist's, else the artist's track of that title
    own: Dict[int, int] = {}
    by_title: Dict[str, Tuple[int, int]] = {}
    for track_id, title, album_id in conn.execute(f"""
        SELECT t.id, t.title, al.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
        WHERE al.primary_artist_id IN ({id_marks})
    """, ids).fetchall():
        own[int(track_id)] = int(album_id)
        by_title.setdefault(str(title or "").strip().casefold(), (int(track_id), int(album_id)))
    top = []
    for title, count, last, _first, linked, album, _recent in rows[:TOP_TRACKS]:
        track_id = album_id = None
        if linked is not None and int(linked) in own:
            track_id, album_id = int(linked), own[int(linked)]
        elif str(title or "").strip().casefold() in by_title:
            track_id, album_id = by_title[str(title).strip().casefold()]
        top.append({
            "title": title, "album": album or "", "play_count": int(count), "last_played": last,
            "track_id": track_id, "album_id": album_id,
            "thumb_url": f"/api/library/v2/artwork/album/{album_id}?size=thumb" if album_id else "",
        })

    counts = _counts_asc(database, conn, owner, rank_scope)
    above = len(counts) - bisect.bisect_right(counts, plays)
    return {
        "play_count": plays,
        "first_played": first_played,
        "last_played": last_played,
        "plays_30d": plays_30d,
        "rank": above + 1,
        "artists_played": max(len(counts), above + 1),
        "months": months,
        "top_tracks": top,
    }


__all__ = ["artist_listening", "clear_rank_cache"]
