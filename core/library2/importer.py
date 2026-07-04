"""Import the existing (legacy) library into the Library v2 ``lib2_*`` tables.

This reads the legacy ``artists`` / ``albums`` / ``tracks`` tables **read-only** and
populates the v2 schema. It is:

- **Idempotent / re-runnable** — rows are keyed on their ``legacy_*_id`` so a second
  run reconciles instead of duplicating. ``reset=True`` wipes ``lib2_*`` first for a
  clean rebuild.
- **Defensive about columns** — the legacy schema has many migration-added columns
  that vary by install, so every SELECT is built from the columns that actually
  exist (``_existing_columns``).
- **Multi-artist aware** — a track's primary artist is its album artist; additional
  credits are parsed from the legacy ``track_artist`` field and from ``feat.`` /
  ``ft.`` markers in the title (``split_artist_credits``) and linked through the
  ``lib2_track_artists`` junction so the song is stored once but shows under each
  artist.
- **Single-vs-album aware** — after import, the same recording appearing both as a
  ``single`` and on a regular album is linked via ``canonical_track_id``
  (``link_single_album_duplicates``).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from utils.logging_config import get_logger

from .schema import ensure_library_v2_schema

logger = get_logger("library2.importer")

ProgressCb = Optional[Callable[[str, int, int], None]]

# Credit separators: "feat."/"ft."/"featuring"/"with" plus list separators.
_FEAT_RE = re.compile(r"\b(?:feat|ft|featuring|with)\b\.?", re.IGNORECASE)
_FEAT_IN_TITLE_RE = re.compile(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^)\]]+)[\)\]]", re.IGNORECASE)
_LIST_SEP_RE = re.compile(r"\s*(?:,|;|/|&|\bx\b|\band\b|\bvs\.?\b|×|\+)\s*", re.IGNORECASE)


def normalize_name(name: str) -> str:
    """Casefold + collapse whitespace for dedup keys. Not for display."""
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def split_artist_credits(*sources: str) -> List[str]:
    """Split one or more raw artist/credit strings into individual artist names.

    Handles ``"A feat. B"``, ``"A, B & C"``, ``"A x B"``, ``"A / B"`` etc. Order is
    preserved and duplicates (case-insensitive) are dropped. Empty inputs yield an
    empty list.
    """
    out: List[str] = []
    seen: Set[str] = set()
    for raw in sources:
        if not raw:
            continue
        # Promote any "feat." segment to a plain separator, then split the list.
        flattened = _FEAT_RE.sub(",", raw)
        for piece in _LIST_SEP_RE.split(flattened):
            name = piece.strip().strip("-").strip()
            if not name:
                continue
            key = normalize_name(name)
            if key and key not in seen:
                seen.add(key)
                out.append(name)
    return out


def featured_from_title(title: str) -> List[str]:
    """Extract featured-artist names embedded in a track title's ``(feat. X)`` tail."""
    names: List[str] = []
    for match in _FEAT_IN_TITLE_RE.finditer(title or ""):
        names.extend(split_artist_credits(match.group(1)))
    return names


def _existing_columns(cursor, table: str) -> Set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _pick(row: Any, *keys: str) -> Optional[Any]:
    """Return the first non-empty value among ``keys`` from a sqlite3.Row, or None."""
    for key in keys:
        try:
            val = row[key]
        except (IndexError, KeyError):
            continue
        if val not in (None, ""):
            return val
    return None


def _detect_single(track_count: Optional[int], actual_tracks: int) -> bool:
    """A legacy album with a single track is treated as a 'single'."""
    count = track_count if track_count else actual_tracks
    return bool(count) and count <= 1


def _normalize_album_type(raw: Any, track_count: Optional[int], actual_tracks: int) -> str:
    """Return a stable release type for the v2 release row.

    Prefer explicit provider metadata when the legacy DB has it. Only fall back to
    the one-track heuristic for old rows that have no release-type metadata at all.
    """
    value = str(raw or "").strip().lower()
    if value in {"album", "single", "ep", "compilation", "live"}:
        return value
    if value in {"appears_on", "appears-on"}:
        return "compilation"
    return "single" if _detect_single(track_count, actual_tracks) else "album"


class _ArtistResolver:
    """Resolves artist names to ``lib2_artists`` ids, creating rows on demand.

    Legacy artists are pre-seeded keyed on ``legacy_artist_id`` *and* normalized
    name, so featured artists parsed out of titles reuse an existing artist row when
    the name matches, and only create a new row when genuinely new.
    """

    def __init__(self, cursor):
        self.cursor = cursor
        self._by_name: Dict[str, int] = {}
        self._by_legacy: Dict[int, int] = {}

    def seed_existing(self) -> None:
        self.cursor.execute("SELECT id, name, legacy_artist_id FROM lib2_artists")
        for row in self.cursor.fetchall():
            self._by_name.setdefault(normalize_name(row["name"]), row["id"])
            if row["legacy_artist_id"] is not None:
                self._by_legacy[row["legacy_artist_id"]] = row["id"]

    def get_legacy(self, legacy_id: int) -> Optional[int]:
        return self._by_legacy.get(legacy_id)

    def get_or_create_by_name(self, name: str) -> int:
        key = normalize_name(name)
        existing = self._by_name.get(key)
        if existing is not None:
            return existing
        self.cursor.execute(
            "INSERT INTO lib2_artists(name, sort_name) VALUES(?, ?)", (name, name)
        )
        new_id = self.cursor.lastrowid
        self._by_name[key] = new_id
        return new_id

    def upsert_legacy(self, legacy_id: int, fields: Dict[str, Any]) -> int:
        """Insert or update a lib2 artist mirrored from a legacy artist row."""
        existing = self._by_legacy.get(legacy_id)
        if existing is not None:
            self.cursor.execute(
                "UPDATE lib2_artists SET name=?, sort_name=?, spotify_id=?, "
                "musicbrainz_id=?, image_url=?, genres=?, summary=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (fields["name"], fields["sort_name"], fields["spotify_id"],
                 fields["musicbrainz_id"], fields["image_url"], fields["genres"],
                 fields["summary"], existing),
            )
            self._by_name.setdefault(normalize_name(fields["name"]), existing)
            return existing
        self.cursor.execute(
            "INSERT INTO lib2_artists(name, sort_name, spotify_id, musicbrainz_id, "
            "image_url, genres, summary, legacy_artist_id) VALUES(?,?,?,?,?,?,?,?)",
            (fields["name"], fields["sort_name"], fields["spotify_id"],
             fields["musicbrainz_id"], fields["image_url"], fields["genres"],
             fields["summary"], legacy_id),
        )
        new_id = self.cursor.lastrowid
        self._by_legacy[legacy_id] = new_id
        self._by_name.setdefault(normalize_name(fields["name"]), new_id)
        return new_id


def _claim_discography_album(cursor, artist_id: int, title: str, album_type: str) -> Optional[int]:
    """Find a provider-only (origin='discography') row matching a legacy album.

    A discography expansion may have created the release before the user's files
    were imported; claiming that row (instead of inserting a fresh one) keeps a
    single release identity — its monitor state and metadata carry over.
    Matching mirrors ``core/library2/discography.py``: normalized title, prefer
    the same single-vs-release bucket.
    """
    key = normalize_name(title)
    want_single = (album_type or "").lower() == "single"
    cursor.execute(
        """SELECT id, title, album_type FROM lib2_albums
            WHERE primary_artist_id=? AND origin='discography' AND legacy_album_id IS NULL""",
        (artist_id,),
    )
    fallback: Optional[int] = None
    for row in cursor.fetchall():
        if normalize_name(row["title"]) != key:
            continue
        if ((row["album_type"] or "").lower() == "single") == want_single:
            return row["id"]
        if fallback is None:
            fallback = row["id"]
    return fallback


def _normalize_genres(raw: Any) -> str:
    """Mirror legacy genre storage (JSON array OR comma string) → JSON array string."""
    if not raw:
        return "[]"
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return json.dumps([str(g).strip() for g in parsed if str(g).strip()])
    except (ValueError, TypeError):
        pass
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    return json.dumps(parts)


def import_legacy_library(database, *, reset: bool = False, progress: ProgressCb = None,
                          profile_id: Optional[int] = None) -> Dict[str, int]:
    """Populate ``lib2_*`` from the legacy library. Returns a stats dict.

    ``database`` is a ``MusicDatabase`` instance (we use its ``_get_connection``).
    ``reset`` wipes the v2 tables first. ``progress(stage, current, total)`` is an
    optional callback for UI progress. ``profile_id`` scopes the watchlist/
    wishlist-derived monitoring to one user profile (None = legacy behavior,
    read everything).
    """
    stats = {
        "artists": 0,
        "albums": 0,
        "tracks": 0,
        "files": 0,
        "wishlist_tracks": 0,
        "linked_duplicates": 0,
    }
    conn = database._get_connection()
    try:
        ensure_library_v2_schema(conn)
        cursor = conn.cursor()

        if reset:
            for table in ("lib2_track_files", "lib2_track_artists", "lib2_tracks",
                          "lib2_album_artists", "lib2_albums", "lib2_artists"):
                cursor.execute(f"DELETE FROM {table}")

        artist_cols = _existing_columns(cursor, "artists")
        album_cols = _existing_columns(cursor, "albums")
        track_cols = _existing_columns(cursor, "tracks")

        resolver = _ArtistResolver(cursor)
        resolver.seed_existing()

        # --- Artists -------------------------------------------------------
        cursor.execute("SELECT * FROM artists")
        artist_rows = cursor.fetchall()
        for i, row in enumerate(artist_rows):
            name = row["name"]
            if not name:
                continue
            resolver.upsert_legacy(row["id"], {
                "name": name,
                "sort_name": name,
                "spotify_id": _pick(row, "spotify_artist_id"),
                "musicbrainz_id": _pick(row, "musicbrainz_id"),
                "image_url": _pick(row, "thumb_url", "banner_url"),
                "genres": _normalize_genres(_pick(row, "genres")),
                "summary": _pick(row, "summary"),
            })
            stats["artists"] += 1
            if progress and i % 200 == 0:
                progress("artists", i, len(artist_rows))

        # --- Albums (map legacy album id -> lib2 album id) -----------------
        album_map: Dict[int, int] = {}
        cursor.execute("SELECT id, legacy_album_id FROM lib2_albums WHERE legacy_album_id IS NOT NULL")
        for r in cursor.fetchall():
            album_map[r["legacy_album_id"]] = r["id"]

        cursor.execute("SELECT * FROM albums")
        album_rows = cursor.fetchall()
        for i, row in enumerate(album_rows):
            lib2_artist = resolver.get_legacy(row["artist_id"])
            if lib2_artist is None:
                continue  # orphan album with no artist; skip
            # actual track rows for single-detection
            cursor.execute("SELECT COUNT(*) AS c FROM tracks WHERE album_id=?", (row["id"],))
            actual = cursor.fetchone()["c"]
            track_count = _pick(row, "track_count")
            album_type = _normalize_album_type(
                _pick(row, "album_type", "release_type", "type"),
                track_count,
                actual,
            )
            year = _pick(row, "year")
            # Expected total = the metadata track count (api_track_count) when known,
            # else the stored track_count, else the number of tracks we actually have.
            expected = _pick(row, "api_track_count") or track_count or actual
            fields = (
                lib2_artist, row["title"], album_type,
                _pick(row, "release_date"), year,
                _pick(row, "spotify_album_id"), _pick(row, "musicbrainz_release_id"),
                _pick(row, "thumb_url"), _normalize_genres(_pick(row, "genres")),
                track_count, expected,
            )
            existing = album_map.get(row["id"])
            if existing is None:
                # A discography expansion may already have created a provider-only
                # row for this release — claim it instead of inserting a duplicate.
                existing = _claim_discography_album(
                    cursor, lib2_artist, row["title"], album_type)
                if existing is not None:
                    album_map[row["id"]] = existing
            if existing is not None:
                cursor.execute(
                    "UPDATE lib2_albums SET primary_artist_id=?, title=?, album_type=?, "
                    "release_date=?, year=?, spotify_id=COALESCE(?, spotify_id), "
                    "musicbrainz_id=COALESCE(?, musicbrainz_id), "
                    "image_url=COALESCE(?, image_url), "
                    "genres=?, track_count=?, expected_track_count=?, "
                    "origin='library', legacy_album_id=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (*fields, row["id"], existing),
                )
                album_id = existing
            else:
                cursor.execute(
                    "INSERT INTO lib2_albums(primary_artist_id, title, album_type, "
                    "release_date, year, spotify_id, musicbrainz_id, image_url, genres, "
                    "track_count, expected_track_count, legacy_album_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*fields, row["id"]),
                )
                album_id = cursor.lastrowid
                album_map[row["id"]] = album_id
            cursor.execute(
                "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
                "VALUES(?,?, 'primary')", (album_id, lib2_artist),
            )
            stats["albums"] += 1
            if progress and i % 200 == 0:
                progress("albums", i, len(album_rows))

        # --- Tracks + track files + track-artist junctions -----------------
        track_map: Dict[int, int] = {}
        cursor.execute("SELECT id, legacy_track_id FROM lib2_tracks WHERE legacy_track_id IS NOT NULL")
        for r in cursor.fetchall():
            track_map[r["legacy_track_id"]] = r["id"]

        cursor.execute("SELECT * FROM tracks")
        track_rows = cursor.fetchall()
        for i, row in enumerate(track_rows):
            album_id = album_map.get(row["album_id"])
            if album_id is None:
                continue
            title = row["title"]
            tfields = (
                album_id, title, _pick(row, "track_number"),
                _pick(row, "disc_number") or 1, _pick(row, "duration"),
                _pick(row, "isrc"), _pick(row, "musicbrainz_recording_id"),
                _pick(row, "spotify_track_id"),
            )
            existing = track_map.get(row["id"])
            if existing is not None:
                cursor.execute(
                    "UPDATE lib2_tracks SET album_id=?, title=?, track_number=?, "
                    "disc_number=?, duration=?, isrc=?, musicbrainz_id=?, spotify_id=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (*tfields, existing),
                )
                track_id = existing
            else:
                cursor.execute(
                    "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, "
                    "duration, isrc, musicbrainz_id, spotify_id, legacy_track_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (*tfields, row["id"]),
                )
                track_id = cursor.lastrowid
                track_map[row["id"]] = track_id
            stats["tracks"] += 1

            # Artist credits: primary = album artist; plus track_artist + title feats.
            primary_legacy = _pick(row, "artist_id")
            primary_lib2 = resolver.get_legacy(primary_legacy) if primary_legacy else None
            credits: List[Tuple[int, str, int]] = []  # (artist_id, role, position)
            if primary_lib2 is not None:
                credits.append((primary_lib2, "primary", 0))
            extra_names = split_artist_credits(_pick(row, "track_artist") or "")
            extra_names += featured_from_title(title)
            pos = 1
            for nm in extra_names:
                aid = resolver.get_or_create_by_name(nm)
                if aid not in {c[0] for c in credits}:
                    credits.append((aid, "featured", pos))
                    pos += 1
            # Reset this track's junction rows (idempotent re-run) then insert.
            cursor.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (track_id,))
            for aid, role, position in credits:
                cursor.execute(
                    "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position) "
                    "VALUES(?,?,?,?)", (track_id, aid, role, position),
                )

            # Track file from legacy file_path.
            file_path = _pick(row, "file_path")
            if file_path:
                fmt = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else None
                cursor.execute("SELECT id FROM lib2_track_files WHERE track_id=? AND path=?",
                               (track_id, file_path))
                if cursor.fetchone() is None:
                    cursor.execute(
                        "INSERT INTO lib2_track_files(track_id, path, size, bitrate, sample_rate, "
                        "bit_depth, format, verification_status, import_status) "
                        "VALUES(?,?,?,?,?,?,?,?, 'imported')",
                        (track_id, file_path, _pick(row, "file_size"), _pick(row, "bitrate"),
                         _pick(row, "sample_rate"), _pick(row, "bit_depth"), fmt,
                         _pick(row, "verification_status")),
                    )
                    stats["files"] += 1
            if progress and i % 200 == 0:
                progress("tracks", i, len(track_rows))

        stats["wishlist_tracks"] = seed_wishlist_tracks(cursor, resolver, profile_id)
        stats["linked_duplicates"] = link_single_album_duplicates(cursor)
        apply_monitoring_from_watchlist_wishlist(cursor, profile_id)
        conn.commit()
        logger.info("Library v2 import complete: %s", stats)
    finally:
        conn.close()
    return stats


def link_single_album_duplicates(cursor) -> int:
    """Link the same recording appearing as a single AND on a regular album.

    Groups tracks by (primary artist name, normalized title); when a group contains
    both a ``single``-type album track and a non-single album track, the single's
    ``canonical_track_id`` is pointed at the album track so the dedup UI can offer
    keep-single / keep-album / move / remove. Returns the number of links made.
    """
    cursor.execute(
        """
        SELECT t.id AS track_id, t.title AS title, al.album_type AS album_type,
               ar.name AS artist_name
        FROM lib2_tracks t
        JOIN lib2_albums al ON al.id = t.album_id
        JOIN lib2_artists ar ON ar.id = al.primary_artist_id
        """
    )
    groups: Dict[Tuple[str, str], List[Tuple[int, str]]] = {}
    for row in cursor.fetchall():
        key = (normalize_name(row["artist_name"]), normalize_name(row["title"]))
        groups.setdefault(key, []).append((row["track_id"], row["album_type"]))

    linked = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        album_tracks = [tid for tid, typ in members if typ != "single"]
        single_tracks = [tid for tid, typ in members if typ == "single"]
        if not album_tracks or not single_tracks:
            continue
        canonical = album_tracks[0]
        for single_id in single_tracks:
            cursor.execute(
                "UPDATE lib2_tracks SET canonical_track_id=? WHERE id=? AND "
                "(canonical_track_id IS NULL OR canonical_track_id<>?)",
                (canonical, single_id, canonical),
            )
            if cursor.rowcount:
                linked += 1
    return linked


def _first_image_url(images: Any) -> Optional[str]:
    if isinstance(images, list):
        for img in images:
            if isinstance(img, dict) and img.get("url"):
                return img["url"]
            if isinstance(img, str) and img:
                return img
    return None


def _artist_name_from_payload(artist: Any) -> Optional[str]:
    if isinstance(artist, dict):
        return artist.get("name")
    if isinstance(artist, str):
        return artist
    return None


def _artist_spotify_from_payload(artist: Any) -> Optional[str]:
    if isinstance(artist, dict):
        return artist.get("id")
    return None


def _album_type_from_payload(album: Dict[str, Any], total_tracks: int) -> str:
    typ = str(album.get("album_type") or album.get("type") or "").lower()
    if typ in {"album", "single", "ep", "compilation", "live"}:
        return typ
    if total_tracks and total_tracks <= 1:
        return "single"
    if total_tracks and total_tracks <= 6:
        return "ep"
    return "album"


def seed_wishlist_tracks(cursor, resolver: _ArtistResolver,
                         profile_id: Optional[int] = None) -> int:
    """Create lib2 metadata rows for wishlist-only tracks.

    The legacy library import only sees downloaded/scanned files. A user can have
    wishlist tracks for artists with zero local files; those still need lib2 rows
    so the Lidarr-style UI can show the concrete songs as monitored + missing.
    Importantly, a wishlisted song must not make the whole artist monitored:
    artist-level monitoring is the watchlist's job.
    ``profile_id`` scopes to one user profile's wishlist (None = all).
    """
    if not _table_exists(cursor, "wishlist_tracks"):
        return 0

    clause, params = _profile_filter(cursor, "wishlist_tracks", profile_id)
    rows = cursor.execute(
        "SELECT spotify_track_id, spotify_data, source_type, date_added FROM wishlist_tracks"
        + (f" WHERE {clause}" if clause else ""),
        params,
    ).fetchall()
    created_or_updated = 0

    for row in rows:
        try:
            payload = json.loads(row["spotify_data"] or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue

        track_id_raw = payload.get("id") or row["spotify_track_id"]
        track_id = str(track_id_raw or "").split("::", 1)[0]
        title = payload.get("name")
        if not track_id or not title:
            continue

        album = payload.get("album") if isinstance(payload.get("album"), dict) else {}
        album_title = album.get("name") or title
        album_spotify = album.get("id")
        total_tracks = int(album.get("total_tracks") or 1)
        album_type = _album_type_from_payload(album, total_tracks)
        release_date = album.get("release_date")
        try:
            year = int(str(release_date)[:4]) if release_date else None
        except (TypeError, ValueError):
            year = None
        album_image = _first_image_url(album.get("images")) or album.get("image_url")

        artists_payload = payload.get("artists") if isinstance(payload.get("artists"), list) else []
        album_artists_payload = album.get("artists") if isinstance(album.get("artists"), list) else []
        primary_payload = (album_artists_payload or artists_payload or [{"name": "Unknown Artist"}])[0]
        primary_name = _artist_name_from_payload(primary_payload) or "Unknown Artist"
        primary_spotify = _artist_spotify_from_payload(primary_payload)

        artist_id = resolver.get_or_create_by_name(primary_name)
        cursor.execute(
            """
            UPDATE lib2_artists
               SET spotify_id = COALESCE(NULLIF(spotify_id, ''), ?),
                   image_url = COALESCE(NULLIF(image_url, ''), image_url),
                   monitored = 0,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (primary_spotify, artist_id),
        )

        album_row = None
        if album_spotify:
            album_row = cursor.execute(
                "SELECT id FROM lib2_albums WHERE spotify_id=? AND primary_artist_id=?",
                (album_spotify, artist_id),
            ).fetchone()
        if album_row is None:
            album_row = cursor.execute(
                """SELECT id FROM lib2_albums
                   WHERE primary_artist_id=? AND lower(title)=lower(?) AND album_type=?""",
                (artist_id, album_title, album_type),
            ).fetchone()

        album_fields = (
            artist_id, album_title, album_type, release_date, year,
            album_spotify, album_image, total_tracks, total_tracks,
        )
        if album_row:
            album_id = album_row["id"]
            cursor.execute(
                """
                UPDATE lib2_albums
                   SET title=?, album_type=?, release_date=?, year=?,
                       spotify_id=COALESCE(NULLIF(spotify_id, ''), ?),
                       image_url=COALESCE(NULLIF(image_url, ''), ?),
                       track_count=COALESCE(track_count, ?),
                       expected_track_count=MAX(COALESCE(expected_track_count, 0), ?),
                       updated_at=CURRENT_TIMESTAMP
                 WHERE id=?
                """,
                (album_title, album_type, release_date, year, album_spotify,
                 album_image, total_tracks, total_tracks, album_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO lib2_albums(primary_artist_id, title, album_type,
                    release_date, year, spotify_id, image_url, track_count,
                    expected_track_count, monitored)
                VALUES(?,?,?,?,?,?,?,?,?,0)
                """,
                album_fields,
            )
            album_id = cursor.lastrowid
        cursor.execute(
            "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
            "VALUES(?,?, 'primary')",
                (album_id, artist_id),
        )

        existing_track = cursor.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=? AND spotify_id=?",
            (album_id, track_id),
        ).fetchone()
        track_number = payload.get("track_number")
        disc_number = payload.get("disc_number") or 1
        duration = payload.get("duration_ms")
        if existing_track:
            lib2_track_id = existing_track["id"]
            cursor.execute(
                """
                UPDATE lib2_tracks
                   SET title=?, track_number=COALESCE(track_number, ?),
                       disc_number=COALESCE(disc_number, ?), duration=COALESCE(duration, ?),
                       monitored=1, updated_at=CURRENT_TIMESTAMP
                 WHERE id=?
                """,
                (title, track_number, disc_number, duration, lib2_track_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,
                    duration, spotify_id, monitored)
                VALUES(?,?,?,?,?,?,1)
                """,
                (album_id, title, track_number, disc_number, duration, track_id),
            )
            lib2_track_id = cursor.lastrowid
            created_or_updated += 1

        # Wishlist payloads often contain the Spotify release's total_tracks but
        # not the titles for the other release tracks. For albums that only exist
        # because of wishlist rows, keep the expected size to the known wishlist
        # rows so the UI does not invent unnamed "Track N - missing" placeholders.
        cursor.execute(
            """
            UPDATE lib2_albums
               SET track_count = (
                       SELECT COUNT(*) FROM lib2_tracks WHERE album_id = ?
                   ),
                   expected_track_count = (
                       SELECT COUNT(*) FROM lib2_tracks WHERE album_id = ?
                   ),
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
               AND legacy_album_id IS NULL
               AND NOT EXISTS (
                   SELECT 1
                     FROM lib2_tracks t
                     JOIN lib2_track_files tf ON tf.track_id = t.id
                    WHERE t.album_id = ?
               )
            """,
            (album_id, album_id, album_id, album_id),
        )

        cursor.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (lib2_track_id,))
        linked_artists: Set[int] = set()
        for pos, artist_payload in enumerate(artists_payload or [primary_payload]):
            name = _artist_name_from_payload(artist_payload)
            if not name:
                continue
            aid = resolver.get_or_create_by_name(name)
            if aid in linked_artists:
                continue
            linked_artists.add(aid)
            spotify_id = _artist_spotify_from_payload(artist_payload)
            if spotify_id:
                cursor.execute(
                    "UPDATE lib2_artists SET spotify_id=COALESCE(NULLIF(spotify_id, ''), ?), "
                    "monitored=0 WHERE id=?",
                    (spotify_id, aid),
                )
            else:
                cursor.execute("UPDATE lib2_artists SET monitored=0 WHERE id=?", (aid,))
            cursor.execute(
                "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position) "
                "VALUES(?,?,?,?)",
                (lib2_track_id, aid, "primary" if pos == 0 else "featured", pos),
            )

    return created_or_updated


def _table_exists(cursor, name: str) -> bool:
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cursor.fetchone() is not None


def _profile_filter(cursor, table: str, profile_id: Optional[int]) -> Tuple[str, tuple]:
    """WHERE fragment scoping a legacy table to one user profile.

    Empty when no ``profile_id`` is given (single-profile installs / legacy
    behavior) or the table predates the ``profile_id`` column.
    """
    if profile_id is None or "profile_id" not in _existing_columns(cursor, table):
        return "", ()
    return "profile_id = ?", (int(profile_id),)


def apply_monitoring_from_watchlist_wishlist(cursor, profile_id: Optional[int] = None) -> None:
    """Make ``monitored`` reflect reality instead of defaulting everything to on.

    Monitoring is the same concept as the existing systems:
    - an artist is monitored iff it's on the **watchlist** (matched by external id
      or name),
    - a track found on the **wishlist** is monitored (matched by Spotify id).

    Album/single monitor flags are explicit Library-v2 state. Toggling an album
    in the UI still mirrors its tracks to the wishlist, but importing an existing
    wishlisted song does not turn the parent release into an album-level monitor.

    Artist flags follow the watchlist table. Track flags are Library-v2 state:
    wishlist rows turn tracks on, but successful downloads or explicit user
    choices must not be turned off just because the wishlist row disappeared.
    No-op when those tables are absent (unit-test DBs).

    ``profile_id`` scopes the derivation to one user profile's watchlist/
    wishlist rows so Library v2 doesn't leak another profile's wanted state
    into this view. ``None`` keeps the legacy read-everything behavior.
    """
    # Artists ← watchlist
    if _table_exists(cursor, "watchlist_artists"):
        clause, params = _profile_filter(cursor, "watchlist_artists", profile_id)
        cursor.execute("UPDATE lib2_artists SET monitored=0")
        wl = cursor.execute(
            "SELECT spotify_artist_id, musicbrainz_artist_id, lower(artist_name) AS n "
            "FROM watchlist_artists" + (f" WHERE {clause}" if clause else ""),
            params,
        ).fetchall()
        ext_ids = {x for r in wl for x in (r["spotify_artist_id"], r["musicbrainz_artist_id"]) if x}
        names = {r["n"] for r in wl if r["n"]}
        for ext in ext_ids:
            cursor.execute(
                "UPDATE lib2_artists SET monitored=1 WHERE spotify_id=? OR musicbrainz_id=?",
                (ext, ext),
            )
        for nm in names:
            cursor.execute("UPDATE lib2_artists SET monitored=1 WHERE lower(name)=?", (nm,))

    # Tracks ← wishlist. Do not reset non-wishlist tracks: monitored also powers
    # Lidarr-style upgrade checks after a file has already been downloaded.
    if _table_exists(cursor, "wishlist_tracks"):
        clause, params = _profile_filter(cursor, "wishlist_tracks", profile_id)
        wanted = {
            str(r[0]).split("::", 1)[0]
            for r in cursor.execute(
                "SELECT spotify_track_id FROM wishlist_tracks "
                "WHERE spotify_track_id IS NOT NULL"
                + (f" AND {clause}" if clause else ""),
                params,
            )
            if r[0]
        }
        for sid in wanted:
            cursor.execute("UPDATE lib2_tracks SET monitored=1 WHERE spotify_id=?", (sid,))
        # NOTE: an artist is monitored ONLY when on the watchlist — never just
        # because one of its tracks is wishlisted. A wishlisted song marks the
        # *track* as wanted, not the whole artist.


__all__ = [
    "import_legacy_library",
    "link_single_album_duplicates",
    "apply_monitoring_from_watchlist_wishlist",
    "split_artist_credits",
    "featured_from_title",
    "normalize_name",
]
