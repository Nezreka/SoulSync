"""Every artist merge is written down, and can be undone (#2073).

The duplicate repair folds two artist rows into one: albums and credits move
to the survivor and the other row is deleted. It kept nothing of what it did.
When it folded Attack Attack! (US) into Attack! Attack! (Wales) there was no
list of merges to look through, and no way back short of restoring a backup.

Three things live here:

- the journal. ``snapshot_before`` is taken as a merge starts and ``record``
  files it: who went into whom, why, and exactly what moved.
- ``undo``. The folded artist comes back with its own id, name and ids, its
  albums and credits return to it, and the survivor gets back the ids it had
  before. The pair is then kept apart for good.
- ``split``. For a merge made before the journal existed there is nothing to
  undo, but the albums still say who they were (their folders and album-artist
  tags). ``split`` moves the named albums to an artist of that name.

``keep_apart`` is the standing word that two rows are two artists. The merge
passes ask it before folding, so an undone or refused merge stays that way.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.artist_merges")

DDL = (
    """CREATE TABLE IF NOT EXISTS lib2_artist_merges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        merged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        survivor_id INTEGER NOT NULL,
        survivor_name TEXT,
        duplicate_id INTEGER NOT NULL,
        duplicate_name TEXT,
        reason TEXT,
        albums_moved INTEGER NOT NULL DEFAULT 0,
        snapshot TEXT NOT NULL DEFAULT '{}',
        undone_at TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_lib2_artist_merges_survivor ON lib2_artist_merges(survivor_id)",
    # pairs the repair would not merge by itself: one shared catalogue id and
    # nothing else. a person decides those (the Artist Merge Review job)
    """CREATE TABLE IF NOT EXISTS lib2_artist_merge_doubts (
        low_id INTEGER NOT NULL,
        high_id INTEGER NOT NULL,
        source TEXT,
        value TEXT,
        seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (low_id, high_id)
    )""",
    """CREATE TABLE IF NOT EXISTS lib2_artist_keep_apart (
        low_id INTEGER NOT NULL,
        high_id INTEGER NOT NULL,
        why TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (low_id, high_id)
    )""",
)

_SURVIVOR_FIELDS = ("external_ids", "spotify_id", "musicbrainz_id", "image_url", "canonical_artist_id")


def ensure_tables(conn: Any) -> None:
    for statement in DDL:
        conn.execute(statement)


def _dicts(cursor: Any) -> List[Dict[str, Any]]:
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


# ── kept apart ───────────────────────────────────────────────────────────────

def keep_apart(conn: Any, first: int, second: int, why: str = "") -> None:
    ensure_tables(conn)
    low, high = sorted((int(first), int(second)))
    if low != high:
        conn.execute("INSERT OR IGNORE INTO lib2_artist_keep_apart(low_id, high_id, why) VALUES(?,?,?)",
                     (low, high, why))


def kept_apart(conn: Any, first: Any, second: Any) -> bool:
    """two rows a person (or an undo) said are two artists"""
    try:
        low, high = sorted((int(first), int(second)))
        return conn.execute("SELECT 1 FROM lib2_artist_keep_apart WHERE low_id=? AND high_id=?",
                            (low, high)).fetchone() is not None
    except Exception:  # noqa: BLE001 - no table yet reads as nothing kept apart
        return False


def note_doubt(conn: Any, first: int, second: int, source: str, value: str) -> None:
    """the repair left this pair apart for want of evidence: remembered for review"""
    try:
        ensure_tables(conn)
        low, high = sorted((int(first), int(second)))
        conn.execute(
            "INSERT INTO lib2_artist_merge_doubts(low_id, high_id, source, value) VALUES(?,?,?,?) "
            "ON CONFLICT(low_id, high_id) DO UPDATE SET source=excluded.source, value=excluded.value,"
            " seen_at=CURRENT_TIMESTAMP", (low, high, source, value))
    except Exception as exc:  # noqa: BLE001
        logger.debug("artist merge doubt not noted: %s", exc)


def doubts(conn: Any) -> List[Dict[str, Any]]:
    """Doubtful pairs that are still two rows and not settled either way."""
    try:
        ensure_tables(conn)
        rows = _dicts(conn.execute(
            """SELECT d.low_id, d.high_id, d.source, d.value, a.name AS low_name, b.name AS high_name
                 FROM lib2_artist_merge_doubts d
                 JOIN lib2_artists a ON a.id = d.low_id
                 JOIN lib2_artists b ON b.id = d.high_id
                ORDER BY d.seen_at DESC"""))
    except Exception as exc:  # noqa: BLE001
        logger.debug("artist merge doubts unavailable: %s", exc)
        return []
    return [row for row in rows if not kept_apart(conn, row["low_id"], row["high_id"])]


# ── the journal ──────────────────────────────────────────────────────────────

def snapshot_before(conn: Any, survivor_id: int, duplicate_id: int) -> Dict[str, Any]:
    """What a merge is about to change, read before it changes it."""
    duplicate = _dicts(conn.execute("SELECT * FROM lib2_artists WHERE id=?", (duplicate_id,)))
    survivor = _dicts(conn.execute("SELECT * FROM lib2_artists WHERE id=?", (survivor_id,)))
    credit = lambda table, column, who: [r[0] for r in conn.execute(  # noqa: E731
        f"SELECT {column} FROM {table} WHERE artist_id=?", (who,))]
    snap = {
        "duplicate": duplicate[0] if duplicate else {},
        "survivor_before": {k: survivor[0].get(k) for k in _SURVIVOR_FIELDS} if survivor else {},
        "albums": [r[0] for r in conn.execute(
            "SELECT id FROM lib2_albums WHERE primary_artist_id=?", (duplicate_id,))],
        "album_credits": _dicts(conn.execute(
            "SELECT * FROM lib2_album_artists WHERE artist_id=?", (duplicate_id,))),
        "track_credits": _dicts(conn.execute(
            "SELECT * FROM lib2_track_artists WHERE artist_id=?", (duplicate_id,))),
        # the albums and tracks the survivor was on already: an undo leaves those with it
        "survivor_albums": credit("lib2_album_artists", "album_id", survivor_id),
        "survivor_tracks": credit("lib2_track_artists", "track_id", survivor_id),
        "aliases": [r[0] for r in conn.execute(
            "SELECT id FROM lib2_artists WHERE canonical_artist_id=?", (duplicate_id,))],
    }
    try:
        snap["mappings"] = _dicts(conn.execute(
            "SELECT * FROM lib2_media_server_mappings WHERE entity_type='artist' AND entity_id=?",
            (duplicate_id,)))
    except Exception:  # noqa: BLE001 - an older schema with no mappings table
        snap["mappings"] = []
    return snap


def record(conn: Any, survivor: Any, duplicate: Any, snapshot: Dict[str, Any], reason: str) -> Optional[int]:
    """File a merge that has just been made. Never the reason a merge fails."""
    try:
        ensure_tables(conn)
        cur = conn.execute(
            "INSERT INTO lib2_artist_merges(survivor_id, survivor_name, duplicate_id, duplicate_name,"
            " reason, albums_moved, snapshot) VALUES(?,?,?,?,?,?,?)",
            (int(survivor["id"]), survivor["name"], int(duplicate["id"]), duplicate["name"], reason,
             len(snapshot.get("albums") or []), json.dumps(snapshot, default=str)))
        return int(cur.lastrowid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("artist merge %s -> %s was not journaled: %s",
                       duplicate["id"], survivor["id"], exc)
        return None


def list_merges(conn: Any, *, include_undone: bool = False, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        ensure_tables(conn)
        where = "" if include_undone else "WHERE undone_at IS NULL"
        return _dicts(conn.execute(
            f"SELECT id, merged_at, survivor_id, survivor_name, duplicate_id, duplicate_name, reason,"
            f" albums_moved, undone_at FROM lib2_artist_merges {where} ORDER BY id DESC LIMIT ?",
            (int(limit),)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("artist merge journal unavailable: %s", exc)
        return []


class MergeUndoError(ValueError):
    """the merge cannot be undone as recorded"""


def _columns(conn: Any, table: str) -> List[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def undo(conn: Any, merge_id: int) -> Dict[str, Any]:
    """Put a recorded merge back: the folded artist returns with what it had."""
    ensure_tables(conn)
    row = conn.execute("SELECT * FROM lib2_artist_merges WHERE id=?", (int(merge_id),)).fetchone()
    if row is None:
        raise MergeUndoError("No such merge")
    entry = dict(zip([d[0] for d in conn.execute(
        "SELECT * FROM lib2_artist_merges LIMIT 0").description], row, strict=True))
    if entry["undone_at"]:
        raise MergeUndoError("This merge was already undone")
    snap = json.loads(entry["snapshot"] or "{}")
    duplicate = snap.get("duplicate") or {}
    survivor_id, duplicate_id = int(entry["survivor_id"]), int(entry["duplicate_id"])
    if not duplicate:
        raise MergeUndoError("Nothing was recorded about the merged artist")
    if conn.execute("SELECT 1 FROM lib2_artists WHERE id=?", (survivor_id,)).fetchone() is None:
        raise MergeUndoError("The artist it was merged into no longer exists")
    if conn.execute("SELECT 1 FROM lib2_artists WHERE id=?", (duplicate_id,)).fetchone() is not None:
        raise MergeUndoError("An artist with that id exists again")

    # the artist comes back as it was, minus any column the schema has since dropped
    live = set(_columns(conn, "lib2_artists"))
    fields = [k for k in duplicate if k in live]
    conn.execute(f"INSERT INTO lib2_artists({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                 [duplicate[k] for k in fields])

    albums = [int(a) for a in snap.get("albums") or []]
    moved = 0
    for album_id in albums:
        moved += conn.execute(
            "UPDATE lib2_albums SET primary_artist_id=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND primary_artist_id=?", (duplicate_id, album_id, survivor_id)).rowcount

    for table, key, kept in (("lib2_album_artists", "album_id", set(snap.get("survivor_albums") or [])),
                             ("lib2_track_artists", "track_id", set(snap.get("survivor_tracks") or []))):
        owner = "lib2_albums" if key == "album_id" else "lib2_tracks"
        cols = set(_columns(conn, table))
        for credit in snap.get("album_credits" if key == "album_id" else "track_credits") or []:
            target = credit.get(key)
            if conn.execute(f"SELECT 1 FROM {owner} WHERE id=?", (target,)).fetchone() is None:
                continue    # the album or track is gone since
            if target not in kept:
                # the survivor only holds this credit because of the merge
                conn.execute(f"DELETE FROM {table} WHERE {key}=? AND artist_id=?", (target, survivor_id))
            names = [k for k in credit if k in cols]
            conn.execute(
                f"INSERT OR IGNORE INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})",
                [credit[k] for k in names])

    before = snap.get("survivor_before") or {}
    if before:
        conn.execute(
            "UPDATE lib2_artists SET external_ids=?, spotify_id=?, musicbrainz_id=?, image_url=?,"
            " updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (before.get("external_ids") or "{}", before.get("spotify_id"), before.get("musicbrainz_id"),
             before.get("image_url"), survivor_id))
    for alias_id in snap.get("aliases") or []:
        conn.execute("UPDATE lib2_artists SET canonical_artist_id=? WHERE id=? AND id<>?",
                     (duplicate_id, alias_id, duplicate_id))
    for mapping in snap.get("mappings") or []:
        try:
            conn.execute(
                "DELETE FROM lib2_media_server_mappings WHERE entity_type='artist' AND entity_id=? "
                "AND server_source=? AND server_id=?",
                (survivor_id, mapping.get("server_source"), mapping.get("server_id")))
            cols = [k for k in mapping if k != "id" and k in set(_columns(conn, "lib2_media_server_mappings"))]
            conn.execute(
                f"INSERT OR IGNORE INTO lib2_media_server_mappings({','.join(cols)}) "
                f"VALUES({','.join('?' for _ in cols)})", [mapping[k] for k in cols])
        except Exception as exc:  # noqa: BLE001 - the next scan maps it again
            logger.debug("artist merge undo: mapping not restored: %s", exc)

    conn.execute("UPDATE lib2_artist_merges SET undone_at=CURRENT_TIMESTAMP WHERE id=?", (int(merge_id),))
    keep_apart(conn, survivor_id, duplicate_id, "merge undone")
    logger.info("Undid artist merge %s: %r is its own artist again (%d albums back)",
                merge_id, duplicate.get("name"), moved)
    return {"artist_id": duplicate_id, "name": duplicate.get("name"), "albums": moved}


# ── a merge made before the journal ──────────────────────────────────────────

def split(conn: Any, artist_id: int, name: str, album_ids: Iterable[int]) -> Dict[str, Any]:
    """Move these albums of one artist to an artist of another name.

    For a wrong merge nothing recorded: the albums themselves still say whose
    they are. The artist of that name is found or made, the albums and the
    credits on their tracks go to it, and the two are kept apart."""
    from core.library2.importer import normalize_name
    name = str(name or "").strip()
    wanted = [int(a) for a in album_ids]
    if not name or not wanted:
        raise MergeUndoError("Nothing to split")
    marks = ",".join("?" for _ in wanted)
    mine = [r[0] for r in conn.execute(
        f"SELECT id FROM lib2_albums WHERE primary_artist_id=? AND id IN ({marks})",
        [int(artist_id), *wanted])]
    if not mine:
        raise MergeUndoError("Those albums are no longer under this artist")
    found = conn.execute(
        "SELECT id FROM lib2_artists WHERE name=? AND id<>? ORDER BY id LIMIT 1",
        (name, int(artist_id))).fetchone()
    if found is not None:
        target = int(found[0])
    else:
        columns = set(_columns(conn, "lib2_artists"))
        fields, values = ["name"], [name]
        if "sort_name" in columns:
            fields.append("sort_name"); values.append(name)
        if "name_key" in columns:
            fields.append("name_key"); values.append(normalize_name(name))
        target = int(conn.execute(
            f"INSERT INTO lib2_artists({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
            values).lastrowid)
    marks = ",".join("?" for _ in mine)
    conn.execute(
        f"UPDATE lib2_albums SET primary_artist_id=?, updated_at=CURRENT_TIMESTAMP WHERE id IN ({marks})",
        [target, *mine])
    conn.execute(f"UPDATE OR IGNORE lib2_album_artists SET artist_id=? WHERE artist_id=? AND album_id IN ({marks})",
                 [target, int(artist_id), *mine])
    conn.execute(f"DELETE FROM lib2_album_artists WHERE artist_id=? AND album_id IN ({marks})",
                 [int(artist_id), *mine])
    tracks = f"SELECT id FROM lib2_tracks WHERE album_id IN ({marks})"
    conn.execute(f"UPDATE OR IGNORE lib2_track_artists SET artist_id=? WHERE artist_id=? AND track_id IN ({tracks})",
                 [target, int(artist_id), *mine])
    conn.execute(f"DELETE FROM lib2_track_artists WHERE artist_id=? AND track_id IN ({tracks})",
                 [int(artist_id), *mine])
    keep_apart(conn, int(artist_id), target, "split by hand")
    logger.info("Split %d album(s) of artist %s to %r (%s)", len(mine), artist_id, name, target)
    return {"artist_id": target, "name": name, "albums": len(mine), "created": found is None}




# ── merges from before the journal: what the albums still say ────────────────

import re as _re
import unicodedata as _unicodedata

_DISC_FOLDER = _re.compile(r"^(cd|disc|disk)\s*\d+$", _re.IGNORECASE)
#: a folder name that turns up under this many artists is a shelf ("Music"), not an artist
GENERIC_FOLDER_ARTISTS = 3


def _name_key(text: Any) -> str:
    folded = _unicodedata.normalize("NFKC", str(text or "")).casefold()
    return " ".join(part for part in _re.split(r"\W+", folded) if part)


def _exact_key(text: Any) -> str:
    """a name as written, case and spacing aside: punctuation counts, so
    Attack Attack! is not Attack! Attack!"""
    return " ".join(_unicodedata.normalize("NFKC", str(text or "")).casefold().split())


def _spelling_key(text: Any) -> str:
    """a name with the differences that are only spelling taken out: accents
    and a leading "The". Jason Derülo is Jason Derulo and Jackson 5 is The
    Jackson 5. Punctuation stays: that is what tells two bands apart."""
    plain = "".join(ch for ch in _unicodedata.normalize("NFKD", _exact_key(text))
                    if not _unicodedata.combining(ch))
    return plain[4:] if plain.startswith("the ") else plain


def _same_act(artist: Any, other: Any) -> bool:
    """is ``other`` this artist under another spelling, or this artist with
    company ("Gorillaz feat. D.R.A.M.", "Le Castle Vania, Addison"), or a
    short form of it ("Oliver" for Oliver Tree)? none of those is a second
    artist whose albums were folded in."""
    if _spelling_key(artist) == _spelling_key(other):
        return True
    a, b = _name_key(artist).split(), _name_key(other).split()
    if not a or not b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) < len(long_) and long_[:len(short)] == short


def _artist_folder(path: str) -> str:
    """The folder two levels above a file (artist/album/file), past a disc folder."""
    parts = [p for p in _re.split(r"[\\/]+", str(path or "")) if p]
    if len(parts) < 3:
        return ""
    if _DISC_FOLDER.match(parts[-2]) and len(parts) >= 4:
        return parts[-4]
    return parts[-3]


def merged_by_mistake(conn: Any, read_album_artist: Any) -> List[Dict[str, Any]]:
    """Artists that look like two artists folded into one.

    A merge deletes one artist row and keeps nothing of it, but the music does
    not move: the folded artist's albums still sit in a folder of its own name
    and their files still carry its name as album artist. So an artist with
    albums in its own-named folder AND albums in a folder of another name,
    where the files in that folder name that other artist too, is two artists.

    Both signs are required. A folder alone is how people file ("Music",
    "Various Artists", a label); a tag alone is one mistagged album.
    ``read_album_artist(path)`` returns a file's album-artist tag or None.
    """
    from collections import Counter, defaultdict
    names = {row[0]: row[1] for row in conn.execute("SELECT id, name FROM lib2_artists")}
    per_album: Dict[tuple, Counter] = defaultdict(Counter)
    sample: Dict[tuple, str] = {}
    for artist_id, album_id, path in conn.execute(
            """SELECT al.primary_artist_id, al.id, f.path FROM lib2_track_files f
                 JOIN lib2_tracks t ON t.id = f.track_id
                 JOIN lib2_albums al ON al.id = t.album_id
                WHERE f.path IS NOT NULL AND TRIM(f.path) <> ''
                  AND COALESCE(f.file_state, 'active') = 'active'"""):
        folder = _artist_folder(path)
        if artist_id is None or not folder:
            continue
        per_album[(artist_id, album_id)][folder] += 1
        sample.setdefault((artist_id, album_id, folder), path)

    by_artist: Dict[int, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
    for (artist_id, album_id), folders in per_album.items():
        by_artist[artist_id][folders.most_common(1)[0][0]].append(album_id)

    # how many artists each other-named folder turns up under
    spread: Counter = Counter()
    for artist_id, folders in by_artist.items():
        own = _name_key(names.get(artist_id))
        for folder in folders:
            if _name_key(folder) != own:
                spread[_name_key(folder)] += 1

    found = []
    for artist_id, folders in by_artist.items():
        name = names.get(artist_id)
        own, exact = _name_key(name), _exact_key(name)
        if not own:
            continue
        for folder, album_ids in folders.items():
            key = _name_key(folder)
            # a folder spelled exactly as the artist is the artist's own. so is
            # one that only differs by what a file system strips ("AC_DC"): the
            # tags settle that below, by naming the artist itself
            if not key or _exact_key(folder) == exact:
                continue
            if key != own and spread[key] >= GENERIC_FOLDER_ARTISTS:
                continue
            # the artist has to have a home of its own besides this folder:
            # with only one folder there is nothing to say it is two artists
            if not any(other != folder and _name_key(other) == own for other in folders):
                continue
            confirmed, said = [], Counter()
            for album_id in album_ids:
                tag = read_album_artist(sample[(artist_id, album_id, folder)])
                # the files name someone who is not this artist, and the folder is theirs
                if (tag and _exact_key(tag) != exact and _name_key(tag) == key
                        and not _same_act(name, tag)):
                    confirmed.append(album_id)
                    said[str(tag).strip()] += 1
            if not confirmed:
                continue
            titles = [row[0] for row in conn.execute(
                f"SELECT title FROM lib2_albums WHERE id IN ({','.join('?' for _ in confirmed)}) "
                "ORDER BY title", confirmed)]
            found.append({"artist_id": artist_id, "artist_name": name,
                          "other_name": said.most_common(1)[0][0],
                          "album_ids": sorted(confirmed), "album_titles": titles})
    return sorted(found, key=lambda f: (-len(f["album_ids"]), str(f["artist_name"])))


__all__ = ["DDL", "MergeUndoError", "doubts", "ensure_tables", "keep_apart", "kept_apart",
           "list_merges", "merged_by_mistake", "note_doubt", "record", "snapshot_before",
           "split", "undo"]
