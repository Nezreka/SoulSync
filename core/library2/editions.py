"""Release Edition + Recording model for Library v2 (audit P1-04 / ADR-04).

``lib2_albums`` used to be BOTH the abstract album idea and one concrete
provider release; deluxe/remaster/country pressings collapsed into whichever
row got imported first, and duplicate detection had nothing but normalized
titles to work with (P1-04/P1-05).

ADR-04 models both levels, Lidarr-style (Album/AlbumRelease):

- **Release group** — the existing ``lib2_albums`` row keeps this role:
  artist credits, title, type, group-level date, monitoring intent.
- **Release edition** (``lib2_release_editions``) — one concrete pressing:
  provider release IDs, country/label/barcode/status, media, disc count,
  release date, track count and a matching signature. Exactly one edition
  per group is ``is_default`` (enforced by a partial unique index).
- **Recording** (``lib2_recordings``) — the edition-independent audio
  identity (ISRC / MusicBrainz recording / Spotify track). This is what
  eventually replaces the unsafe ``canonical_track_id`` cluster.
- **Release track** (``lib2_release_tracks``) — a concrete position on an
  edition, pointing at exactly one recording and (compat) at the
  ``lib2_tracks`` row today's read/acquisition paths still use.

Identity rules (the audit's hard requirement): recordings are merged ONLY on
shared hard identifiers — same ISRC, same MusicBrainz recording ID, or same
Spotify track ID. Titles never merge anything, so Live/Remaster/Radio Edit
variants keep separate recordings. Existing ``canonical_track_id`` links
whose pair shares no hard ID are recorded in ``lib2_recording_review`` as
findings for the user instead of being silently merged (§14.2 Schritt 3).

The migration is additive: ``backfill_editions`` creates one default edition
per album and one recording + release track per track, is idempotent, and
runs from both the schema-ensure step and the importer.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.editions")

LIB2_RELEASE_EDITIONS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_release_editions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_group_id INTEGER NOT NULL,                -- lib2_albums row (the group)
    is_default INTEGER NOT NULL DEFAULT 0,            -- exactly one per group
    title TEXT,                                       -- NULL = inherit group title
    disambiguation TEXT,                              -- 'Deluxe', '2011 Remaster', ...
    spotify_id TEXT,
    musicbrainz_id TEXT,                              -- MB *release* id
    external_ids TEXT NOT NULL DEFAULT '{}',
    country TEXT,
    label TEXT,
    barcode TEXT,
    status TEXT,                                      -- 'official'|'promotion'|'bootleg'|...
    media TEXT NOT NULL DEFAULT '[]',                 -- JSON: [{'format':'CD','disc':1},...]
    disc_count INTEGER,
    release_date TEXT,
    track_count INTEGER,
    duration INTEGER,                                 -- milliseconds
    signature TEXT,                                   -- matching signature (see edition_signature)
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (release_group_id) REFERENCES lib2_albums(id) ON DELETE CASCADE
)
"""

LIB2_RECORDINGS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,                              -- canonical recording title
    duration INTEGER,                                 -- milliseconds
    isrc TEXT,
    musicbrainz_id TEXT,                              -- MB *recording* id
    spotify_id TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

LIB2_RELEASE_TRACKS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_release_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_edition_id INTEGER NOT NULL,
    recording_id INTEGER NOT NULL,
    track_id INTEGER,                                 -- compat link to lib2_tracks
    disc_number INTEGER DEFAULT 1,
    track_number INTEGER,
    title_override TEXT,                              -- NULL = recording title
    duration INTEGER,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (release_edition_id) REFERENCES lib2_release_editions(id) ON DELETE CASCADE,
    FOREIGN KEY (recording_id) REFERENCES lib2_recordings(id) ON DELETE RESTRICT,
    FOREIGN KEY (track_id) REFERENCES lib2_tracks(id) ON DELETE SET NULL
)
"""

# Canonical links that could NOT be verified through a shared hard ID are a
# review finding for the user, never an automatic merge (audit §14.2).
LIB2_RECORDING_REVIEW_DDL = """
CREATE TABLE IF NOT EXISTS lib2_recording_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL,
    other_track_id INTEGER NOT NULL,
    reason TEXT NOT NULL,                             -- 'canonical_link_unverified'
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(track_id, other_track_id, reason)
)
"""

_INDEXES = (
    # One default edition per release group — a schema invariant, not a habit.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_lib2_editions_default "
    "ON lib2_release_editions(release_group_id) WHERE is_default=1",
    "CREATE INDEX IF NOT EXISTS idx_lib2_editions_group "
    "ON lib2_release_editions(release_group_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_editions_spotify "
    "ON lib2_release_editions(spotify_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_editions_mbid "
    "ON lib2_release_editions(musicbrainz_id)",
    # Hard identifiers are unique per recording; empty values stay free.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_lib2_recordings_isrc "
    "ON lib2_recordings(isrc) WHERE isrc IS NOT NULL AND isrc <> ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_lib2_recordings_mbid "
    "ON lib2_recordings(musicbrainz_id) "
    "WHERE musicbrainz_id IS NOT NULL AND musicbrainz_id <> ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_lib2_recordings_spotify "
    "ON lib2_recordings(spotify_id) WHERE spotify_id IS NOT NULL AND spotify_id <> ''",
    "CREATE INDEX IF NOT EXISTS idx_lib2_release_tracks_edition "
    "ON lib2_release_tracks(release_edition_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_release_tracks_recording "
    "ON lib2_release_tracks(recording_id)",
    # A lib2 track appears at most once per edition.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_lib2_release_tracks_track "
    "ON lib2_release_tracks(release_edition_id, track_id) WHERE track_id IS NOT NULL",
    # iss32-M05: the unique index above leads with release_edition_id, so it
    # cannot answer "is this track materialized anywhere?" — the predicate the
    # backfill runs once per track and the prune runs over the whole table.
    # Without this index that is a scan of lib2_release_tracks per track; on
    # 307k tracks it is the difference between minutes and hours.
    "CREATE INDEX IF NOT EXISTS idx_lib2_release_tracks_track_id "
    "ON lib2_release_tracks(track_id)",
)

# iss32-M05: how many source rows one commit covers. Small enough that the
# write lock is never held for long, large enough that the per-commit fsync
# does not dominate. 500 tracks is roughly a quarter second of work.
BACKFILL_BATCH_SIZE = 500


def ensure_editions_schema(cursor: Any) -> None:
    """Create the edition/recording tables + indexes. Idempotent."""
    for ddl in (LIB2_RELEASE_EDITIONS_DDL, LIB2_RECORDINGS_DDL,
                LIB2_RELEASE_TRACKS_DDL, LIB2_RECORDING_REVIEW_DDL):
        cursor.execute(ddl)
    for index_sql in _INDEXES:
        cursor.execute(index_sql)


def _norm(value: Optional[Any]) -> str:
    """Conservative, reproducible normalization (same rules as stable_ids)."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return " ".join(text.split()).casefold()


def edition_signature(spotify_id: Optional[str], musicbrainz_id: Optional[str],
                      title: Optional[str], track_count: Optional[int]) -> str:
    """Matching signature for an edition.

    A provider release ID *is* the edition identity when present; otherwise
    fall back to normalized title + track count — deliberately coarse, it
    only has to keep obviously different pressings apart until the typed
    provider adapters (Phase 3) deliver real edition facts.
    """
    if spotify_id:
        parts = ("spotify", str(spotify_id))
    elif musicbrainz_id:
        parts = ("musicbrainz", str(musicbrainz_id))
    else:
        parts = ("fallback", _norm(title), str(track_count or 0))
    payload = "\x1f".join(("edition", *parts)).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def record_alternative_edition(cursor: Any, release_group_id: int, *,
                               source: str, provider_id: str,
                               title: Optional[str] = None,
                               release_date: Optional[str] = None,
                               track_count: Optional[int] = None) -> Optional[int]:
    """Persist an ALTERNATIVE provider release of an already-known group.

    §62.6 Stufe 2: when a sync/title/day-count match lands on a group that
    already carries a different id of the same source (JP vs. international
    pressing, provider-side duplicate listing), the losing release id used to
    be logged away (G1). Recording it as a non-default edition keeps the
    fact "this provider ALSO knows the group as <id>" queryable. Idempotent
    per (source, provider_id) within the group; returns the edition id, or
    None when the id is already represented (including as the group's own)."""
    import json

    src = str(source or "").strip().lower()
    pid = str(provider_id or "").strip()
    if not src or not pid:
        return None
    group_id = int(release_group_id)
    for row in cursor.execute(
            """SELECT spotify_id, musicbrainz_id, external_ids
                 FROM lib2_release_editions WHERE release_group_id=?""",
            (group_id,)).fetchall():
        if src == "spotify" and str(row["spotify_id"] or "") == pid:
            return None
        if src == "musicbrainz" and str(row["musicbrainz_id"] or "") == pid:
            return None
        try:
            known = json.loads(row["external_ids"] or "{}")
        except (TypeError, ValueError):
            known = {}
        if isinstance(known, dict) and str(known.get(src) or "") == pid:
            return None

    spotify_id = pid if src == "spotify" else None
    musicbrainz_id = pid if src == "musicbrainz" else None
    signature = edition_signature(spotify_id, musicbrainz_id, title, track_count)
    cursor.execute(
        """INSERT INTO lib2_release_editions(
               release_group_id, is_default, title, spotify_id, musicbrainz_id,
               external_ids, release_date, track_count, status, signature)
           VALUES(?,0,?,?,?,?,?,?, 'official', ?)""",
        (group_id, title, spotify_id, musicbrainz_id,
         json.dumps({src: pid}, sort_keys=True, separators=(",", ":")),
         release_date, track_count, signature))
    return int(cursor.lastrowid)


def default_edition_id(cursor: Any, album_id: int) -> Optional[int]:
    row = cursor.execute(
        "SELECT id FROM lib2_release_editions "
        "WHERE release_group_id=? AND is_default=1", (int(album_id),)).fetchone()
    return int(row[0]) if row else None


# What the default edition is derived from: the album row's provider ids and
# its canonical pin.
_EDITION_ALBUM_COLUMNS = (
    "id, title, spotify_id, musicbrainz_id, external_ids, release_date, "
    "track_count, expected_track_count, canonical_source, canonical_album_id, "
    "canonical_track_count"
)


def _row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _album_own_ids(album_row: Any) -> Dict[str, str]:
    from core.library2.provider_ids import parse_external_ids

    ids = parse_external_ids(_row_value(album_row, "external_ids"))
    for column, source in (("spotify_id", "spotify"), ("musicbrainz_id", "musicbrainz")):
        value = str(_row_value(album_row, column) or "").strip()
        if value:
            ids[source] = value
    return ids


def _canonical_pin(album_row: Any) -> Optional[tuple]:
    source = str(_row_value(album_row, "canonical_source") or "").strip().lower()
    pinned = str(_row_value(album_row, "canonical_album_id") or "").strip()
    return (source, pinned) if source and pinned else None


def album_release_ids(album_row: Any) -> Dict[str, str]:
    """The provider release ids the album stands for, keyed by source.

    The canonical pin wins for its source (#758/#765): the release a user
    picked, or the resolver fitted to the files, is the release the match
    chip, the tracklist, completeness and track-number repair all mean. Before
    this they disagreed -- the chip showed the manual REGULAR while the
    default edition still fetched the DELUXE tracklist (feature-parity A01).
    """
    ids = _album_own_ids(album_row)
    pin = _canonical_pin(album_row)
    if pin:
        ids[pin[0]] = pin[1]
    return ids


def _edition_ids_json(ids: Dict[str, str]) -> str:
    import json

    rest = {k: v for k, v in ids.items() if k not in ("spotify", "musicbrainz") and v}
    return json.dumps(rest, sort_keys=True, separators=(",", ":"))


def _pinned_track_count(album_row: Any) -> Optional[int]:
    """The track count known for the pinned release, or None when unknown."""
    return _row_value(album_row, "canonical_track_count") if _canonical_pin(album_row) else None


def _edition_track_count(album_row: Any) -> Optional[int]:
    """The album's own counts describe its own release; a pin naming another
    release only has the count fetched for it (None until then)."""
    pin = _canonical_pin(album_row)
    if pin and _album_own_ids(album_row).get(pin[0]) != pin[1]:
        return _pinned_track_count(album_row)
    return (_pinned_track_count(album_row)
            or _row_value(album_row, "expected_track_count")
            or _row_value(album_row, "track_count"))


def _ensure_default_edition(cursor: Any, album_row: Any) -> int:
    """The album's default edition id, creating it from the group's provider
    facts when missing (the additive §14.2-Schritt-3 backfill)."""
    existing = default_edition_id(cursor, album_row["id"])
    if existing is not None:
        return existing
    ids = album_release_ids(album_row)
    track_count = _edition_track_count(album_row)
    signature = edition_signature(ids.get("spotify"), ids.get("musicbrainz"),
                                  album_row["title"], track_count)
    cursor.execute(
        """INSERT INTO lib2_release_editions(
               release_group_id, is_default, spotify_id, musicbrainz_id,
               external_ids, release_date, track_count, status, signature)
           VALUES(?,?,?,?,?,?,?, 'official', ?)""",
        (album_row["id"], 1, ids.get("spotify"), ids.get("musicbrainz"),
         _edition_ids_json(ids),
         _row_value(album_row, "release_date"),
         track_count,
         signature))
    return int(cursor.lastrowid)


def sync_default_edition(cursor: Any, album_id: int) -> bool:
    """Make the default edition name the release the album row stands for.

    Called wherever an album's provider id or canonical pin changes -- a manual
    match, an enrichment match, a canonical pin -- so every edition consumer
    reads the release the user sees. When a release id is replaced (not merely
    added), the stored release facts described the old release and are
    dropped; the next tracklist fetch supplies the new ones. Returns whether
    anything changed. Does not commit.
    """
    import json

    if hasattr(cursor, "cursor") and not hasattr(cursor, "lastrowid"):
        cursor = cursor.cursor()
    album = cursor.execute(
        f"SELECT {_EDITION_ALBUM_COLUMNS} FROM lib2_albums WHERE id=?",
        (int(album_id),)).fetchone()
    if album is None:
        return False
    edition = cursor.execute(
        "SELECT id, spotify_id, musicbrainz_id, external_ids, track_count "
        "FROM lib2_release_editions WHERE release_group_id=? AND is_default=1",
        (int(album_id),)).fetchone()
    if edition is None:
        _ensure_default_edition(cursor, album)
        return True
    from core.library2.provider_ids import parse_external_ids

    current = parse_external_ids(edition["external_ids"])
    for column, source in (("spotify_id", "spotify"), ("musicbrainz_id", "musicbrainz")):
        value = str(edition[column] or "").strip()
        if value:
            current[source] = value
    wanted = album_release_ids(album)
    if wanted == current:
        return False
    replaced = any(current[k] != wanted.get(k) for k in current)
    track_count = edition["track_count"]
    sets = ["spotify_id=?", "musicbrainz_id=?", "external_ids=?",
            "updated_at=CURRENT_TIMESTAMP"]
    params: list = [wanted.get("spotify"), wanted.get("musicbrainz"), _edition_ids_json(wanted)]
    if replaced:
        track_count = _edition_track_count(album)
        sets += ["release_date=NULL", "track_count=?", "disc_count=NULL", "barcode=NULL"]
        params.append(track_count)
    sets.append("signature=?")
    params.append(edition_signature(wanted.get("spotify"), wanted.get("musicbrainz"),
                                    album["title"], track_count))
    cursor.execute(
        f"UPDATE lib2_release_editions SET {', '.join(sets)} WHERE id=?",
        (*params, int(edition["id"])))
    # An alternative edition recorded for one of these ids is the default now;
    # keep it only while release tracks hang off it.
    for row in cursor.execute(
            "SELECT id, spotify_id, musicbrainz_id, external_ids FROM lib2_release_editions"
            " WHERE release_group_id=? AND is_default=0", (int(album_id),)).fetchall():
        alt = parse_external_ids(row["external_ids"])
        if row["spotify_id"]:
            alt["spotify"] = str(row["spotify_id"])
        if row["musicbrainz_id"]:
            alt["musicbrainz"] = str(row["musicbrainz_id"])
        if alt and all(wanted.get(k) == v for k, v in alt.items()):
            cursor.execute(
                "DELETE FROM lib2_release_editions WHERE id=? AND NOT EXISTS ("
                " SELECT 1 FROM lib2_release_tracks WHERE release_edition_id=?)",
                (int(row["id"]), int(row["id"])))
    logger.debug("default edition of album %s now %s", album_id, json.dumps(wanted))
    return True


def pin_album_release(cursor: Any, album_id: int, source: str,
                      provider_id: Optional[str]) -> None:
    """A manual album match pins (and locks) that release as canonical (#758).

    ``provider_id=None`` lifts a pin naming ``source`` -- the user cleared the
    match it came from. The default edition follows either way. Does not
    commit.
    """
    source = str(source or "").strip().lower()
    if provider_id:
        cursor.execute(
            "UPDATE lib2_albums SET canonical_source=?, canonical_album_id=?,"
            " canonical_score=1.0, canonical_locked=1,"
            " canonical_resolved_at=CURRENT_TIMESTAMP, canonical_track_count=NULL,"
            " updated_at=CURRENT_TIMESTAMP"
            " WHERE id=? AND NOT (COALESCE(canonical_source,'')=? AND"
            "                     COALESCE(canonical_album_id,'')=? AND"
            "                     COALESCE(canonical_locked,0)=1)",
            (source, str(provider_id), int(album_id), source, str(provider_id)))
    else:
        cursor.execute(
            "UPDATE lib2_albums SET canonical_source=NULL, canonical_album_id=NULL,"
            " canonical_score=NULL, canonical_locked=NULL, canonical_resolved_at=NULL,"
            " canonical_track_count=NULL, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=? AND LOWER(COALESCE(canonical_source,''))=?",
            (int(album_id), source))
    sync_default_edition(cursor, int(album_id))


def reconcile_pinned_editions(cursor: Any) -> int:
    """Default editions that do not name their album's canonical pin.

    Covers editions created before the pin was honoured, and a pin written
    outside :func:`sync_default_edition`. Only albums with a pin are read, so
    it stays cheap on every start. Returns how many editions changed.
    """
    rows = cursor.execute(
        """SELECT al.id FROM lib2_albums al
             JOIN lib2_release_editions ed
               ON ed.release_group_id = al.id AND ed.is_default = 1
            WHERE COALESCE(al.canonical_source, '') <> ''
              AND COALESCE(al.canonical_album_id, '') <> ''
              AND NOT (
                  (LOWER(al.canonical_source) = 'spotify'
                   AND COALESCE(ed.spotify_id, '') = al.canonical_album_id)
               OR (LOWER(al.canonical_source) = 'musicbrainz'
                   AND COALESCE(ed.musicbrainz_id, '') = al.canonical_album_id)
               OR COALESCE(CAST(json_extract(
                       CASE WHEN json_valid(ed.external_ids) THEN ed.external_ids
                            ELSE '{}' END,
                       '$.' || LOWER(al.canonical_source))
                                AS TEXT), '') = al.canonical_album_id)""").fetchall()
    return sum(1 for row in rows if sync_default_edition(cursor, int(row[0])))


def _find_recording_by_hard_ids(cursor: Any, isrc: Optional[str],
                                musicbrainz_id: Optional[str],
                                spotify_id: Optional[str]) -> Optional[int]:
    """Recording lookup on hard identifiers ONLY (ADR-04): ISRC first, then
    MB recording id, then Spotify track id. Titles never match anything."""
    for column, value in (("isrc", isrc), ("musicbrainz_id", musicbrainz_id),
                          ("spotify_id", spotify_id)):
        if not value:
            continue
        # iss32-M08: the `AND {column} IS NOT NULL AND {column} <> ''` is not
        # redundant — it is what makes the PARTIAL unique index usable.
        #
        # `idx_lib2_recordings_isrc` is declared `WHERE isrc IS NOT NULL AND
        # isrc <> ''`. SQLite may only use a partial index when the query's
        # WHERE provably implies the index's WHERE, and `isrc = ?` with a bound
        # parameter proves nothing at planning time. Without these conjuncts
        # the plan is `SCAN lib2_recordings` — a full table scan, three times
        # per track, against a table that grows to one row per track.
        #
        # Measured on a 307,885-track catalogue: 200 probes took 755 ms as a
        # scan and 3.1 ms through the index. That single line is the
        # difference between a migration that takes hours and one that takes
        # minutes, and it is why Nezreka's run looked hung — it was not stuck,
        # it was quadratic.
        row = cursor.execute(
            f"SELECT id FROM lib2_recordings "
            f"WHERE {column}=? AND {column} IS NOT NULL AND {column} <> ''",
            (value,)).fetchone()
        if row:
            return int(row[0])
    return None


def _fill_missing_hard_id(cursor: Any, recording_id: int, column: str,
                          value: Optional[str]) -> None:
    """Enrich a found recording with a hard ID it lacks — guarded so the
    partial unique indexes can never be violated."""
    if not value:
        return
    # Same partial-index rule as _find_recording_by_hard_ids: without the two
    # extra conjuncts the collision check is a full scan of lib2_recordings.
    cursor.execute(
        f"""UPDATE lib2_recordings SET {column}=?, updated_at=CURRENT_TIMESTAMP
             WHERE id=? AND ({column} IS NULL OR {column}='')
               AND NOT EXISTS (SELECT 1 FROM lib2_recordings o
                                WHERE o.{column}=? AND o.{column} IS NOT NULL
                                  AND o.{column} <> '' AND o.id<>?)""",
        (value, int(recording_id), value, int(recording_id)))


def reconcile_recording_identity(cursor: Any) -> Dict[str, int]:
    """Push hard ids a track has learned since into its recording.

    ``ensure_release_track`` returns early once a release track exists, so a
    track that gains its ISRC/MBID/Spotify id later — the tracklist fetch runs
    long after the row is materialized — leaves its recording identifier-less
    forever. Such a recording can never merge with the one the same song owns
    on another release, which is precisely how "Call of Silence" ended up
    present under its single and missing on the OST (docs §49.11).

    Two outcomes per track: the id is free, so the recording simply takes it
    (``filled``); or another recording already owns it, in which case that one
    IS this song and the release track is re-pointed at it (``merged``).
    Recordings left with no release track are dropped. Idempotent; never
    merges on anything but a hard id, and never commits.
    """
    stats = {"filled": 0, "merged": 0, "pruned": 0}
    rows = cursor.execute(
        """SELECT rt.id AS release_track_id, rt.recording_id, t.id AS track_id,
                  t.isrc, t.musicbrainz_id, t.spotify_id
             FROM lib2_release_tracks rt
             JOIN lib2_tracks t ON t.id = rt.track_id
             JOIN lib2_recordings rec ON rec.id = rt.recording_id
            WHERE (COALESCE(t.isrc,'') <> '' AND COALESCE(rec.isrc,'') = '')
               OR (COALESCE(t.musicbrainz_id,'') <> ''
                   AND COALESCE(rec.musicbrainz_id,'') = '')
               OR (COALESCE(t.spotify_id,'') <> ''
                   AND COALESCE(rec.spotify_id,'') = '')"""
    ).fetchall()

    touched_recordings = set()
    for row in rows:
        owner = _find_recording_by_hard_ids(
            cursor, row["isrc"], row["musicbrainz_id"], row["spotify_id"])
        if owner is not None and int(owner) != int(row["recording_id"]):
            touched_recordings.add(int(row["recording_id"]))
            cursor.execute(
                "UPDATE lib2_release_tracks SET recording_id=? WHERE id=?",
                (int(owner), int(row["release_track_id"])))
            stats["merged"] += 1
            target = int(owner)
        else:
            target = int(row["recording_id"])
        before = stats["filled"]
        for column in ("isrc", "musicbrainz_id", "spotify_id"):
            if not row[column]:
                continue
            cursor.execute(
                f"SELECT 1 FROM lib2_recordings WHERE id=? "
                f"AND ({column} IS NULL OR {column}='')", (target,))
            if cursor.fetchone() is None:
                continue
            _fill_missing_hard_id(cursor, target, column, row[column])
            if stats["filled"] == before:
                stats["filled"] += 1

    for recording_id in touched_recordings:
        cursor.execute(
            "DELETE FROM lib2_recordings WHERE id=? AND NOT EXISTS ("
            "  SELECT 1 FROM lib2_release_tracks WHERE recording_id=?)",
            (recording_id, recording_id))
        stats["pruned"] += cursor.rowcount or 0
    return stats


def ensure_release_track(cursor: Any, track_row: Any, edition_id: int) -> bool:
    """Materialize one lib2 track onto an edition (recording + release track).

    ``track_row`` needs id/title/duration/isrc/musicbrainz_id/spotify_id/
    disc_number/track_number. Idempotent per (edition, track). Returns True
    when a release track was created.
    """
    exists = cursor.execute(
        "SELECT 1 FROM lib2_release_tracks WHERE release_edition_id=? AND track_id=?",
        (int(edition_id), track_row["id"])).fetchone()
    if exists:
        return False
    recording_id = _find_recording_by_hard_ids(
        cursor, track_row["isrc"], track_row["musicbrainz_id"],
        track_row["spotify_id"])
    if recording_id is None:
        cursor.execute(
            """INSERT INTO lib2_recordings(title, duration, isrc,
                                           musicbrainz_id, spotify_id)
               VALUES(?,?,?,?,?)""",
            (track_row["title"], track_row["duration"],
             track_row["isrc"] or None, track_row["musicbrainz_id"] or None,
             track_row["spotify_id"] or None))
        recording_id = int(cursor.lastrowid)
    else:
        for column in ("isrc", "musicbrainz_id", "spotify_id"):
            _fill_missing_hard_id(cursor, recording_id, column, track_row[column])
    cursor.execute(
        """INSERT INTO lib2_release_tracks(release_edition_id, recording_id,
               track_id, disc_number, track_number, duration)
           VALUES(?,?,?,?,?,?)""",
        (int(edition_id), recording_id, track_row["id"],
         track_row["disc_number"], track_row["track_number"],
         track_row["duration"]))
    return True


def attach_track_to_edition(
    cursor: Any, track_id: int, *, edition_id: Optional[int] = None,
) -> bool:
    """Give one freshly created ``lib2_tracks`` row its edition membership.

    dd28-10: autolink created catalog tracks but never called
    :func:`ensure_release_track`, so a newly imported track was invisible to
    every edition-scoped consumer (bundle matching, the acquisition catalog).
    The only thing that eventually attached it was ``backfill_editions``, which
    runs at schema-ensure/legacy-import time and pins whatever it finds to
    ``default_edition_id`` — regardless of which edition was actually
    downloaded. Because the backfill is idempotent per ``(edition, track)``,
    that mis-assignment then became permanent.

    Attaching at creation time keeps the default-edition fallback (we usually
    genuinely do not know the edition) but lets a caller that DOES know pass
    ``edition_id`` and get it right the first time. Idempotent; does not commit.
    """
    # ``_ensure_default_edition``/``ensure_release_track`` read ``lastrowid``,
    # which only a Cursor exposes — autolink hands us its Connection.
    if hasattr(cursor, "cursor") and not hasattr(cursor, "lastrowid"):
        cursor = cursor.cursor()
    track_row = cursor.execute(
        """SELECT id, album_id, title, duration, isrc, musicbrainz_id,
                  spotify_id, disc_number, track_number
             FROM lib2_tracks WHERE id=?""",
        (int(track_id),),
    ).fetchone()
    if track_row is None or track_row["album_id"] is None:
        return False
    if edition_id is None:
        edition_id = default_edition_id(cursor, track_row["album_id"])
    if edition_id is None:
        album_row = cursor.execute(
            f"SELECT {_EDITION_ALBUM_COLUMNS} FROM lib2_albums WHERE id=?",
            (track_row["album_id"],),
        ).fetchone()
        if album_row is None:
            return False
        edition_id = _ensure_default_edition(cursor, album_row)
    return ensure_release_track(cursor, track_row, int(edition_id))


def _review_unverified_canonical_links(cursor: Any) -> int:
    """File a review finding for every canonical link whose pair shares no
    hard identifier — those may well be different recordings with the same
    name (P1-05) and must never merge silently."""
    cursor.execute(
        """INSERT OR IGNORE INTO lib2_recording_review(track_id, other_track_id, reason)
           SELECT t.id, c.id, 'canonical_link_unverified'
             FROM lib2_tracks t
             JOIN lib2_tracks c ON c.id = t.canonical_track_id
            WHERE NOT (
                  (t.isrc IS NOT NULL AND t.isrc <> '' AND t.isrc = c.isrc)
               OR (t.musicbrainz_id IS NOT NULL AND t.musicbrainz_id <> ''
                   AND t.musicbrainz_id = c.musicbrainz_id)
               OR (t.spotify_id IS NOT NULL AND t.spotify_id <> ''
                   AND t.spotify_id = c.spotify_id))""")
    return cursor.rowcount


def prune_orphaned_edition_rows(cursor: Any) -> int:
    """Drop shadow rows whose lib2 track vanished (entity deletes don't
    cascade through the compat link on every code path), then recordings and
    review findings nothing references anymore. Idempotent."""
    pruned = 0
    cursor.execute(
        """DELETE FROM lib2_release_tracks
            WHERE track_id IS NULL
               OR track_id NOT IN (SELECT id FROM lib2_tracks)""")
    pruned += cursor.rowcount
    cursor.execute(
        """DELETE FROM lib2_recordings
            WHERE id NOT IN (SELECT recording_id FROM lib2_release_tracks)""")
    pruned += cursor.rowcount
    cursor.execute(
        """DELETE FROM lib2_recording_review
            WHERE track_id NOT IN (SELECT id FROM lib2_tracks)
               OR other_track_id NOT IN (SELECT id FROM lib2_tracks)""")
    pruned += cursor.rowcount
    return pruned


def backfill_editions(cursor: Any, *, connection: Any = None,
                      batch_size: int = BACKFILL_BATCH_SIZE,
                      progress: Optional[Callable[[str, int, int], None]] = None,
                      on_batch: Optional[Callable[[], None]] = None,
                      should_stop: Optional[Callable[[], bool]] = None,
                      ) -> Dict[str, int]:
    """Converge the additive edition/recording model. Idempotent.

    Creates the default edition for every album that has none, one
    recording + release track for every track not yet materialized, files
    review findings for unverified canonical links and prunes shadow rows of
    deleted tracks.

    iss32-M05 — this used to ``fetchall()`` every unmaterialized album and
    track and write them all inside the caller's single transaction. On
    Nezreka's 69,296 albums / 307,885 tracks that held SQLite's only write
    lock for nine minutes: every enrichment worker, the automation engine and
    the config save saw ``database is locked``, and the run reported no
    progress at all because the next checkpoint only comes after the step.

    It now walks both sources by keyset in ``batch_size`` chunks. Pass
    ``connection`` to get a real ``COMMIT`` after every batch — that is what
    turns the nine-minute lock into a sequence of short ones and gives
    :mod:`core.library2.wal` a window to checkpoint in. Without it the
    behaviour is the old one (single transaction, caller commits), which is
    what the importer's own finalize and the test suite rely on.

    ``progress(stage, done, total)`` is called once per batch, ``on_batch()``
    after each commit (the WAL checkpoint hook), and ``should_stop()`` is
    polled between batches so a shutdown does not have to wait out a full
    library. Stopping early is safe: the predicate is "not yet materialized",
    so the next run continues where this one left off.
    """
    stats = {"editions": 0, "release_tracks": 0, "review_findings": 0, "pruned": 0}

    def _commit() -> None:
        if connection is not None:
            connection.commit()
            if on_batch is not None:
                on_batch()

    def _stopped() -> bool:
        return bool(should_stop and should_stop())

    def _report(stage: str, done: int, total: int) -> None:
        if progress is not None:
            progress(stage, done, total)

    stats["pruned"] = prune_orphaned_edition_rows(cursor)
    _commit()

    # --- Albums: one default edition each -----------------------------------
    album_total = int(cursor.execute(
        """SELECT COUNT(*) FROM lib2_albums al
            WHERE NOT EXISTS (SELECT 1 FROM lib2_release_editions e
                               WHERE e.release_group_id = al.id)""").fetchone()[0])
    _report("editions", 0, album_total)
    after_id = 0
    while not _stopped():
        albums = cursor.execute(
            f"""SELECT {', '.join('al.' + c.strip() for c in _EDITION_ALBUM_COLUMNS.split(','))}
                 FROM lib2_albums al
                WHERE al.id > ?
                  AND NOT EXISTS (SELECT 1 FROM lib2_release_editions e
                                   WHERE e.release_group_id = al.id)
                ORDER BY al.id LIMIT ?""", (after_id, batch_size)).fetchall()
        if not albums:
            break
        for album_row in albums:
            _ensure_default_edition(cursor, album_row)
            stats["editions"] += 1
            after_id = int(album_row["id"])
        _commit()
        _report("editions", stats["editions"], album_total)

    # --- Tracks: one recording + release track each -------------------------
    track_total = int(cursor.execute(
        """SELECT COUNT(*) FROM lib2_tracks t
            WHERE NOT EXISTS (SELECT 1 FROM lib2_release_tracks rt
                               WHERE rt.track_id = t.id)""").fetchone()[0])
    _report("release_tracks", 0, track_total)
    edition_cache: Dict[int, Optional[int]] = {}
    seen = 0
    after_id = 0
    while not _stopped():
        tracks = cursor.execute(
            """SELECT t.id, t.album_id, t.title, t.duration, t.isrc,
                      t.musicbrainz_id, t.spotify_id, t.disc_number, t.track_number
                 FROM lib2_tracks t
                WHERE t.id > ?
                  AND NOT EXISTS (SELECT 1 FROM lib2_release_tracks rt
                                   WHERE rt.track_id = t.id)
                ORDER BY t.id LIMIT ?""", (after_id, batch_size)).fetchall()
        if not tracks:
            break
        for track_row in tracks:
            # Keyset advances even for tracks we skip below — an album without
            # a default edition must not make the walk stand still forever.
            after_id = int(track_row["id"])
            seen += 1
            album_id = track_row["album_id"]
            if album_id not in edition_cache:
                edition_cache[album_id] = default_edition_id(cursor, album_id)
            edition_id = edition_cache[album_id]
            if edition_id is None:
                continue
            if ensure_release_track(cursor, track_row, edition_id):
                stats["release_tracks"] += 1
        _commit()
        _report("release_tracks", seen, track_total)

    stats["review_findings"] = _review_unverified_canonical_links(cursor)
    _commit()
    # Editions created before the canonical pin was honoured (A01).
    stats["pinned_editions"] = reconcile_pinned_editions(cursor)
    _commit()
    # Ids a track has learned since its release track was materialized (docs
    # §49.11). Runs last: the walk above may have just created the recording
    # this pass merges into.
    stats["identity"] = reconcile_recording_identity(cursor)
    _commit()
    if any(stats.values()):
        logger.info("Edition/recording backfill: %s", stats)
    return stats


__all__ = [
    "album_release_ids",
    "attach_track_to_edition",
    "backfill_editions",
    "pin_album_release",
    "default_edition_id",
    "edition_signature",
    "ensure_editions_schema",
    "ensure_release_track",
    "prune_orphaned_edition_rows",
    "reconcile_pinned_editions",
    "sync_default_edition",
]
