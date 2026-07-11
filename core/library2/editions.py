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
from typing import Any, Dict, Optional

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
)


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


def default_edition_id(cursor: Any, album_id: int) -> Optional[int]:
    row = cursor.execute(
        "SELECT id FROM lib2_release_editions "
        "WHERE release_group_id=? AND is_default=1", (int(album_id),)).fetchone()
    return int(row[0]) if row else None


def _ensure_default_edition(cursor: Any, album_row: Any) -> int:
    """The album's default edition id, creating it from the group's provider
    facts when missing (the additive §14.2-Schritt-3 backfill)."""
    existing = default_edition_id(cursor, album_row["id"])
    if existing is not None:
        return existing
    signature = edition_signature(album_row["spotify_id"],
                                  album_row["musicbrainz_id"],
                                  album_row["title"],
                                  album_row["expected_track_count"]
                                  or album_row["track_count"])
    cursor.execute(
        """INSERT INTO lib2_release_editions(
               release_group_id, is_default, spotify_id, musicbrainz_id,
               release_date, track_count, status, signature)
           VALUES(?,?,?,?,?,?, 'official', ?)""",
        (album_row["id"], 1, album_row["spotify_id"], album_row["musicbrainz_id"],
         album_row["release_date"],
         album_row["expected_track_count"] or album_row["track_count"],
         signature))
    return int(cursor.lastrowid)


def _find_recording_by_hard_ids(cursor: Any, isrc: Optional[str],
                                musicbrainz_id: Optional[str],
                                spotify_id: Optional[str]) -> Optional[int]:
    """Recording lookup on hard identifiers ONLY (ADR-04): ISRC first, then
    MB recording id, then Spotify track id. Titles never match anything."""
    for column, value in (("isrc", isrc), ("musicbrainz_id", musicbrainz_id),
                          ("spotify_id", spotify_id)):
        if not value:
            continue
        row = cursor.execute(
            f"SELECT id FROM lib2_recordings WHERE {column}=?", (value,)).fetchone()
        if row:
            return int(row[0])
    return None


def _fill_missing_hard_id(cursor: Any, recording_id: int, column: str,
                          value: Optional[str]) -> None:
    """Enrich a found recording with a hard ID it lacks — guarded so the
    partial unique indexes can never be violated."""
    if not value:
        return
    cursor.execute(
        f"""UPDATE lib2_recordings SET {column}=?, updated_at=CURRENT_TIMESTAMP
             WHERE id=? AND ({column} IS NULL OR {column}='')
               AND NOT EXISTS (SELECT 1 FROM lib2_recordings o
                                WHERE o.{column}=? AND o.id<>?)""",
        (value, int(recording_id), value, int(recording_id)))


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


def backfill_editions(cursor: Any) -> Dict[str, int]:
    """Converge the additive edition/recording model. Idempotent.

    Creates the default edition for every album that has none, one
    recording + release track for every track not yet materialized, files
    review findings for unverified canonical links and prunes shadow rows of
    deleted tracks. Runs from the schema-ensure step AND after imports.
    """
    stats = {"editions": 0, "release_tracks": 0, "review_findings": 0, "pruned": 0}
    stats["pruned"] = prune_orphaned_edition_rows(cursor)

    albums = cursor.execute(
        """SELECT al.id, al.title, al.spotify_id, al.musicbrainz_id,
                  al.release_date, al.track_count, al.expected_track_count
             FROM lib2_albums al
            WHERE NOT EXISTS (SELECT 1 FROM lib2_release_editions e
                               WHERE e.release_group_id = al.id)""").fetchall()
    for album_row in albums:
        _ensure_default_edition(cursor, album_row)
        stats["editions"] += 1

    tracks = cursor.execute(
        """SELECT t.id, t.album_id, t.title, t.duration, t.isrc,
                  t.musicbrainz_id, t.spotify_id, t.disc_number, t.track_number
             FROM lib2_tracks t
            WHERE NOT EXISTS (SELECT 1 FROM lib2_release_tracks rt
                               WHERE rt.track_id = t.id)
            ORDER BY t.id""").fetchall()
    edition_cache: Dict[int, Optional[int]] = {}
    for track_row in tracks:
        album_id = track_row["album_id"]
        if album_id not in edition_cache:
            edition_cache[album_id] = default_edition_id(cursor, album_id)
        edition_id = edition_cache[album_id]
        if edition_id is None:
            continue
        if ensure_release_track(cursor, track_row, edition_id):
            stats["release_tracks"] += 1

    stats["review_findings"] = _review_unverified_canonical_links(cursor)
    if any(stats.values()):
        logger.info("Edition/recording backfill: %s", stats)
    return stats


__all__ = [
    "backfill_editions",
    "default_edition_id",
    "edition_signature",
    "ensure_editions_schema",
    "ensure_release_track",
    "prune_orphaned_edition_rows",
]
