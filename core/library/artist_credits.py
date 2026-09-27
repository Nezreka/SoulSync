"""every artist credited on a track or album, as the metadata sources report it.

tracks.artist_id and albums.artist_id only hold the primary artist. spotify and deezer hand back
the full credit list with ids when the workers match a track, the workers just
used to throw it away. now they save it here, one row per credited artist per
source. albums the same way, so watch the throne shows on kanye's page as well
as jay-z's. (tidal's client drops the ids, qobuz and itunes only ever give the
primary artist, so they have nothing to add yet.)

rows point at the provider's artist id, not at our artists table. a featured
artist who isn't in the library still gets a row (name + provider id) but never
becomes a library artist, so the artist grid and the enrichment workers don't
fill up with people who own zero albums. linking to a library artist happens at
read time, through the provider id columns the workers already keep on artists,
so a credit starts linking the moment that artist gets matched.

this is separate from track_credits on purpose. track_credits is derived from
the track_artist string by regex and gets wiped whenever that string changes;
these rows come from the source and only change when the source match does.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("library.artist_credits")

# source -> (tracks column holding the matched track id, artists column holding
# the matched artist id)
SOURCES: Dict[str, Tuple[str, str]] = {
    'spotify': ('spotify_track_id', 'spotify_artist_id'),
    'deezer': ('deezer_id', 'deezer_id'),
}

# source -> albums column holding the matched album id
ALBUM_SOURCES: Dict[str, str] = {
    'spotify': 'spotify_album_id',
    'deezer': 'deezer_id',
}


def ensure_schema(cursor) -> None:
    """create the tables, index and the triggers that keep them honest."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS track_artist_credits (
            track_id TEXT NOT NULL,
            source TEXT NOT NULL,
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            source_artist_id TEXT,
            PRIMARY KEY (track_id, source, position)
        ) WITHOUT ROWID
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_track_artist_credits_artist "
                   "ON track_artist_credits (source, source_artist_id)")
    # tracks whose match changed and need their credits fetched again. the
    # workers read this instead of sweeping the library, a sweep of a 300k
    # track library with nothing left to do still took 24s
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS track_artist_credits_pending (
            track_id TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (source, track_id)
        ) WITHOUT ROWID
    """)
    cursor.execute("DROP TRIGGER IF EXISTS trg_track_artist_credits_gone")
    cursor.execute("""
        CREATE TRIGGER trg_track_artist_credits_gone
        AFTER DELETE ON tracks
        BEGIN
            DELETE FROM track_artist_credits WHERE track_id = OLD.id;
            DELETE FROM track_artist_credits_pending WHERE track_id = OLD.id;
        END
    """)
    cursor.execute("PRAGMA table_info(tracks)")
    track_columns = {row[1] for row in cursor.fetchall()}
    for source, (track_col, _artist_col) in SOURCES.items():
        if track_col not in track_columns:
            continue
        # the credits belong to the match. when the match changes or gets
        # cleared (manual rematch, match reset) the old credits are wrong, so
        # they go, and a new match gets queued for fresh ones. a worker that
        # set the id itself saves the credits right after, which clears the
        # queue entry. dropped and made again each boot so a changed body lands.
        cursor.execute(f"DROP TRIGGER IF EXISTS trg_track_artist_credits_{source}_rematch")
        cursor.execute(f"""
            CREATE TRIGGER trg_track_artist_credits_{source}_rematch
            AFTER UPDATE OF {track_col} ON tracks
            WHEN NEW.{track_col} IS NOT OLD.{track_col}
            BEGIN
                DELETE FROM track_artist_credits
                WHERE track_id = NEW.id AND source = '{source}';
                INSERT OR IGNORE INTO track_artist_credits_pending (track_id, source)
                SELECT NEW.id, '{source}'
                WHERE NEW.{track_col} IS NOT NULL AND NEW.{track_col} != '';
            END
        """)

    # albums, the same three pieces
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS album_artist_credits (
            album_id TEXT NOT NULL,
            source TEXT NOT NULL,
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            source_artist_id TEXT,
            PRIMARY KEY (album_id, source, position)
        ) WITHOUT ROWID
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_artist_credits_artist "
                   "ON album_artist_credits (source, source_artist_id)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS album_artist_credits_pending (
            album_id TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (source, album_id)
        ) WITHOUT ROWID
    """)
    cursor.execute("DROP TRIGGER IF EXISTS trg_album_artist_credits_gone")
    cursor.execute("""
        CREATE TRIGGER trg_album_artist_credits_gone
        AFTER DELETE ON albums
        BEGIN
            DELETE FROM album_artist_credits WHERE album_id = OLD.id;
            DELETE FROM album_artist_credits_pending WHERE album_id = OLD.id;
        END
    """)
    cursor.execute("PRAGMA table_info(albums)")
    album_columns = {row[1] for row in cursor.fetchall()}
    for source, album_col in ALBUM_SOURCES.items():
        if album_col not in album_columns:
            continue
        cursor.execute(f"DROP TRIGGER IF EXISTS trg_album_artist_credits_{source}_rematch")
        cursor.execute(f"""
            CREATE TRIGGER trg_album_artist_credits_{source}_rematch
            AFTER UPDATE OF {album_col} ON albums
            WHEN NEW.{album_col} IS NOT OLD.{album_col}
            BEGIN
                DELETE FROM album_artist_credits
                WHERE album_id = NEW.id AND source = '{source}';
                INSERT OR IGNORE INTO album_artist_credits_pending (album_id, source)
                SELECT NEW.id, '{source}'
                WHERE NEW.{album_col} IS NOT NULL AND NEW.{album_col} != '';
            END
        """)


def normalize_credits(artists: Any) -> List[Tuple[str, Optional[str]]]:
    """turn whatever a client handed back into [(name, provider_id)].

    takes dicts ({'name', 'id'}), objects with .name/.id, or bare names. drops
    blanks and repeats, keeps the source's order (first is the primary)."""
    out: List[Tuple[str, Optional[str]]] = []
    seen = set()
    if not artists:
        return out
    if isinstance(artists, (str, dict)):
        artists = [artists]
    for a in artists:
        if isinstance(a, str):
            name, aid = a, None
        elif isinstance(a, dict):
            name, aid = a.get('name'), a.get('id')
        else:
            name, aid = getattr(a, 'name', None), getattr(a, 'id', None)
        name = str(name or '').strip()
        if not name:
            continue
        aid = str(aid).strip() if aid not in (None, '') else None
        key = aid or name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((name, aid))
    return out


def _save(cursor, kind: str, entity_id: Any, source: str, artists: Any) -> int:
    """replace this source's credits for one track or album, and take it off
    the queue. kind is 'track' or 'album'."""
    if entity_id in (None, ''):
        return 0
    credits = normalize_credits(artists)
    if not any(aid for _name, aid in credits):
        return 0
    eid = str(entity_id)
    table, key = f"{kind}_artist_credits", f"{kind}_id"
    cursor.execute(f"DELETE FROM {table}_pending WHERE {key} = ? AND source = ?", (eid, source))
    cursor.execute(f"DELETE FROM {table} WHERE {key} = ? AND source = ?", (eid, source))
    cursor.executemany(
        f"INSERT INTO {table} ({key}, source, position, name, source_artist_id) "
        "VALUES (?, ?, ?, ?, ?)",
        [(eid, source, i, name, aid) for i, (name, aid) in enumerate(credits)],
    )
    return len(credits)


def save_track_credits(cursor, track_id: Any, source: str, artists: Any) -> int:
    """replace this source's credits for a track. returns rows written.

    writes nothing, and leaves what's there alone, when the list carries no
    artist ids at all. names alone can't link to anyone, and a names-only row
    would stop the backfill from ever going back for the real thing."""
    if source not in SOURCES:
        return 0
    return _save(cursor, 'track', track_id, source, artists)


def save_album_credits(cursor, album_id: Any, source: str, artists: Any) -> int:
    """save_track_credits for an album's artists."""
    if source not in ALBUM_SOURCES:
        return 0
    return _save(cursor, 'album', album_id, source, artists)


def try_save_album_credits(cursor, album_id: Any, source: str, artists: Any) -> int:
    """save_album_credits that never raises, same reason as the track one."""
    try:
        return save_album_credits(cursor, album_id, source, artists)
    except Exception as e:
        logger.debug("Could not save %s credits for album %s: %s", source, album_id, e)
        return 0


def try_save_track_credits(cursor, track_id: Any, source: str, artists: Any) -> int:
    """save_track_credits that never raises. the workers call it inside the
    same transaction as the match, and a credit failure must not lose the
    match."""
    try:
        return save_track_credits(cursor, track_id, source, artists)
    except Exception as e:
        logger.debug("Could not save %s credits for track %s: %s", source, track_id, e)
        return 0


def _missing_sql(source: str) -> str:
    track_col = SOURCES[source][0]
    return f"""
        t.{track_col} IS NOT NULL AND t.{track_col} != ''
        AND NOT EXISTS (
            SELECT 1 FROM track_artist_credits c
            WHERE c.track_id = t.id AND c.source = '{source}'
        )"""


def tracks_missing_credits(cursor, source: str, limit: int = 50,
                           after: Any = None) -> List[Tuple[Any, str, Any]]:
    """matched tracks with no credits yet from this source, in id order, as
    [(track_id, provider_track_id, album_id)]. `after` resumes past the last
    id handed out, so a track the source can't answer for isn't asked again
    on every pass."""
    if source not in SOURCES:
        return []
    params: List[Any] = []
    after_sql = ''
    if after is not None:
        after_sql = ' AND t.id > ?'
        params.append(after)
    cursor.execute(f"""
        SELECT t.id, t.{SOURCES[source][0]}, t.album_id
        FROM tracks t
        WHERE {_missing_sql(source)}{after_sql}
        ORDER BY t.id
        LIMIT ?
    """, params + [limit])
    return [(r[0], str(r[1]), r[2]) for r in cursor.fetchall()]


def album_missing_credits(cursor, source: str,
                          after: Any = None) -> List[Tuple[Any, str, Any]]:
    """the next album (in id order, past `after`) with matched tracks missing
    credits from this source, and those tracks. same shape as
    tracks_missing_credits. for sources where one album call answers for
    every track on it."""
    if source not in SOURCES:
        return []
    params: List[Any] = []
    after_sql = ''
    if after is not None:
        after_sql = ' AND t.album_id > ?'
        params.append(after)
    cursor.execute(f"""
        SELECT t.album_id FROM tracks t
        WHERE t.album_id IS NOT NULL AND {_missing_sql(source)}{after_sql}
        ORDER BY t.album_id
        LIMIT 1
    """, params)
    row = cursor.fetchone()
    if not row:
        return []
    cursor.execute(f"""
        SELECT t.id, t.{SOURCES[source][0]}, t.album_id
        FROM tracks t
        WHERE t.album_id = ? AND {_missing_sql(source)}
        ORDER BY t.id
    """, (row[0],))
    return [(r[0], str(r[1]), r[2]) for r in cursor.fetchall()]


def _pending_batch(cursor, source: str, limit: int, by_album: bool) -> List[Tuple[Any, str, Any]]:
    """claim queued tracks: hand them out and take them off the queue. a track
    the source can't answer for then isn't asked about forever. in album mode
    only one album's worth, so it stays one call."""
    track_col = SOURCES[source][0]
    cursor.execute(f"""
        SELECT p.track_id, t.{track_col}, t.album_id
        FROM track_artist_credits_pending p
        LEFT JOIN tracks t ON t.id = p.track_id
        WHERE p.source = ?
        LIMIT ?
    """, (source, limit if not by_album else 1))
    rows = cursor.fetchall()
    if not rows:
        return []
    if by_album and rows[0][2] is not None and rows[0][1]:
        cursor.execute(f"""
            SELECT p.track_id, t.{track_col}, t.album_id
            FROM track_artist_credits_pending p
            JOIN tracks t ON t.id = p.track_id
            WHERE p.source = ? AND t.album_id = ?
        """, (source, rows[0][2]))
        rows = cursor.fetchall()
    cursor.executemany("DELETE FROM track_artist_credits_pending WHERE source = ? AND track_id = ?",
                       [(source, r[0]) for r in rows])
    # a queued track that's gone or lost its match since has nothing to fetch
    return [(r[0], str(r[1]), r[2]) for r in rows if r[1]]


def requeue(cursor, source: str, tracks: Iterable[Tuple[Any, str, Any]]) -> None:
    """put claimed tracks back, for when the fetch failed for a reason that
    isn't about the track (rate limit, network)."""
    cursor.executemany(
        "INSERT OR IGNORE INTO track_artist_credits_pending (track_id, source) VALUES (?, ?)",
        [(str(t[0]), source) for t in tracks])


def requeue_albums(cursor, source: str, albums: Iterable[Tuple[Any, str, Any]]) -> None:
    """requeue for albums."""
    cursor.executemany(
        "INSERT OR IGNORE INTO album_artist_credits_pending (album_id, source) VALUES (?, ?)",
        [(str(a[0]), source) for a in albums])


class CreditsBackfill:
    """what a worker should fetch credits for next.

    first the queue (tracks rematched since). then, once per install, a sweep
    of the tracks matched before credits existed. the sweep keeps its place in
    the metadata table so a restart carries on instead of starting over, and
    marks itself done at the end. it never runs again after that; the queue
    covers everything since.

    by_album hands out one album's tracks at a time, for a source that answers
    a whole album in one call. otherwise batch_size tracks in id order."""

    sweep_key = "artist_credits_sweep_{source}"

    def __init__(self, source: str, batch_size: int = 50, by_album: bool = False):
        self.source = source
        self.batch_size = batch_size
        self.by_album = by_album
        self._key = self.sweep_key.format(source=source)
        self._sweep_done: Optional[bool] = None

    def _claim(self, cursor) -> List[Tuple[Any, str, Any]]:
        return _pending_batch(cursor, self.source, self.batch_size, self.by_album)

    def _sweep_batch(self, cursor, after: Any) -> List[Tuple[Any, str, Any]]:
        if self.by_album:
            return album_missing_credits(cursor, self.source, after)
        return tracks_missing_credits(cursor, self.source, self.batch_size, after)

    def _place(self, batch: List[Tuple[Any, str, Any]]) -> Any:
        return batch[-1][2] if self.by_album else batch[-1][0]

    def _read_sweep(self, cursor) -> Tuple[bool, Any]:
        cursor.execute("SELECT value FROM metadata WHERE key = ?", (self._key,))
        row = cursor.fetchone()
        if not row or row[0] is None:
            return False, None
        try:
            state = json.loads(row[0])
        except (TypeError, ValueError):
            return False, None
        return bool(state.get('done')), state.get('after')

    def _write_sweep(self, cursor, done: bool, after: Any) -> None:
        cursor.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (self._key, json.dumps({'done': done, 'after': after})))

    def next_batch(self, cursor) -> List[Tuple[Any, str, Any]]:
        """the next tracks to fetch credits for, [] when there's nothing.
        commits its own bookkeeping on the cursor's connection."""
        batch = self._claim(cursor)
        if batch:
            cursor.connection.commit()
            return batch
        if self._sweep_done is None:
            self._sweep_done, _after = self._read_sweep(cursor)
        if self._sweep_done:
            # the claim may have dropped stale queue rows
            cursor.connection.commit()
            return []
        _done, after = self._read_sweep(cursor)
        batch = self._sweep_batch(cursor, after)
        if not batch:
            self._write_sweep(cursor, True, None)
            self._sweep_done = True
        else:
            self._write_sweep(cursor, False, self._place(batch))
        cursor.connection.commit()
        return batch


class AlbumCreditsBackfill(CreditsBackfill):
    """the same queue-then-one-sweep, over matched albums. hands out
    [(album_id, provider_album_id, None)]."""

    sweep_key = "album_artist_credits_sweep_{source}"

    def _claim(self, cursor) -> List[Tuple[Any, str, Any]]:
        col = ALBUM_SOURCES[self.source]
        cursor.execute(f"""
            SELECT p.album_id, al.{col}
            FROM album_artist_credits_pending p
            LEFT JOIN albums al ON al.id = p.album_id
            WHERE p.source = ?
            LIMIT ?
        """, (self.source, self.batch_size))
        rows = cursor.fetchall()
        cursor.executemany("DELETE FROM album_artist_credits_pending WHERE source = ? AND album_id = ?",
                           [(self.source, r[0]) for r in rows])
        return [(r[0], str(r[1]), None) for r in rows if r[1]]

    def _sweep_batch(self, cursor, after: Any) -> List[Tuple[Any, str, Any]]:
        col = ALBUM_SOURCES[self.source]
        params: List[Any] = [self.source]
        after_sql = ''
        if after is not None:
            after_sql = ' AND al.id > ?'
            params.append(after)
        cursor.execute(f"""
            SELECT al.id, al.{col} FROM albums al
            WHERE al.{col} IS NOT NULL AND al.{col} != ''
              AND NOT EXISTS (
                  SELECT 1 FROM album_artist_credits c
                  WHERE c.album_id = al.id AND c.source = ?
              ){after_sql}
            ORDER BY al.id
            LIMIT ?
        """, params + [self.batch_size])
        return [(r[0], str(r[1]), None) for r in cursor.fetchall()]

    def _place(self, batch: List[Tuple[Any, str, Any]]) -> Any:
        return batch[-1][0]


def artist_source_ids(cursor, artist_id: Any) -> List[Tuple[str, str]]:
    """[(source, provider_artist_id)] for every source this artist is matched on."""
    cursor.execute("PRAGMA table_info(artists)")
    columns = {row[1] for row in cursor.fetchall()}
    wanted = [(s, col) for s, (_t, col) in SOURCES.items() if col in columns]
    if not wanted:
        return []
    cursor.execute(f"SELECT {', '.join(col for _s, col in wanted)} FROM artists WHERE id = ?",
                   (artist_id,))
    row = cursor.fetchone()
    if not row:
        return []
    return [(s, str(row[i])) for i, (s, _col) in enumerate(wanted) if row[i] not in (None, '')]


def appears_on(cursor, artist_id: Any, limit: int = 200,
               scope_sql: str = '1=1', scope_params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """tracks this artist is credited on that are filed under someone else.

    their own tracks are already on the page, so those are left out. one row
    per track even when several sources credit them. scope_sql is the caller's
    library scope over t.owner_profile_id, so a profile only sees its own."""
    ids = artist_source_ids(cursor, artist_id)
    if not ids:
        return []
    where = ' OR '.join('(c.source = ? AND c.source_artist_id = ?)' for _ in ids)
    params: List[Any] = [v for pair in ids for v in pair]
    cursor.execute(f"""
        SELECT t.id, t.title, t.track_number, t.duration, t.file_path,
               al.id, al.title, al.thumb_url, al.year,
               ar.id, ar.name
        FROM tracks t
        JOIN albums al ON al.id = t.album_id
        JOIN artists ar ON ar.id = t.artist_id
        WHERE t.id IN (SELECT c.track_id FROM track_artist_credits c WHERE {where})
          AND t.artist_id != ?
          AND {scope_sql}
        ORDER BY al.year DESC, al.title, t.track_number
        LIMIT ?
    """, params + [artist_id] + list(scope_params) + [limit])
    rows = cursor.fetchall()
    if not rows:
        return []
    track_ids = [str(r[0]) for r in rows]
    credits = credited_names(cursor, track_ids)
    return [{
        'id': r[0],
        'title': r[1],
        'track_number': r[2],
        'duration': r[3],
        'file_path': r[4],
        'album_id': r[5],
        'album_title': r[6],
        'album_thumb_url': r[7],
        'year': r[8],
        'artist_id': r[9],
        'artist_name': r[10],
        'credits': credits.get(str(r[0]), []),
    } for r in rows]


def credited_names(cursor, track_ids: Sequence[str]) -> Dict[str, List[str]]:
    """{track_id: [names in credit order]}, from whichever source credited the
    track with the most artists (they mostly agree, the fuller list wins)."""
    if not track_ids:
        return {}
    ph = ','.join('?' for _ in track_ids)
    cursor.execute(f"""
        SELECT track_id, source, position, name FROM track_artist_credits
        WHERE track_id IN ({ph}) ORDER BY track_id, source, position
    """, list(track_ids))
    by_track: Dict[str, Dict[str, List[str]]] = {}
    for tid, source, _pos, name in cursor.fetchall():
        by_track.setdefault(str(tid), {}).setdefault(source, []).append(name)
    return {tid: max(lists.values(), key=len) for tid, lists in by_track.items()}


def appears_on_albums(cursor, artist_id: Any, limit: int = 100,
                      scope_sql: str = '1=1', scope_params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """albums this artist is credited on that are filed under someone else,
    with their tracks. a collab album sits under one artist row; this puts it
    on the other artist's page too. scope_sql is over al.owner_profile_id."""
    ids = artist_source_ids(cursor, artist_id)
    if not ids:
        return []
    where = ' OR '.join('(c.source = ? AND c.source_artist_id = ?)' for _ in ids)
    params: List[Any] = [v for pair in ids for v in pair]
    cursor.execute(f"""
        SELECT al.id, al.title, al.thumb_url, al.year, ar.id, ar.name
        FROM albums al
        JOIN artists ar ON ar.id = al.artist_id
        WHERE al.id IN (SELECT c.album_id FROM album_artist_credits c WHERE {where})
          AND al.artist_id != ?
          AND {scope_sql}
        ORDER BY al.year DESC, al.title
        LIMIT ?
    """, params + [artist_id] + list(scope_params) + [limit])
    albums = cursor.fetchall()
    if not albums:
        return []
    album_ids = [str(a[0]) for a in albums]
    ph = ','.join('?' for _ in album_ids)
    cursor.execute(f"""
        SELECT album_id, source, position, name FROM album_artist_credits
        WHERE album_id IN ({ph}) ORDER BY album_id, source, position
    """, album_ids)
    by_album: Dict[str, Dict[str, List[str]]] = {}
    for aid, source, _pos, name in cursor.fetchall():
        by_album.setdefault(str(aid), {}).setdefault(source, []).append(name)
    cursor.execute(f"""
        SELECT album_id, id, title, track_number, disc_number, duration, file_path
        FROM tracks WHERE album_id IN ({ph})
        ORDER BY album_id, COALESCE(disc_number, 1), track_number
    """, album_ids)
    tracks: Dict[str, List[Dict[str, Any]]] = {}
    for r in cursor.fetchall():
        tracks.setdefault(str(r[0]), []).append({
            'id': r[1], 'title': r[2], 'track_number': r[3], 'disc_number': r[4],
            'duration': r[5], 'file_path': r[6],
        })
    return [{
        'id': a[0],
        'title': a[1],
        'thumb_url': a[2],
        'year': a[3],
        'artist_id': a[4],
        'artist_name': a[5],
        'credits': max(by_album[str(a[0])].values(), key=len) if str(a[0]) in by_album else [],
        'tracks': tracks.get(str(a[0]), []),
    } for a in albums]
