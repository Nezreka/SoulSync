"""the owned video library in one compact read, for the /player app.

the guide builds channels out of what's on disk (genre, network, decade, kids)
and needs real runtimes to schedule them. the library list routes page, carry
no runtime and no added date, so this reads the few columns the player needs
in one pass. owned only: a channel never airs something that isn't there.

it has to hold up on a big library (9k films, 100k+ owned episodes), so:
  * every query is one pass over a table (no per-row subqueries);
  * rows are positional lists, with the field order in MOVIE_FIELDS and
    SHOW_FIELDS (the player decodes them, -lib/catalog.ts);
  * a show carries a sample of its owned episodes spread across its run,
    not all of them: enough for a channel to air something different every
    half hour, at a size a TV browser can parse.

every function takes an open video-db connection. the route filters the
result by what the profile may see (kinds and rating caps).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

EPISODE_SAMPLE = 10
RECENT_EPISODES = 40
YOUTUBE_MAX = 3000

MOVIE_FIELDS = ("id", "title", "year", "runtime_seconds", "content_rating", "rating",
                "genre_ids", "added_at", "has_poster", "has_backdrop", "studio", "tmdb_id")
SHOW_FIELDS = ("id", "title", "year", "network", "content_rating", "rating", "genre_ids",
               "added_at", "has_poster", "has_backdrop", "episode_count", "episodes", "tmdb_id")


def _genre_map(conn, table: str, key: str) -> Dict[int, List[int]]:
    out: Dict[int, List[int]] = {}
    for row in conn.execute(f"SELECT {key}, genre_id FROM {table}"):  # noqa: S608 - fixed names
        out.setdefault(row[0], []).append(row[1])
    return out


def genre_names(conn) -> Dict[int, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT id, name FROM genres")}


def owned_movies(conn) -> List[list]:
    files: Dict[int, Tuple[Any, Any]] = {}
    for r in conn.execute("""
            SELECT movie_id, MAX(runtime_seconds), MAX(added_at) FROM media_files
            WHERE movie_id IS NOT NULL GROUP BY movie_id"""):
        files[r[0]] = (r[1], r[2])
    genres = _genre_map(conn, "movie_genres", "movie_id")
    out = []
    for r in conn.execute("""
            SELECT id, title, year, runtime_minutes, content_rating, rating, added_at,
                   poster_url IS NOT NULL, backdrop_url IS NOT NULL, studio, tmdb_id
            FROM movies WHERE has_file = 1"""):
        file_seconds, file_added = files.get(r[0], (None, None))
        seconds = file_seconds or ((r[3] or 0) * 60) or None
        out.append([r[0], r[1], r[2], int(seconds) if seconds else None, r[4],
                    round(r[5], 1) if r[5] else None, genres.get(r[0], []),
                    file_added or r[6], 1 if r[7] else 0, 1 if r[8] else 0, r[9], r[10]])
    return out


def sample_evenly(items: list, n: int) -> list:
    """n items spread across the list, first and last included, in order."""
    if len(items) <= n:
        return list(items)
    if n <= 1:
        return items[:1]
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


def owned_shows(conn, sample: int = EPISODE_SAMPLE) -> List[list]:
    """shows with an owned episode. each carries its owned episode count and a
    sample of those episodes as [episode_id, season, episode, seconds, title],
    in airing order."""
    shows: Dict[int, list] = {}
    for r in conn.execute("""
            SELECT id, title, year, network, content_rating, rating, added_at, runtime_minutes,
                   poster_url IS NOT NULL, backdrop_url IS NOT NULL, tmdb_id
            FROM shows"""):
        shows[r[0]] = list(r)
    episodes: Dict[int, list] = {}
    for r in conn.execute("""
            SELECT show_id, id, season_number, episode_number, runtime_minutes, title
            FROM episodes WHERE has_file = 1
            ORDER BY show_id, season_number = 0, season_number, episode_number"""):
        episodes.setdefault(r[0], []).append(r)
    genres = _genre_map(conn, "show_genres", "show_id")
    out = []
    for show_id, eps in episodes.items():
        s = shows.get(show_id)
        if s is None:
            continue
        fallback = (s[7] or 0) * 60
        picked = [[e[1], e[2], e[3], ((e[4] or 0) * 60) or fallback or None, e[5] or ""]
                  for e in sample_evenly(eps, sample)]
        out.append([s[0], s[1], s[2], s[3], s[4], round(s[5], 1) if s[5] else None,
                    genres.get(show_id, []), s[6], 1 if s[8] else 0, 1 if s[9] else 0,
                    len(eps), picked, s[10]])
    return out


def recent_episodes(conn, limit: int = RECENT_EPISODES) -> List[Dict[str, Any]]:
    """the newest episode files, newest first, one row per episode. a top-n
    sort over media_files, then a lookup for just those episodes."""
    seen: Dict[int, str] = {}
    for r in conn.execute("""
            SELECT episode_id, added_at FROM media_files WHERE episode_id IS NOT NULL
            ORDER BY added_at DESC LIMIT ?""", (int(limit) * 4,)):
        if r[0] not in seen:
            seen[r[0]] = r[1]
        if len(seen) >= limit:
            break
    if not seen:
        return []
    ids = list(seen)
    rows = {r[0]: r for r in conn.execute(
        """SELECT e.id, e.show_id, s.title, s.content_rating, e.season_number, e.episode_number,
                  e.title, e.still_url IS NOT NULL
           FROM episodes e JOIN shows s ON s.id = e.show_id
           WHERE e.id IN (%s)""" % ",".join("?" for _ in ids), ids)}
    out = []
    for episode_id in ids:
        r = rows.get(episode_id)
        if r is None:
            continue
        out.append({"id": r[0], "show_id": r[1], "show_title": r[2], "content_rating": r[3],
                    "season_number": r[4], "episode_number": r[5], "title": r[6],
                    "has_still": bool(r[7]), "added_at": seen[episode_id]})
    return out


def downloaded_youtube(conn, limit: int = YOUTUBE_MAX) -> List[Dict[str, Any]]:
    """youtube videos on disk (the download ledger), newest download first,
    with the duration and channel the follow cache knows."""
    from core.player import youtube

    ledger = list(youtube.downloaded_ids(conn).items())[:int(limit)]
    if not ledger:
        return []
    ids = [vid for vid, _ in ledger]
    found: Dict[str, Any] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for r in conn.execute(
                "SELECT youtube_id, duration, thumbnail_url FROM youtube_channel_videos "
                "WHERE youtube_id IN (%s)" % ",".join("?" for _ in chunk), chunk):
            found[r[0]] = (r[1], r[2])
    titles = {r[0]: r[1] for r in conn.execute("SELECT channel_id, title FROM youtube_channel_meta")}
    out = []
    for vid, entry in ledger:
        duration, thumb = found.get(vid, (None, None))
        out.append({"id": vid, "youtube_id": vid, "title": entry.get("title") or "",
                    "duration_seconds": youtube.parse_duration(duration),
                    "published_at": entry.get("published_at"),
                    "thumbnail_url": youtube.thumbnail(vid, thumb or entry.get("poster_url")),
                    "channel_id": entry.get("channel_id"),
                    "channel_title": titles.get(entry.get("channel_id") or ""),
                    "added_at": entry.get("completed_at")})
    return out


def read_all(conn, sees_movies: bool, sees_shows: bool, sees_youtube: bool) -> Dict[str, Any]:
    return {
        "genres": genre_names(conn) if (sees_movies or sees_shows) else {},
        "movies": owned_movies(conn) if sees_movies else [],
        "shows": owned_shows(conn) if sees_shows else [],
        "recent_episodes": recent_episodes(conn) if sees_shows else [],
        "youtube": downloaded_youtube(conn) if sees_youtube else [],
    }
