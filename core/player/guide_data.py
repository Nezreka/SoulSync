"""what the guide needs beyond its catalog:

  * what this profile has watched (movies and episodes), so channels air
    what you haven't seen first. the library's watch state for whoever's
    watch state it is (core.video.watch_scope), and what the player
    itself saw played through
  * a person's films and shows (an actor, a director), for a channel of
    them, and finding a person by name
  * reminders: a program you want to be told about when it starts, kept
    in player.db so every screen of the profile can tell you
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

REMINDERS = "player_reminders"
MAX_REMINDERS = 200

_ready: set = set()


class GuideDataError(ValueError):
    pass


# ── watched ──────────────────────────────────────────────────────────────

def watched_ids(video_db: Any, watcher_rows: Optional[int], player_db: Any, profile_id: int) -> Dict[str, List[int]]:
    """{movies: [ids], episodes: [ids]} this profile has seen"""
    movies: set = set()
    episodes: set = set()
    with video_db._get_connection() as conn:
        if watcher_rows is None:
            movies.update(r[0] for r in conn.execute(
                "SELECT id FROM movies WHERE has_file = 1 AND COALESCE(play_count, 0) > 0"))
            episodes.update(r[0] for r in conn.execute(
                "SELECT id FROM episodes WHERE has_file = 1 AND COALESCE(play_count, 0) > 0"))
        else:
            for kind, item in conn.execute(
                    "SELECT kind, item_id FROM video_watch_state WHERE profile_id = ? AND play_count > 0",
                    (int(watcher_rows),)):
                (movies if kind == "movie" else episodes).add(int(item))
    from core.player.progress import TABLE, ensure_schema
    ensure_schema(player_db)
    with player_db._get_connection() as conn:
        for kind, item in conn.execute(
                f"SELECT item_type, item_id FROM {TABLE} WHERE profile_id = ? AND finished = 1 "
                "AND item_type IN ('movie', 'episode')", (int(profile_id),)):
            try:
                (movies if kind == "movie" else episodes).add(int(item))
            except (TypeError, ValueError):
                continue
    return {"movies": sorted(movies), "episodes": sorted(episodes)}


# ── people ───────────────────────────────────────────────────────────────

def find_people(video_db: Any, query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """people in the library by name, the ones in most owned titles first"""
    q = " ".join(str(query or "").split())
    if len(q) < 2:
        return []
    with video_db._get_connection() as conn:
        rows = conn.execute(
            "SELECT p.id, p.name, p.photo_url, COUNT(DISTINCT COALESCE(c.movie_id, -c.show_id)) AS n, "
            "MAX(CASE WHEN c.department = 'crew' THEN c.job END) AS job "
            "FROM people p JOIN credits c ON c.person_id = p.id "
            "LEFT JOIN movies m ON m.id = c.movie_id LEFT JOIN shows s ON s.id = c.show_id "
            "WHERE p.name LIKE ? AND (m.has_file = 1 OR s.id IS NOT NULL) "
            "GROUP BY p.id ORDER BY n DESC, p.name LIMIT ?",
            (f"%{q}%", max(1, min(int(limit), 25)))).fetchall()
    return [{"id": r[0], "name": r[1], "photo_url": r[2], "titles": r[3], "job": r[4]} for r in rows]


def person_titles(video_db: Any, person_id: int) -> Dict[str, Any]:
    """a person's owned films and their shows: {name, movies: [ids], shows: [ids]}"""
    with video_db._get_connection() as conn:
        row = conn.execute("SELECT name FROM people WHERE id = ?", (int(person_id),)).fetchone()
        if not row:
            raise GuideDataError("no such person")
        movies = [r[0] for r in conn.execute(
            "SELECT DISTINCT c.movie_id FROM credits c JOIN movies m ON m.id = c.movie_id "
            "WHERE c.person_id = ? AND m.has_file = 1", (int(person_id),))]
        shows = [r[0] for r in conn.execute(
            "SELECT DISTINCT show_id FROM credits WHERE person_id = ? AND show_id IS NOT NULL",
            (int(person_id),))]
    return {"name": row[0], "movies": movies, "shows": shows}


# ── reminders ────────────────────────────────────────────────────────────

def ensure_schema(db: Any) -> None:
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {REMINDERS} (
                profile_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                starts_at REAL NOT NULL,
                program_key TEXT NOT NULL,
                title TEXT NOT NULL,
                subtitle TEXT,
                channel_name TEXT,
                created_at REAL NOT NULL,
                PRIMARY KEY (profile_id, channel_id, starts_at)
            )""")
        conn.commit()
    _ready.add(key)


def _text(v: Any, n: int) -> Optional[str]:
    t = " ".join(str(v or "").split())[:n]
    return t or None


def list_reminders(db: Any, profile_id: int, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """what's still to come (an hour after a start it's gone)"""
    ensure_schema(db)
    t = time.time() if now is None else now
    with db._get_connection() as conn:
        conn.execute(f"DELETE FROM {REMINDERS} WHERE starts_at < ?", (t - 3600,))
        conn.commit()
        rows = conn.execute(
            f"SELECT channel_id, starts_at, program_key, title, subtitle, channel_name FROM {REMINDERS} "
            "WHERE profile_id = ? ORDER BY starts_at", (int(profile_id),)).fetchall()
    return [{"channel_id": r[0], "starts_at": r[1], "program_key": r[2], "title": r[3],
             "subtitle": r[4], "channel_name": r[5]} for r in rows]


def add_reminder(db: Any, profile_id: int, body: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise GuideDataError("a reminder needs a program")
    channel_id = _text(body.get("channel_id"), 120)
    program_key = _text(body.get("program_key"), 200)
    title = _text(body.get("title"), 200)
    starts = body.get("starts_at")
    t = time.time() if now is None else now
    if not channel_id or not program_key or not title:
        raise GuideDataError("a reminder needs a channel, a program and its title")
    if isinstance(starts, bool) or not isinstance(starts, (int, float)) or not t - 60 < starts < t + 14 * 86400:
        raise GuideDataError("starts_at must be a time in the next two weeks (seconds)")
    ensure_schema(db)
    with db._get_connection() as conn:
        n = conn.execute(f"SELECT COUNT(*) FROM {REMINDERS} WHERE profile_id = ?", (int(profile_id),)).fetchone()[0]
        if n >= MAX_REMINDERS:
            raise GuideDataError("that's a lot of reminders: take some off first")
        conn.execute(
            f"INSERT OR REPLACE INTO {REMINDERS} (profile_id, channel_id, starts_at, program_key, title, "
            "subtitle, channel_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(profile_id), channel_id, float(starts), program_key, title,
             _text(body.get("subtitle"), 200), _text(body.get("channel_name"), 80), t))
        conn.commit()
    return {"channel_id": channel_id, "starts_at": float(starts), "program_key": program_key, "title": title}


def remove_reminder(db: Any, profile_id: int, channel_id: str, starts_at: float) -> bool:
    ensure_schema(db)
    with db._get_connection() as conn:
        cur = conn.execute(f"DELETE FROM {REMINDERS} WHERE profile_id = ? AND channel_id = ? AND starts_at = ?",
                           (int(profile_id), str(channel_id), float(starts_at)))
        conn.commit()
        return bool(cur.rowcount)
