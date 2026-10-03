"""on repeat, repeat rewind, flow and blend: mixes read straight off what you play.

every one of these is owned tracks only. a play in listening_history carries
lib2_track_id when it matched a library track (125k of boulder's 162k do), so
these mixes play the moment you press play, nothing to download first.

- on repeat: what you've played most in the last 30 days.
- repeat rewind: what you had on repeat 2 to 12 months ago and then stopped.
- flow: an endless-feeling queue of your favourites mixed with the corners
  of your library you never play: rarely-played tracks by artists you love,
  and owned tracks by artists similar to them. new every time you press it.
- blend: one mix out of two people's listening. only between profiles that
  each have their own listening history (#1293); otherwise it's you blended
  with yourself.

each reads the listening pile the profile owns (core.listening_scope).
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("personalized.for_you")

ON_REPEAT_DAYS = 30
ON_REPEAT_MIN_PLAYS = 2
REWIND_FROM_DAYS = 365
REWIND_QUIET_DAYS = 60
REWIND_MIN_PLAYS = 4
MIX_SIZE = 30
FLOW_SIZE = 60
BLEND_SIZE = 50
BLEND_DAYS = 180
# a mix needs at least this many tracks to be worth a card
MIN_TRACKS = 8


def _stamp(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _chunks(items: Sequence[Any], n: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


# ── reading ─────────────────────────────────────────────────────────────────

def _plays(conn, owner: int, since: str, until: Optional[str] = None) -> Dict[str, int]:
    """library track id -> plays in [since, until). Library v2: the catalogue
    link is ``lib2_track_id``; ``db_track_id`` is the media server's own id."""
    sql = ("SELECT lib2_track_id, COUNT(*) FROM listening_history "
           "WHERE profile_id = ? AND lib2_track_id IS NOT NULL AND played_at >= ?")
    args: List[Any] = [owner, since]
    if until:
        sql += " AND played_at < ?"
        args.append(until)
    sql += " GROUP BY lib2_track_id"
    return {str(tid): n for tid, n in conn.execute(sql, args).fetchall()}


# Library v2: a track's artist is its first credit, else its album's artist.
_TRACK_ROW_SQL = """
    SELECT t.id, t.title, t.duration, COALESCE(credited.name, ar.name), al.title,
           COALESCE(al.image_url, ar.image_url)
    FROM lib2_tracks t
    JOIN lib2_albums al ON al.id = t.album_id
    JOIN lib2_artists ar ON ar.id = al.primary_artist_id
    LEFT JOIN lib2_artists credited ON credited.id = (
         SELECT ta.artist_id FROM lib2_track_artists ta
          WHERE ta.track_id = t.id
          ORDER BY CASE ta.role WHEN 'primary' THEN 0 ELSE 1 END,
                   ta.position, ta.artist_id LIMIT 1)
"""


def _owned(conn, track_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """library rows for these ids that are actually on disk, mix-track ready."""
    from core.library2.sql_util import owned_sql
    from core.metadata import normalize_image_url
    out: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunks(list(track_ids), 900):
        rows = conn.execute(
            f"""{_TRACK_ROW_SQL}
            WHERE {owned_sql('track', 't')}
              AND t.id IN ({','.join('?' * len(chunk))})
            """,
            list(chunk),
        ).fetchall()
        for tid, title, duration, artist, album, cover in rows:
            if not title or not artist:
                continue
            out[str(tid)] = {
                'id': str(tid), 'title': title, 'artist': artist, 'album': album or '',
                'duration': duration or 0,
                'cover': normalize_image_url(cover) if cover else None,
            }
    return out


def mix_track(row: Dict[str, Any]) -> Dict[str, Any]:
    """the shape daily mixes and moods use, so the page treats them all alike."""
    return {
        'name': row['title'],
        'artists': [{'name': row['artist']}],
        'album': {'name': row.get('album') or '',
                  'images': [{'url': row['cover']}] if row.get('cover') else []},
        'duration_ms': int(row.get('duration') or 0),
        'owned': True,
    }


# ── picking (pure) ──────────────────────────────────────────────────────────

def ranked(counts: Dict[str, int], owned: Dict[str, Dict[str, Any]], size: int,
           per_artist: int = 3) -> List[Dict[str, Any]]:
    """most-played first, a cap per artist so one artist can't be the whole mix."""
    out: List[Dict[str, Any]] = []
    per: Dict[str, int] = {}
    for tid, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        row = owned.get(tid)
        if not row:
            continue
        key = row['artist'].lower()
        if per.get(key, 0) >= per_artist:
            continue
        per[key] = per.get(key, 0) + 1
        out.append(row)
        if len(out) >= size:
            break
    return out


def weighted_sample(rows: Sequence[Dict[str, Any]], weights: Sequence[float], k: int,
                    rng: random.Random) -> List[Dict[str, Any]]:
    """k rows without replacement, heavier weights more likely (u^(1/w) keys)."""
    keyed = [(rng.random() ** (1.0 / w), r) for r, w in zip(rows, weights, strict=True) if w > 0]
    keyed.sort(key=lambda kv: kv[0], reverse=True)
    return [r for _, r in keyed[:k]]


def interleave(streams: Sequence[Sequence[Dict[str, Any]]], size: int,
               per_artist: int = 3) -> List[Dict[str, Any]]:
    """round-robin across streams, skipping repeats and artists at their cap."""
    out: List[Dict[str, Any]] = []
    seen, per = set(), {}
    idx = [0] * len(streams)
    while len(out) < size and any(idx[i] < len(s) for i, s in enumerate(streams)):
        for i, s in enumerate(streams):
            while idx[i] < len(s):
                row = s[idx[i]]
                idx[i] += 1
                key = row['artist'].lower()
                if row['id'] in seen or per.get(key, 0) >= per_artist:
                    continue
                seen.add(row['id'])
                per[key] = per.get(key, 0) + 1
                out.append(row)
                break
            if len(out) >= size:
                break
    return out


_HOLIDAY = ('christmas', 'xmas', 'x-mas', 'navidad', 'noel', 'jingle', 'santa', 'holiday')


def in_season(row: Dict[str, Any], now: datetime) -> bool:
    """a christmas song is for november and december. flow served colbie
    caillat's merry little christmas in september."""
    if now.month in (11, 12):
        return True
    text = f"{row.get('title', '')} {row.get('album', '')}".lower()
    return not any(word in text for word in _HOLIDAY)


# ── the mixes ───────────────────────────────────────────────────────────────

def on_repeat(conn, owner: int, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or _now()
    counts = {k: v for k, v in _plays(conn, owner, _stamp(now - timedelta(days=ON_REPEAT_DAYS))).items()
              if v >= ON_REPEAT_MIN_PLAYS}
    return ranked(counts, _owned(conn, list(counts)), MIX_SIZE)


def repeat_rewind(conn, owner: int, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """heavy then, silent lately: played REWIND_MIN_PLAYS+ times in the year
    before the quiet window, and not once inside it."""
    now = now or _now()
    quiet_since = _stamp(now - timedelta(days=REWIND_QUIET_DAYS))
    then = _plays(conn, owner, _stamp(now - timedelta(days=REWIND_FROM_DAYS)), quiet_since)
    lately = _plays(conn, owner, quiet_since)
    counts = {k: v for k, v in then.items() if v >= REWIND_MIN_PLAYS and k not in lately}
    return ranked(counts, _owned(conn, list(counts)), MIX_SIZE)


def _artists_named(conn, names: Sequence[str]) -> List[tuple]:
    """library artist rows (id, name, spotify, deezer, itunes, mbid) for these
    names. exact names go through the name index; only the ones that miss
    (scrobbled casing, say) cost a case-insensitive pass over artists.
    joining a LOWER(name) filter onto tracks scanned every track (9+ min)."""
    cols = ("id, name, spotify_id, json_extract(external_ids, '$.deezer'),"
            " json_extract(external_ids, '$.itunes'), musicbrainz_id")
    names = [n for n in dict.fromkeys(names) if n]
    if not names:
        return []
    found: List[tuple] = []
    for chunk in _chunks(names, 900):
        found += conn.execute(
            f"SELECT {cols} FROM lib2_artists WHERE name IN ({','.join('?' * len(chunk))})",
            list(chunk)).fetchall()
    have = {(r[1] or '').lower() for r in found}
    missing = {n.lower() for n in names} - have
    if missing:
        found += [r for r in conn.execute(f"SELECT {cols} FROM lib2_artists WHERE name IS NOT NULL")
                  if (r[1] or '').lower() in missing]
    return found


def _library_by_artists(conn, artist_names: Sequence[str], max_plays: int,
                        artist_rows: Optional[List[tuple]] = None) -> List[Dict[str, Any]]:
    """owned tracks by these artists that you've played at most max_plays times."""
    from core.library2.sql_util import owned_sql
    from core.metadata import normalize_image_url
    out: List[Dict[str, Any]] = []
    rows_for = artist_rows if artist_rows is not None else _artists_named(conn, artist_names)
    ids = [r[0] for r in rows_for]
    for chunk in _chunks(ids, 450):
        marks = ','.join('?' * len(chunk))
        # credited on the track, or the album's artist
        rows = conn.execute(
            f"""{_TRACK_ROW_SQL}
            WHERE (al.primary_artist_id IN ({marks})
                   OR t.id IN (SELECT ta.track_id FROM lib2_track_artists ta
                                WHERE ta.artist_id IN ({marks})))
              AND {owned_sql('track', 't')}
              AND COALESCE(t.play_count, 0) <= ?
            """,
            [*chunk, *chunk, max_plays],
        ).fetchall()
        for tid, title, duration, artist, album, cover in rows:
            if title and artist:
                out.append({'id': str(tid), 'title': title, 'artist': artist, 'album': album or '',
                            'duration': duration or 0,
                            'cover': normalize_image_url(cover) if cover else None})
    return out


def _top_artists(conn, owner: int, since: str, limit: int) -> List[str]:
    rows = conn.execute(
        "SELECT artist, COUNT(*) n FROM listening_history WHERE profile_id = ? "
        "AND played_at >= ? AND artist IS NOT NULL AND artist != '' "
        "GROUP BY LOWER(artist) ORDER BY n DESC LIMIT ?",
        (owner, since, limit),
    ).fetchall()
    return [r[0] for r in rows]


def _similar_names(conn, artists: Sequence[str], profile_id: int, limit: int,
                   artist_rows: Optional[List[tuple]] = None) -> List[str]:
    """artists similar to yours, by name. similar_artists is keyed by the
    SOURCE ids your artists carry (spotify/deezer/itunes/mbid), never artists.id."""
    rows_for = artist_rows if artist_rows is not None else _artists_named(conn, artists)
    source_ids = sorted({str(x) for r in rows_for for x in r[2:] if x})
    if not source_ids:
        return []
    counts: Dict[str, List[Any]] = {}
    for chunk in _chunks(source_ids, 900):
        for name, in conn.execute(
                f"SELECT similar_artist_name FROM similar_artists WHERE profile_id = ? "
                f"AND source_artist_id IN ({','.join('?' * len(chunk))})",
                [profile_id, *chunk]).fetchall():
            if name:
                entry = counts.setdefault(name.lower(), [name, 0])
                entry[1] += 1
    mine = {a.lower() for a in artists}
    ranked_names = sorted((v for k, v in counts.items() if k not in mine), key=lambda v: -v[1])
    return [v[0] for v in ranked_names[:limit]]


def flow(conn, owner: int, profile_id: int, *, now: Optional[datetime] = None,
         rng: Optional[random.Random] = None, size: int = FLOW_SIZE) -> List[Dict[str, Any]]:
    """favourites, the tracks of theirs you skip past, and their neighbours in
    your library, woven together. a fresh draw every time."""
    now = now or _now()
    rng = rng or random.Random()
    recent = _plays(conn, owner, _stamp(now - timedelta(days=90)))
    # the 400 most played are plenty to draw favourites from, and looking up
    # all ~3.6k recent tracks was most of flow's time
    top_ids = sorted(recent, key=lambda t: -recent[t])[:400]
    owned_recent = _owned(conn, top_ids)
    fav_rows = list(owned_recent.values())
    favourites = weighted_sample(fav_rows, [1.0 + math.log1p(recent[r['id']]) for r in fav_rows],
                                 size, rng)
    top = _top_artists(conn, owner, _stamp(now - timedelta(days=365)), 30)
    top_rows = _artists_named(conn, top)
    unplayed = [r for r in _library_by_artists(conn, top, 1, top_rows) if in_season(r, now)]
    rng.shuffle(unplayed)
    near = _similar_names(conn, top, profile_id, 60, top_rows)
    neighbours = [r for r in _library_by_artists(conn, near, 3) if in_season(r, now)]
    rng.shuffle(neighbours)
    favourites = [r for r in favourites if in_season(r, now)]
    # roughly 2 favourites : 1 deep cut : 2 neighbours
    return interleave([favourites, unplayed, neighbours, favourites[1::2], neighbours[1::2]],
                      size)


def blend(conn, owner_a: int, owner_b: int, *, now: Optional[datetime] = None,
          size: int = BLEND_SIZE) -> List[Dict[str, Any]]:
    """what you both play leads, then each person's favourites by artists you
    both play, then each person's own favourites, taking turns."""
    now = now or _now()
    since = _stamp(now - timedelta(days=BLEND_DAYS))
    a, b = _plays(conn, owner_a, since), _plays(conn, owner_b, since)
    owned = _owned(conn, list(set(a) | set(b)))
    both = sorted((t for t in a if t in b and t in owned), key=lambda t: -(a[t] + b[t]))
    artists_a = {owned[t]['artist'].lower() for t in a if t in owned}
    artists_b = {owned[t]['artist'].lower() for t in b if t in owned}
    shared = artists_a & artists_b

    def tops(counts, shared_only):
        rows = [owned[t] for t in sorted(counts, key=lambda t: -counts[t]) if t in owned]
        return [r for r in rows if (r['artist'].lower() in shared) == shared_only]

    return interleave([[owned[t] for t in both], tops(a, True), tops(b, True),
                       tops(a, False), tops(b, False)], size)


# ── the payload ─────────────────────────────────────────────────────────────

def _card(key: str, name: str, subtitle: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(rows) < MIN_TRACKS:
        return None
    return {'key': key, 'name': name, 'subtitle': subtitle,
            'tracks': [mix_track(r) for r in rows]}


def _profile_names(conn) -> Dict[int, str]:
    try:
        return {int(i): n for i, n in conn.execute("SELECT id, name FROM profiles").fetchall()}
    except Exception:  # noqa: BLE001 - names are a label
        return {}


def build_for_you(database, profile_id: int, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """on repeat, repeat rewind and any blends for this profile."""
    from core.listening_scope import listening_owner, listening_owners
    owner = listening_owner(database, profile_id)
    others = [o for o in listening_owners(database) if o != owner]
    conn = database._get_connection()
    try:
        mixes = [
            _card('on_repeat', 'On Repeat', "What you can't stop playing right now",
                  on_repeat(conn, owner, now)),
            _card('repeat_rewind', 'Repeat Rewind', 'Your old favourites, gone quiet lately',
                  repeat_rewind(conn, owner, now)),
        ]
        names = _profile_names(conn)
        me = names.get(int(profile_id or 1), 'You')
        for other in others[:3]:
            them = names.get(other, 'Someone')
            mixes.append(_card(f'blend_{other}', f'{me} + {them}',
                               f'A blend of what you and {them} play', blend(conn, owner, other, now=now)))
    finally:
        conn.close()
    return {'mixes': [m for m in mixes if m]}


def build_flow(database, profile_id: int) -> Dict[str, Any]:
    from core.listening_scope import listening_owner
    owner = listening_owner(database, profile_id)
    conn = database._get_connection()
    try:
        rows = flow(conn, owner, int(profile_id or 1))
    finally:
        conn.close()
    return {'tracks': [mix_track(r) for r in rows]}
