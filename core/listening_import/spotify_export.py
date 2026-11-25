"""spotify streaming history, from the privacy export a user downloads.

the browser opens the zip or the json files and sends only the parsed music
plays, in batches. nothing else in the export leaves the device: podcasts,
audiobooks, plays under 30 seconds and plays made in a private session are
dropped before anything is sent, and dropped here again in case a client
didn't.

two export shapes come in, already normalised by the browser to one row:

- account data (StreamingHistory_music_*.json): endTime "YYYY-MM-DD HH:MM",
  artistName, trackName, msPlayed. minute precision, no track id
- extended history (Streaming_History_Audio_*.json, endsong_*.json): ts,
  master_metadata_track_name / _album_artist_name / _album_album_name,
  spotify_track_uri, ms_played, reason_end, skipped, shuffle,
  incognito_mode and the episode / audiobook fields

each kept play becomes a track-level row in listening_history under its own
source, in the importing profile's own pile. a play that ListenBrainz or
Last.fm already brought in (spotify scrobbled it there) is linked to that row,
never stored twice. the import can be kept out of stats and your year
(taste only) and removed again as a whole.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.listening_import.dedup import CANONICAL, insert_import_events
from core.listening_scope import (
    HISTORY_IMPORT_COLUMN,
    SHARED_OWNER,
    TASTE_ONLY_SOURCE,
    TASTE_ONLY_TABLE,
    owner_clause,
)
from utils.logging_config import get_logger

logger = get_logger("listening_import.spotify_export")

SOURCE = TASTE_ONLY_SOURCE
TABLE = TASTE_ONLY_TABLE

# spotify counts a stream from 30 seconds, and so do we
MIN_PLAY_MS = 30_000
# one request's worth; the browser sends the rest in further batches
MAX_BATCH = 5_000
# how far a play and its listenbrainz / last.fm copy may sit apart. the
# extended history knows the second; the account export only the minute, and
# its end time minus the time played lands up to a minute off the scrobble
PRECISE_TOLERANCE_SECONDS = 30
COARSE_TOLERANCE_SECONDS = 90

_TRACK_URI = re.compile(r"^spotify:track:([A-Za-z0-9]{22})$")

TABLE_DDL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        profile_id INTEGER PRIMARY KEY,
        include_in_stats INTEGER NOT NULL DEFAULT 1,
        started_at TEXT,
        updated_at TEXT,
        received INTEGER NOT NULL DEFAULT 0,
        dropped INTEGER NOT NULL DEFAULT 0
    )
"""


def ensure_schema(conn) -> None:
    conn.execute(TABLE_DDL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
    if cols and HISTORY_IMPORT_COLUMN not in cols:
        try:
            conn.execute(f"ALTER TABLE profiles ADD COLUMN {HISTORY_IMPORT_COLUMN} INTEGER DEFAULT 0")
        except Exception as e:  # noqa: BLE001 - added by a parallel request
            logger.debug("history import column add: %s", e)


def _now() -> str:
    return datetime.now(timezone.utc).strftime(CANONICAL)


def import_owner(profile_id: Any) -> int:
    """the pile an import from this profile goes into: the admin's is the
    shared pile, anyone else's is their own. a member never writes into the
    shared history this way."""
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return SHARED_OWNER
    return pid if pid > 0 else SHARED_OWNER


# -- rows ---------------------------------------------------------------------

def _text(value: Any) -> str:
    return str(value or "").strip()


def _ended_at(value: Any) -> Optional[datetime]:
    """the stream's end, utc. both exports write utc: the account one as
    "YYYY-MM-DD HH:MM", the extended one as iso with a Z."""
    text = _text(value)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ms(value: Any) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def track_id_from_uri(uri: Any) -> Optional[str]:
    found = _TRACK_URI.match(_text(uri))
    return found.group(1) if found else None


def normalize_row(row: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(event, None) for a play to keep, (None, why) for one to drop.

    the row is what the browser sends: ended_at, ms_played, track, artist,
    album, uri, coarse (minute precision), and the flags it read
    (incognito, episode)."""
    if not isinstance(row, dict):
        return None, "invalid"
    if row.get("incognito"):
        return None, "private"
    uri = _text(row.get("uri"))
    if row.get("episode") or (uri and not uri.startswith("spotify:track:")):
        return None, "podcast"
    ms_played = _ms(row.get("ms_played"))
    if ms_played < MIN_PLAY_MS:
        return None, "short"
    title, artist = _text(row.get("track")), _text(row.get("artist"))
    ended = _ended_at(row.get("ended_at"))
    if not title or not artist or ended is None:
        return None, "invalid"
    if ended > datetime.now(timezone.utc) + timedelta(days=1):
        return None, "invalid"
    # stored as the play's start, the time a scrobble of it carries, so the
    # same play from listenbrainz or last.fm lines up
    started = ended - timedelta(milliseconds=ms_played)
    track_id = track_id_from_uri(uri)
    return {
        "track_id": track_id,
        "title": title[:500],
        "artist": artist[:500],
        "album": _text(row.get("album"))[:500],
        "played_at": started.strftime(CANONICAL),
        "duration_ms": ms_played,
        "lib2_track_id": None,
        # an export is history, never something to scrobble back
        "scrobble_eligible": False,
        "_coarse": bool(row.get("coarse")),
    }, None


def resolve_library_tracks(database, events: List[Dict[str, Any]]) -> None:
    """link each play to its library track: by spotify id first (exact when
    the library knows it), then by title and artist like the other imports."""
    if not events:
        return
    from core.library2.title_match import resolve_title_artist_pairs, title_fold
    from core.library2.importer import normalize_name

    conn = database._get_connection()
    try:
        ids = sorted({ev["track_id"] for ev in events if ev.get("track_id")})
        by_spotify: Dict[str, int] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for spotify_id, track_id in conn.execute(
                    f"SELECT spotify_id, MIN(id) FROM lib2_tracks WHERE spotify_id IN ({marks}) "
                    f"GROUP BY spotify_id", chunk).fetchall():
                by_spotify[str(spotify_id)] = int(track_id)
        for ev in events:
            if ev.get("track_id") in by_spotify:
                ev["lib2_track_id"] = by_spotify[ev["track_id"]]
        rest = [ev for ev in events if ev.get("lib2_track_id") is None]
        pairs = {(ev["title"], normalize_name(ev["artist"])) for ev in rest}
        if pairs:
            found = resolve_title_artist_pairs(conn.cursor(), pairs)
            for ev in rest:
                ev["lib2_track_id"] = found.get((title_fold(ev["title"]), normalize_name(ev["artist"])))
    finally:
        conn.close()


# -- import -------------------------------------------------------------------

def _mark_pile_owned(conn, owner: int) -> None:
    if owner != SHARED_OWNER:
        conn.execute(f"UPDATE profiles SET {HISTORY_IMPORT_COLUMN} = 1 WHERE id = ?", (owner,))


def _key(title: Any, artist: Any) -> Tuple[str, str]:
    return (_text(title).casefold(), _text(artist).casefold())


def _epoch(played_at: str) -> int:
    return int(datetime.strptime(played_at, CANONICAL).replace(tzinfo=timezone.utc).timestamp())


def _spotify_rows_near(conn, owner: int, events: List[Dict[str, Any]], *, account: bool):
    """{(title, artist): [(epoch, history id)]} of this pile's imported spotify
    plays around these events: the account-export ones (no track id) or the
    extended ones"""
    stamps = [_epoch(ev["played_at"]) for ev in events]
    low, high = min(stamps) - COARSE_TOLERANCE_SECONDS, max(stamps) + COARSE_TOLERANCE_SECONDS
    shape = "track_id IS NULL" if account else "track_id IS NOT NULL"
    found: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
    for history_id, title, artist, played_at in conn.execute(f"""
        SELECT id, title, artist, played_at FROM listening_history
        WHERE played_at >= datetime(?, 'unixepoch') AND played_at <= datetime(?, 'unixepoch')
          AND server_source = ? AND {shape} AND {owner_clause(owner)}
    """, (low, high, SOURCE)).fetchall():
        try:
            found.setdefault(_key(title, artist), []).append((_epoch(played_at), int(history_id)))
        except ValueError:
            continue
    return found


def _upgrade_account_plays(database, owner: int, precise: List[Dict[str, Any]]
                           ) -> List[Dict[str, Any]]:
    """an extended play whose account-export copy is already stored takes
    that row over (the second, the track id, the album) instead of adding a
    second one. returns the plays still to insert."""
    if not precise:
        return precise
    conn = database._get_connection()
    try:
        near = _spotify_rows_near(conn, owner, precise, account=True)
        if not near:
            return precise
        used = set()
        rest = []
        for ev in precise:
            stamp = _epoch(ev["played_at"])
            match = None
            for when, history_id in near.get(_key(ev["title"], ev["artist"]), []):
                if history_id not in used and abs(when - stamp) <= COARSE_TOLERANCE_SECONDS:
                    match = history_id
                    break
            if match is None:
                rest.append(ev)
                continue
            used.add(match)
            conn.execute("""
                UPDATE OR IGNORE listening_history
                SET played_at = ?, track_id = ?, duration_ms = ?,
                    album = CASE WHEN COALESCE(album, '') = '' THEN ? ELSE album END,
                    lib2_track_id = COALESCE(?, lib2_track_id)
                WHERE id = ?
            """, (ev["played_at"], ev.get("track_id"), ev.get("duration_ms") or 0,
                  ev.get("album") or "", ev.get("lib2_track_id"), match))
            # its import link now carries the extended play's time, so the
            # extended export imported again finds it
            conn.execute("UPDATE OR IGNORE listening_import_events SET listened_at = ? "
                         "WHERE history_id = ? AND source = ?", (stamp, match, SOURCE))
        conn.commit()
        return rest
    finally:
        conn.close()


def _without_extended_copies(database, owner: int, coarse: List[Dict[str, Any]]
                             ) -> List[Dict[str, Any]]:
    """account-export plays the extended history already brought in stay out"""
    if not coarse:
        return coarse
    conn = database._get_connection()
    try:
        near = _spotify_rows_near(conn, owner, coarse, account=False)
    finally:
        conn.close()
    if not near:
        return coarse
    used = set()
    rest = []
    for ev in coarse:
        stamp = _epoch(ev["played_at"])
        match = None
        for when, history_id in near.get(_key(ev["title"], ev["artist"]), []):
            if history_id not in used and abs(when - stamp) <= COARSE_TOLERANCE_SECONDS:
                match = history_id
                break
        if match is None:
            rest.append(ev)
        else:
            used.add(match)
    return rest


def import_batch(database, rows: Iterable[Any], profile_id: Any,
                 include_in_stats: Optional[bool] = None) -> Dict[str, Any]:
    """store one batch of parsed plays. returns what happened to them:
    {received, kept, added, merged, dropped: {short, podcast, private, invalid}}"""
    owner = import_owner(profile_id)
    rows = list(rows or [])[:MAX_BATCH]
    dropped = {"short": 0, "podcast": 0, "private": 0, "invalid": 0}
    events: List[Dict[str, Any]] = []
    for row in rows:
        event, why = normalize_row(row)
        if event is None:
            dropped[why] += 1
        else:
            events.append(event)

    conn = database._get_connection()
    try:
        ensure_schema(conn)
        now = _now()
        conn.execute(
            f"INSERT INTO {TABLE} (profile_id, include_in_stats, started_at, updated_at) "
            f"VALUES (?, ?, ?, ?) ON CONFLICT(profile_id) DO NOTHING",
            (owner, 0 if include_in_stats is False else 1, now, now))
        if include_in_stats is not None:
            conn.execute(f"UPDATE {TABLE} SET include_in_stats = ? WHERE profile_id = ?",
                         (1 if include_in_stats else 0, owner))
        conn.execute(
            f"UPDATE {TABLE} SET received = received + ?, dropped = dropped + ?, updated_at = ? "
            f"WHERE profile_id = ?", (len(rows), sum(dropped.values()), now, owner))
        # the pile is this profile's own from its first imported play, so its
        # stats and mixes read it, and nobody else's do
        _mark_pile_owned(conn, owner)
        conn.commit()
    finally:
        conn.close()

    added = 0
    if events:
        resolve_library_tracks(database, events)
        precise: List[Dict[str, Any]] = []
        coarse: List[Dict[str, Any]] = []
        for ev in events:
            (coarse if ev.pop("_coarse") else precise).append(ev)
        # the account export and the extended one hold the same plays, the
        # first to the minute: whichever came first, one play stays one play
        precise = _upgrade_account_plays(database, owner, precise)
        if precise:
            added += insert_import_events(database, precise, SOURCE, profile_id=owner,
                                          tolerance_seconds=PRECISE_TOLERANCE_SECONDS)
        coarse = _without_extended_copies(database, owner, coarse)
        if coarse:
            added += insert_import_events(database, coarse, SOURCE, profile_id=owner,
                                          tolerance_seconds=COARSE_TOLERANCE_SECONDS)
    return {
        "received": len(rows),
        "kept": len(events),
        "added": added,
        # kept but already in the history: a repeat of an earlier import, or
        # the same play listenbrainz or last.fm brought in
        "merged": len(events) - added,
        "dropped": dropped,
    }


def summary(database, profile_id: Any) -> Dict[str, Any]:
    """what this profile's imported spotify history holds now"""
    owner = import_owner(profile_id)
    out: Dict[str, Any] = {"imported": False, "include_in_stats": True, "plays": 0,
                           "first_played": None, "last_played": None, "artists": 0,
                           "matched": 0, "linked": 0, "updated_at": None}
    conn = database._get_connection()
    try:
        try:
            row = conn.execute(
                f"SELECT include_in_stats, updated_at FROM {TABLE} WHERE profile_id = ?",
                (owner,)).fetchone()
        except Exception:  # noqa: BLE001 - no table: nothing imported yet
            return out
        if row is None:
            return out
        out.update(imported=True, include_in_stats=bool(row[0]), updated_at=row[1])
        scope = owner_clause(owner)
        plays, first, last, artists, matched = conn.execute(f"""
            SELECT COUNT(*), MIN(played_at), MAX(played_at),
                   COUNT(DISTINCT LOWER(artist)), COUNT(lib2_track_id)
            FROM listening_history WHERE server_source = ? AND {scope}
        """, (SOURCE,)).fetchone()
        try:
            linked = conn.execute("""
                SELECT COUNT(*) FROM listening_import_events a
                JOIN listening_history h ON h.id = a.history_id
                WHERE a.source = ? AND a.profile_id = ? AND h.server_source != ?
            """, (SOURCE, owner, SOURCE)).fetchone()[0]
        except Exception:  # noqa: BLE001 - no alias table yet
            linked = 0
        out.update(plays=plays or 0, first_played=first, last_played=last,
                   artists=artists or 0, matched=matched or 0, linked=linked or 0)
    finally:
        conn.close()
    return out


def set_include_in_stats(database, profile_id: Any, include: bool) -> bool:
    owner = import_owner(profile_id)
    conn = database._get_connection()
    try:
        ensure_schema(conn)
        cur = conn.execute(f"UPDATE {TABLE} SET include_in_stats = ?, updated_at = ? "
                           f"WHERE profile_id = ?", (1 if include else 0, _now(), owner))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def remove_import(database, profile_id: Any) -> int:
    """take the whole import back out. plays it added go; plays listenbrainz
    or last.fm brought in stay (only the link to the import goes). returns
    how many plays were removed."""
    owner = import_owner(profile_id)
    conn = database._get_connection()
    try:
        ensure_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM listening_import_events WHERE source = ? AND profile_id = ?",
                         (SOURCE, owner))
        except Exception as e:  # noqa: BLE001 - no alias table: nothing linked
            logger.debug("no import links to remove: %s", e)
        cur = conn.execute(
            f"DELETE FROM listening_history WHERE server_source = ? AND {owner_clause(owner)}",
            (SOURCE,))
        removed = cur.rowcount
        conn.execute(f"DELETE FROM {TABLE} WHERE profile_id = ?", (owner,))
        if owner != SHARED_OWNER:
            # the pile stays theirs while it still holds plays (the web player
            # wrote there since); an empty one goes back to how it was
            left = conn.execute(
                f"SELECT 1 FROM listening_history WHERE {owner_clause(owner)} LIMIT 1").fetchone()
            if left is None:
                conn.execute(f"UPDATE profiles SET {HISTORY_IMPORT_COLUMN} = 0 WHERE id = ?", (owner,))
                # and its stats caches, so a later own pile never opens on them
                from core.listening_scope import pile_cache_keys
                for key in pile_cache_keys(owner):
                    conn.execute("DELETE FROM metadata WHERE key = ?", (key,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    logger.info("removed the spotify history import of pile %s (%s plays)", owner, removed)
    return removed


__all__ = [
    "MAX_BATCH", "MIN_PLAY_MS", "SOURCE", "ensure_schema", "import_batch",
    "import_owner", "normalize_row", "remove_import", "resolve_library_tracks",
    "set_include_in_stats", "summary", "track_id_from_uri",
]
