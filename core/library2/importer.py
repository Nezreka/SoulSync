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
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from utils.logging_config import get_logger

from .profile_lookup import default_quality_profile_id
from .schema import ensure_library_v2_schema

logger = get_logger("library2.importer")

ProgressCb = Optional[Callable[[str, int, int], None]]

# Credit separators: "feat."/"ft."/"featuring"/"with" plus list separators.
_FEAT_RE = re.compile(r"\b(?:feat|ft|featuring|with)\b\.?", re.IGNORECASE)
_FEAT_IN_TITLE_RE = re.compile(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^)\]]+)[\)\]]", re.IGNORECASE)
# A bare (un-parenthesized) trailing featured-artist credit, e.g. "Song feat. X".
# "with" is intentionally excluded here — bare "with" is too ambiguous (e.g.
# "Dancing With Myself"); only the parenthesized form above strips a "with" credit.
_FEAT_TITLE_TAIL_RE = re.compile(r"\s+(?:featuring|feat|ft)\b\.?\s+\S.*$", re.IGNORECASE)
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


def dedup_title_key(title: str) -> str:
    """Grouping key for single↔album duplicate detection.

    Drops a featured-artist annotation (``(feat. …)`` / ``[ft. …]`` /
    ``featuring …``) so the same recording links across releases even when only
    one side spells out the guests — the common real-world reason a single and
    its album cut carry different raw titles (#39). Version qualifiers (Remix,
    Live, Remastered, Acoustic, …) are deliberately preserved: those are distinct
    recordings and must not be collapsed into one canonical row.
    """
    text = _FEAT_IN_TITLE_RE.sub("", title or "")   # drop "(feat. …)"/"(with …)" groups
    text = _FEAT_TITLE_TAIL_RE.sub("", text)         # drop a bare trailing "feat. …"
    return normalize_name(text)


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


def _legacy_key(legacy_id: Any) -> Optional[str]:
    """Normalize a legacy row id to a stable dict key.

    A legacy ``artists``/``albums``/``tracks`` id can be TEXT (soulsync/Deezer-
    generated, e.g. '630009860') while the lib2 back-reference columns
    (``legacy_artist_id``/``legacy_album_id``/``legacy_track_id``) are
    INTEGER-affinity, so the SAME id round-trips as int on re-seed but str on
    lookup. Without coercion the re-import maps (``_by_legacy``/``album_map``/
    ``track_map``) miss the existing row and INSERT a duplicate every run
    (#38/#40 — duplicate artists AND albums/EPs/singles on re-import). Coerce to
    str on both write and read so re-imports always match the same row."""
    return None if legacy_id is None else str(legacy_id)


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


def _merge_album_external_ids(cursor, album_id: int, ids: Dict[str, Any]) -> None:
    """Merge {source: id} into an album's ``external_ids`` (never overwrites an
    existing source, so a discography-claimed row keeps its provider ids). Used
    to import EVERY provider album id (Deezer default), not only Spotify."""
    clean = {
        str(source).strip().lower(): str(value).strip()
        for source, value in ids.items()
        if value not in (None, "") and str(value).strip()
    }
    if not clean:
        return
    row = cursor.connection.execute(
        "SELECT external_ids FROM lib2_albums WHERE id=?", (album_id,)).fetchone()
    try:
        current = json.loads((row["external_ids"] if row else None) or "{}")
        if not isinstance(current, dict):
            current = {}
    except (TypeError, ValueError):
        current = {}
    merged = dict(current)
    for source, value in clean.items():
        merged.setdefault(source, value)
    if merged != current:
        cursor.execute(
            "UPDATE lib2_albums SET external_ids=? WHERE id=?",
            (json.dumps(merged, sort_keys=True, separators=(",", ":")), album_id))


class _ArtistResolver:
    """Resolves artist names to ``lib2_artists`` ids, creating rows on demand.

    Legacy artists are pre-seeded keyed on ``legacy_artist_id`` *and* normalized
    name, so featured artists parsed out of titles reuse an existing artist row when
    the name matches, and only create a new row when genuinely new.
    """

    def __init__(self, cursor, default_profile_id: int):
        self.cursor = cursor
        self.default_profile_id = default_profile_id
        self._by_name: Dict[str, int] = {}
        self._by_legacy: Dict[str, int] = {}
        # provider-id VALUE -> artist id. Source-agnostic on purpose: SoulSync
        # is multi-source (Deezer is the DEFAULT), so identity must key on ANY
        # provider id — Deezer/MusicBrainz/Spotify/… — not just Spotify. The
        # source→id map is persisted in the app-wide ``external_ids`` column
        # exactly like ``discography.py`` already uses it.
        self._by_provider: Dict[str, int] = {}

    @staticmethod
    def _clean_ids(ids: Optional[Dict[str, Any]]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for source, value in (ids or {}).items():
            src = str(source).strip().lower()
            val = str(value).strip() if value not in (None, "") else ""
            if src and val:
                out[src] = val
        return out

    def _register(self, artist_id: int, ids: Dict[str, str]) -> None:
        for value in ids.values():
            self._by_provider.setdefault(value, artist_id)

    def _stored_ids(self, artist_id: int) -> Dict[str, str]:
        """The artist's current source→id map (external_ids + the two columns)."""
        row = self.cursor.connection.execute(
            "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_artists WHERE id=?",
            (artist_id,)).fetchone()
        if not row:
            return {}
        ids: Dict[str, str] = {}
        try:
            raw = json.loads(row["external_ids"] or "{}")
            if isinstance(raw, dict):
                ids.update(self._clean_ids(raw))
        except (TypeError, ValueError):
            pass
        if row["spotify_id"]:
            ids.setdefault("spotify", str(row["spotify_id"]))
        if row["musicbrainz_id"]:
            ids.setdefault("musicbrainz", str(row["musicbrainz_id"]))
        return ids

    def _merge_ids(self, artist_id: int, ids: Dict[str, str]) -> None:
        """Adopt any NEW provider ids onto an existing artist (never overwrite)."""
        stored = self._stored_ids(artist_id)
        merged = dict(stored)
        for source, value in ids.items():
            merged.setdefault(source, value)
        if merged != stored:
            self.cursor.execute(
                "UPDATE lib2_artists SET external_ids=?, "
                "spotify_id=COALESCE(NULLIF(spotify_id,''), ?), "
                "musicbrainz_id=COALESCE(NULLIF(musicbrainz_id,''), ?), "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(merged, sort_keys=True, separators=(",", ":")),
                 merged.get("spotify"), merged.get("musicbrainz"), artist_id),
            )
        self._register(artist_id, merged)

    def seed_existing(self) -> None:
        self.cursor.execute(
            "SELECT id, name, legacy_artist_id, spotify_id, musicbrainz_id, "
            "external_ids FROM lib2_artists")
        for row in self.cursor.fetchall():
            self._by_name.setdefault(normalize_name(row["name"]), row["id"])
            if row["legacy_artist_id"] is not None:
                self._by_legacy[_legacy_key(row["legacy_artist_id"])] = row["id"]
            ids: Dict[str, str] = {}
            try:
                raw = json.loads(row["external_ids"] or "{}")
                if isinstance(raw, dict):
                    ids.update(self._clean_ids(raw))
            except (TypeError, ValueError):
                pass
            if row["spotify_id"]:
                ids.setdefault("spotify", str(row["spotify_id"]))
            if row["musicbrainz_id"]:
                ids.setdefault("musicbrainz", str(row["musicbrainz_id"]))
            self._register(row["id"], ids)

    def get_legacy(self, legacy_id: Any) -> Optional[int]:
        return self._by_legacy.get(_legacy_key(legacy_id))

    def known_name(self, name: str) -> bool:
        """Whether an artist with exactly this (normalized) name already exists."""
        return normalize_name(name) in self._by_name

    def get_or_create_by_name(self, name: str, *,
                              provider_ids: Optional[Dict[str, Any]] = None,
                              spotify_id: Optional[str] = None,
                              musicbrainz_id: Optional[str] = None) -> int:
        """Resolve an artist name to a lib2 id, disambiguating by provider id.

        A provider id (from ANY source — Deezer, MusicBrainz, Spotify, …) is the
        authoritative key (§16.3(b)): two artists sharing a display name but
        carrying DIFFERENT ids are distinct entities, and the same id always
        resolves to the same row even under a different display name — this is
        what stops an album from being hung on the wrong same-named artist (and
        its real tracklist never being fetchable). Only when no id matches do we
        fall back to the normalized-name key. A same-named row whose stored id
        CONFLICTS for the same source forces a new row; otherwise any new ids are
        adopted. ``spotify_id`` / ``musicbrainz_id`` are convenience aliases for
        ``provider_ids={'spotify': …, 'musicbrainz': …}``.
        """
        ids = self._clean_ids(provider_ids)
        if spotify_id:
            ids.setdefault("spotify", str(spotify_id).strip())
        if musicbrainz_id:
            ids.setdefault("musicbrainz", str(musicbrainz_id).strip())
        ids = self._clean_ids(ids)

        # 1) authoritative provider-id VALUE match (any source beats the name key)
        for value in ids.values():
            hit = self._by_provider.get(value)
            if hit is not None:
                self._merge_ids(hit, ids)
                return hit

        # 2) name match — reuse unless a stored id conflicts for the same source
        key = normalize_name(name)
        existing = self._by_name.get(key)
        if existing is not None:
            stored = self._stored_ids(existing)
            conflict = any(src in stored and stored[src] != val
                           for src, val in ids.items())
            if not conflict:
                self._merge_ids(existing, ids)  # adopt any new ids
                return existing

        # 3) create a fresh row (may share a name but is id-distinct)
        self.cursor.execute(
            "INSERT INTO lib2_artists(name, sort_name, spotify_id, musicbrainz_id, "
            "external_ids, quality_profile_id) VALUES(?,?,?,?,?,?)",
            (name, name, ids.get("spotify"), ids.get("musicbrainz"),
             json.dumps(ids, sort_keys=True, separators=(",", ":")) if ids else "{}",
             self.default_profile_id),
        )
        new_id = self.cursor.lastrowid
        self._by_name.setdefault(key, new_id)  # keep first-seen for name-only lookups
        self._register(new_id, ids)
        return new_id

    def upsert_legacy(
        self, legacy_id: int, fields: Dict[str, Any], run_id: str
    ) -> int:
        """Insert or update a lib2 artist mirrored from a legacy artist row.

        Captures EVERY provider id the legacy row carries (``fields['provider_ids']``
        = source→id, e.g. spotify/deezer/musicbrainz) into ``external_ids`` so a
        non-Spotify (e.g. Deezer-primary) library keeps its full identity — not
        just the two well-known columns.
        """
        ids = self._clean_ids(fields.get("provider_ids"))
        if fields.get("spotify_id"):
            ids.setdefault("spotify", str(fields["spotify_id"]).strip())
        if fields.get("musicbrainz_id"):
            ids.setdefault("musicbrainz", str(fields["musicbrainz_id"]).strip())
        external_json = json.dumps(ids, sort_keys=True, separators=(",", ":")) if ids else "{}"
        spotify_col, mbid_col = ids.get("spotify"), ids.get("musicbrainz")
        existing = self._by_legacy.get(_legacy_key(legacy_id))
        if existing is not None:
            self.cursor.execute(
                "UPDATE lib2_artists SET name=?, sort_name=?, spotify_id=?, "
                "musicbrainz_id=?, external_ids=?, image_url=?, genres=?, summary=?, "
                "legacy_import_run_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (fields["name"], fields["sort_name"], spotify_col, mbid_col,
                 external_json, fields["image_url"], fields["genres"],
                 fields["summary"], run_id, existing),
            )
            self._by_name.setdefault(normalize_name(fields["name"]), existing)
            self._register(existing, ids)
            return existing
        self.cursor.execute(
            "INSERT INTO lib2_artists(name, sort_name, spotify_id, musicbrainz_id, "
            "external_ids, image_url, genres, summary, legacy_artist_id, "
            "quality_profile_id, legacy_import_run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (fields["name"], fields["sort_name"], spotify_col, mbid_col, external_json,
             fields["image_url"], fields["genres"], fields["summary"], legacy_id,
             self.default_profile_id, run_id),
        )
        new_id = self.cursor.lastrowid
        self._by_legacy[_legacy_key(legacy_id)] = new_id
        self._by_name.setdefault(normalize_name(fields["name"]), new_id)
        self._register(new_id, ids)
        return new_id


def _discography_album_index(cursor) -> Dict[Tuple[int, str], List[Dict[str, Any]]]:
    """Preload claimable provider-only releases for O(1) legacy matching."""
    rows = cursor.execute(
        """SELECT id, primary_artist_id, title, album_type FROM lib2_albums
            WHERE origin='discography' AND legacy_album_id IS NULL"""
    ).fetchall()
    index: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
    for row in rows:
        index.setdefault(
            (int(row["primary_artist_id"]), normalize_name(row["title"])), []
        ).append(dict(row))
    return index


def _claim_discography_album(
    cursor,
    artist_id: int,
    title: str,
    album_type: str,
    *,
    index: Optional[Dict[Tuple[int, str], List[Dict[str, Any]]]] = None,
) -> Optional[int]:
    """Find a provider-only (origin='discography') row matching a legacy album.

    A discography expansion may have created the release before the user's files
    were imported; claiming that row (instead of inserting a fresh one) keeps a
    single release identity — its monitor state and metadata carry over.
    Matching mirrors ``core/library2/discography.py``: normalized title, prefer
    the same single-vs-release bucket.
    """
    key = normalize_name(title)
    want_single = (album_type or "").lower() == "single"
    if index is None:
        index = _discography_album_index(cursor)
    candidates = index.get((int(artist_id), key), [])
    if not candidates:
        return None
    selected = next(
        (
            row for row in candidates
            if ((row["album_type"] or "").lower() == "single") == want_single
        ),
        candidates[0],
    )
    candidates.remove(selected)
    return int(selected["id"])


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


def _has_preserved_intent(cursor, entity_type: str, entity_id: int) -> bool:
    """Whether a stale legacy row has an independent user-owned reason to live."""
    return cursor.execute(
        """SELECT 1 FROM lib2_monitor_rules
            WHERE entity_type=? AND entity_id=?
              AND provenance IN ('user_explicit', 'wishlist_import')
            LIMIT 1""",
        (entity_type, entity_id),
    ).fetchone() is not None


def _reconcile_legacy_snapshot(cursor, run_id: str) -> Dict[str, int]:
    """Reconcile importer-owned rows not observed in the current snapshot.

    Legacy-owned files are removed first. Metadata that also has a provider
    identity, explicit user/wishlist intent, or a non-legacy file is detached
    from the legacy source instead of deleted. This keeps snapshot semantics
    without letting the importer erase independently managed Library v2 state.
    """
    stats = {
        "reconciled_files": 0,
        "reconciled_tracks": 0,
        "reconciled_albums": 0,
        "reconciled_artists": 0,
    }

    cursor.execute(
        """DELETE FROM lib2_track_files
            WHERE legacy_track_id IS NOT NULL
              AND (legacy_import_run_id IS NULL OR legacy_import_run_id<>?)""",
        (run_id,),
    )
    stats["reconciled_files"] = cursor.rowcount

    stale_tracks = cursor.execute(
        """SELECT id FROM lib2_tracks
            WHERE legacy_track_id IS NOT NULL
              AND (legacy_import_run_id IS NULL OR legacy_import_run_id<>?)""",
        (run_id,),
    ).fetchall()
    for row in stale_tracks:
        track_id = int(row["id"])
        independently_backed = cursor.execute(
            """SELECT 1 FROM lib2_tracks t
                WHERE t.id=? AND (
                    NULLIF(t.spotify_id, '') IS NOT NULL
                    OR NULLIF(t.musicbrainz_id, '') IS NOT NULL
                    OR NULLIF(t.isrc, '') IS NOT NULL
                    OR EXISTS (
                        SELECT 1 FROM lib2_track_files f
                         WHERE f.track_id=t.id AND f.legacy_track_id IS NULL
                    )
                )""",
            (track_id,),
        ).fetchone()
        if independently_backed or _has_preserved_intent(cursor, "track", track_id):
            cursor.execute(
                """UPDATE lib2_tracks
                      SET legacy_track_id=NULL, legacy_import_run_id=NULL,
                          updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (track_id,),
            )
        else:
            cursor.execute("DELETE FROM lib2_tracks WHERE id=?", (track_id,))
        stats["reconciled_tracks"] += 1

    stale_albums = cursor.execute(
        """SELECT id FROM lib2_albums
            WHERE legacy_album_id IS NOT NULL
              AND (legacy_import_run_id IS NULL OR legacy_import_run_id<>?)""",
        (run_id,),
    ).fetchall()
    for row in stale_albums:
        album_id = int(row["id"])
        independently_backed = cursor.execute(
            """SELECT 1 FROM lib2_albums al
                WHERE al.id=? AND (
                    NULLIF(al.spotify_id, '') IS NOT NULL
                    OR NULLIF(al.musicbrainz_id, '') IS NOT NULL
                    OR EXISTS (SELECT 1 FROM lib2_tracks t WHERE t.album_id=al.id)
                )""",
            (album_id,),
        ).fetchone()
        if independently_backed or _has_preserved_intent(cursor, "album", album_id):
            cursor.execute(
                """UPDATE lib2_albums
                      SET legacy_album_id=NULL, legacy_import_run_id=NULL,
                          origin='discography', updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (album_id,),
            )
        else:
            cursor.execute("DELETE FROM lib2_albums WHERE id=?", (album_id,))
        stats["reconciled_albums"] += 1

    stale_artists = cursor.execute(
        """SELECT id FROM lib2_artists
            WHERE legacy_artist_id IS NOT NULL
              AND (legacy_import_run_id IS NULL OR legacy_import_run_id<>?)""",
        (run_id,),
    ).fetchall()
    for row in stale_artists:
        artist_id = int(row["id"])
        independently_backed = cursor.execute(
            """SELECT 1 FROM lib2_artists ar
                WHERE ar.id=? AND (
                    NULLIF(ar.spotify_id, '') IS NOT NULL
                    OR NULLIF(ar.musicbrainz_id, '') IS NOT NULL
                    OR EXISTS (SELECT 1 FROM lib2_albums al
                                WHERE al.primary_artist_id=ar.id)
                    OR EXISTS (SELECT 1 FROM lib2_album_artists aa
                                WHERE aa.artist_id=ar.id)
                    OR EXISTS (SELECT 1 FROM lib2_track_artists ta
                                WHERE ta.artist_id=ar.id)
                )""",
            (artist_id,),
        ).fetchone()
        if independently_backed or _has_preserved_intent(cursor, "artist", artist_id):
            cursor.execute(
                """UPDATE lib2_artists
                      SET legacy_artist_id=NULL, legacy_import_run_id=NULL,
                          updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (artist_id,),
            )
        else:
            cursor.execute("DELETE FROM lib2_artists WHERE id=?", (artist_id,))
        stats["reconciled_artists"] += 1

    from core.library2.monitor_rules import prune_orphaned_rules
    prune_orphaned_rules(cursor)
    return stats


def import_legacy_library(database, *, reset: bool = False, progress: ProgressCb = None,
                          profile_id: Optional[int] = None) -> Dict[str, int]:
    """Populate ``lib2_*`` from the legacy library. Returns a stats dict.

    ``database`` is a ``MusicDatabase`` instance (we use its ``_get_connection``).
    ``reset`` wipes the v2 tables first. ``progress(stage, current, total)`` is an
    optional callback for UI progress. ``profile_id`` scopes the watchlist/
    wishlist-derived monitoring to the admin profile. Omitting it is an alias
    for admin profile 1, never for an all-profile import.

    ADR-01 (admin-only): only the admin profile may drive this import. The
    lib2 monitored flags are GLOBAL columns derived from exactly one
    profile's watchlist/wishlist here — importing with another profile would
    overwrite the admin's monitoring intent for everyone (audit P0-02).
    """
    from core.library2 import ADMIN_PROFILE_ID
    effective_profile_id = (ADMIN_PROFILE_ID if profile_id is None
                            else int(profile_id))
    if effective_profile_id != ADMIN_PROFILE_ID:
        raise ValueError(
            f"Library v2 import is admin-only (ADR-01): got profile_id={profile_id}, "
            f"expected {ADMIN_PROFILE_ID}")
    profile_id = effective_profile_id
    stats = {
        "artists": 0,
        "albums": 0,
        "tracks": 0,
        "files": 0,
        "wishlist_tracks": 0,
        "linked_duplicates": 0,
        "reconciled_files": 0,
        "reconciled_tracks": 0,
        "reconciled_albums": 0,
        "reconciled_artists": 0,
    }
    conn = database._get_connection()
    try:
        ensure_library_v2_schema(conn)
        cursor = conn.cursor()
        run_id = uuid.uuid4().hex

        preserved_album_intent = {}

        if reset:
            # Local ids change across a destructive rebuild. Preserve deliberate
            # album intent by provider/stable identity, never by surrogate id.
            from core.library2.monitor_rules import snapshot_album_monitor_intent
            from core.library2.stable_ids import backfill_stable_ids
            backfill_stable_ids(cursor)
            preserved_album_intent = snapshot_album_monitor_intent(
                conn, profile_id=profile_id
            )
            # lib2_manual_skips deliberately survives a reset: it's an audit of
            # user decisions about FILES, not derived library state.
            for table in ("lib2_track_files", "lib2_track_artists", "lib2_tracks",
                          "lib2_album_artists", "lib2_albums", "lib2_artists"):
                cursor.execute(f"DELETE FROM {table}")
            cursor.execute("DELETE FROM lib2_monitor_rules")
            cursor.execute("DELETE FROM lib2_wanted_tracks")

        artist_cols = _existing_columns(cursor, "artists")
        album_cols = _existing_columns(cursor, "albums")
        track_cols = _existing_columns(cursor, "tracks")

        default_profile_id = default_quality_profile_id(conn)
        resolver = _ArtistResolver(cursor, default_profile_id)
        resolver.seed_existing()
        discography_albums = _discography_album_index(cursor)

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
                "musicbrainz_id": _pick(row, "musicbrainz_artist_id", "musicbrainz_id"),
                # Import EVERY provider id the legacy row carries (SoulSync's
                # default source is Deezer, not Spotify) into external_ids.
                # The real legacy schema names these deezer_id/tidal_id/qobuz_id
                # (only Spotify/MusicBrainz carry the *_artist_id suffix). Accept
                # both so a Deezer-primary artist keeps its id in external_ids —
                # without it, expand_artist_discography has no id to fetch with
                # and "Update Discography" returns only a stray single (#38).
                "provider_ids": {
                    "spotify": _pick(row, "spotify_artist_id"),
                    "deezer": _pick(row, "deezer_artist_id", "deezer_id"),
                    "musicbrainz": _pick(row, "musicbrainz_artist_id", "musicbrainz_id"),
                    "tidal": _pick(row, "tidal_artist_id", "tidal_id"),
                    "qobuz": _pick(row, "qobuz_artist_id", "qobuz_id"),
                },
                "image_url": _pick(row, "thumb_url", "banner_url"),
                "genres": _normalize_genres(_pick(row, "genres")),
                "summary": _pick(row, "summary"),
            }, run_id)
            stats["artists"] += 1
            if progress and i % 200 == 0:
                progress("artists", i, len(artist_rows))

        # --- Albums (map legacy album id -> lib2 album id) -----------------
        album_map: Dict[str, int] = {}
        cursor.execute("SELECT id, legacy_album_id FROM lib2_albums WHERE legacy_album_id IS NOT NULL")
        for r in cursor.fetchall():
            album_map[_legacy_key(r["legacy_album_id"])] = r["id"]

        cursor.execute("SELECT * FROM albums")
        album_rows = cursor.fetchall()
        actual_track_counts = {
            int(row["album_id"]): int(row["count"])
            for row in cursor.execute(
                "SELECT album_id, COUNT(*) AS count FROM tracks GROUP BY album_id"
            ).fetchall()
        }
        # Files actually present per legacy album — used to derive the initial
        # album monitor flag (§16.2). An album is only auto-monitored when it is
        # fully owned; a partially-downloaded album must NOT be blanket-monitored
        # (that would project every un-owned track wanted and auto-grab it).
        present_track_counts = {
            int(row["album_id"]): int(row["count"])
            for row in cursor.execute(
                "SELECT album_id, COUNT(*) AS count FROM tracks "
                "WHERE file_path IS NOT NULL AND TRIM(file_path) <> '' "
                "GROUP BY album_id"
            ).fetchall()
        }
        for i, row in enumerate(album_rows):
            lib2_artist = resolver.get_legacy(row["artist_id"])
            if lib2_artist is None:
                continue  # orphan album with no artist; skip
            # actual track rows for single-detection
            actual = actual_track_counts.get(int(row["id"]), 0)
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
            existing = album_map.get(_legacy_key(row["id"]))
            if existing is None:
                # A discography expansion may already have created a provider-only
                # row for this release — claim it instead of inserting a duplicate.
                existing = _claim_discography_album(
                    cursor,
                    lib2_artist,
                    row["title"],
                    album_type,
                    index=discography_albums,
                )
                if existing is not None:
                    album_map[_legacy_key(row["id"])] = existing
            if existing is not None:
                cursor.execute(
                    "UPDATE lib2_albums SET primary_artist_id=?, title=?, album_type=?, "
                    "release_date=?, year=?, spotify_id=COALESCE(?, spotify_id), "
                    "musicbrainz_id=COALESCE(?, musicbrainz_id), "
                    "image_url=COALESCE(?, image_url), "
                    "genres=?, track_count=?, expected_track_count=?, "
                    "origin='library', legacy_album_id=?, legacy_import_run_id=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (*fields, row["id"], run_id, existing),
                )
                album_id = existing
            else:
                # Derive the initial album monitor flag from ownership (§16.2)
                # instead of taking the schema default of 1. Fully owned (every
                # known track present, and at least as many as the metadata
                # expects) → monitored; anything partial → unmonitored, so only
                # the concretely-wanted tracks (wishlist rules) stay wanted.
                present = present_track_counts.get(int(row["id"]), 0)
                album_monitored = (
                    1 if present and present >= actual and present >= (expected or 0)
                    else 0
                )
                cursor.execute(
                    "INSERT INTO lib2_albums(primary_artist_id, title, album_type, "
                    "release_date, year, spotify_id, musicbrainz_id, image_url, genres, "
                    "track_count, expected_track_count, legacy_album_id, "
                    "quality_profile_id, monitored, legacy_import_run_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*fields, row["id"], default_profile_id, album_monitored, run_id),
                )
                album_id = cursor.lastrowid
                album_map[_legacy_key(row["id"])] = album_id
            # Real legacy albums carry deezer_id/tidal_id/qobuz_id; accept the
            # *_album_id aliases too (see the artist provider_ids note above).
            _merge_album_external_ids(cursor, album_id, {
                "spotify": _pick(row, "spotify_album_id"),
                "deezer": _pick(row, "deezer_album_id", "deezer_id"),
                "musicbrainz": _pick(row, "musicbrainz_release_id"),
                "tidal": _pick(row, "tidal_album_id", "tidal_id"),
                "qobuz": _pick(row, "qobuz_album_id", "qobuz_id"),
            })
            cursor.execute(
                "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
                "VALUES(?,?, 'primary')", (album_id, lib2_artist),
            )
            stats["albums"] += 1
            if progress and i % 200 == 0:
                progress("albums", i, len(album_rows))

        # --- Tracks + track files + track-artist junctions -----------------
        track_map: Dict[str, int] = {}
        cursor.execute("SELECT id, legacy_track_id FROM lib2_tracks WHERE legacy_track_id IS NOT NULL")
        for r in cursor.fetchall():
            track_map[_legacy_key(r["legacy_track_id"])] = r["id"]
        existing_files = {
            (int(row["track_id"]), str(row["path"])): int(row["id"])
            for row in cursor.execute(
                "SELECT id, track_id, path FROM lib2_track_files "
                "WHERE track_id IS NOT NULL AND path IS NOT NULL"
            ).fetchall()
        }
        # A newly-created track's own ``monitored`` flag must agree with its
        # album's (§16.2 follow-up): the wanted projection's album rule always
        # decides an un-ruled track's wanted state over its own flag (see
        # wanted.py's priority order — album rule outranks a mere legacy_import
        # track rule), and the runtime album-monitor cascade already applies
        # this same album-wins convention to every non-explicit child track
        # (api/library_v2.py ``lib2_set_monitored``, "albums" branch). Reading
        # it fresh here (not the local per-album loop var) also covers
        # existing/updated albums, whose monitored flag isn't recomputed above.
        album_monitored_by_id = {
            int(r["id"]): int(r["monitored"])
            for r in cursor.execute("SELECT id, monitored FROM lib2_albums").fetchall()
        }

        cursor.execute("SELECT * FROM tracks")
        track_rows = cursor.fetchall()
        for i, row in enumerate(track_rows):
            album_id = album_map.get(_legacy_key(row["album_id"]))
            if album_id is None:
                continue
            title = row["title"]
            tfields = (
                album_id, title, _pick(row, "track_number"),
                _pick(row, "disc_number") or 1, _pick(row, "duration"),
                _pick(row, "isrc"), _pick(row, "musicbrainz_recording_id"),
                _pick(row, "spotify_track_id"),
            )
            existing = track_map.get(_legacy_key(row["id"]))
            if existing is not None:
                cursor.execute(
                    "UPDATE lib2_tracks SET album_id=?, title=?, track_number=?, "
                    "disc_number=?, duration=?, isrc=?, musicbrainz_id=?, spotify_id=?, "
                    "legacy_import_run_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (*tfields, run_id, existing),
                )
                track_id = existing
            else:
                track_monitored = album_monitored_by_id.get(album_id, 1)
                cursor.execute(
                    "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, "
                    "duration, isrc, musicbrainz_id, spotify_id, legacy_track_id, "
                    "quality_profile_id, monitored, legacy_import_run_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*tfields, row["id"], default_profile_id, track_monitored, run_id),
                )
                track_id = cursor.lastrowid
                track_map[_legacy_key(row["id"])] = track_id
            stats["tracks"] += 1

            # Artist credits: primary = album artist; plus track_artist + title feats.
            primary_legacy = _pick(row, "artist_id")
            primary_lib2 = resolver.get_legacy(primary_legacy) if primary_legacy else None
            credits: List[Tuple[int, str, int]] = []  # (artist_id, role, position)
            if primary_lib2 is not None:
                credits.append((primary_lib2, "primary", 0))
            # Band names legitimately contain the list separators ("Simon &
            # Garfunkel", "Florence and the Machine") — when the FULL credit
            # string is already a known artist, trust it over the split
            # heuristic so we don't invent ghost artists.
            raw_credit = _pick(row, "track_artist") or ""
            if raw_credit and resolver.known_name(raw_credit):
                extra_names = [raw_credit.strip()]
            else:
                extra_names = split_artist_credits(raw_credit)
            extra_names += featured_from_title(title)
            pos = 1
            for nm in extra_names:
                aid = resolver.get_or_create_by_name(nm)
                if aid not in {c[0] for c in credits}:
                    credits.append((aid, "featured", pos))
                    pos += 1
            # Reset this track's junction rows (idempotent re-run) then insert.
            cursor.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (track_id,))
            if credits:
                cursor.executemany(
                    "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position) "
                    "VALUES(?,?,?,?)",
                    [(track_id, aid, role, position) for aid, role, position in credits],
                )

            # Track file from legacy file_path.
            file_path = _pick(row, "file_path")
            if file_path:
                fmt = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else None
                file_key = (int(track_id), str(file_path))
                file_id = existing_files.get(file_key)
                if file_id is None:
                    cursor.execute(
                        "INSERT INTO lib2_track_files(track_id, path, size, bitrate, sample_rate, "
                        "bit_depth, format, verification_status, import_status, legacy_track_id, "
                        "legacy_import_run_id) VALUES(?,?,?,?,?,?,?,?, 'imported',?,?)",
                        (track_id, file_path, _pick(row, "file_size"), _pick(row, "bitrate"),
                         _pick(row, "sample_rate"), _pick(row, "bit_depth"), fmt,
                         _pick(row, "verification_status"), row["id"], run_id),
                    )
                    existing_files[file_key] = int(cursor.lastrowid)
                    stats["files"] += 1
                else:
                    # Adopt pre-P1-02 rows when their exact current path matches;
                    # unrelated secondary files stay unowned and are never pruned.
                    cursor.execute(
                        """UPDATE lib2_track_files
                              SET size=?, bitrate=?, sample_rate=?, bit_depth=?, format=?,
                                  verification_status=?, legacy_track_id=?,
                                  legacy_import_run_id=?, updated_at=CURRENT_TIMESTAMP
                            WHERE id=?""",
                        (_pick(row, "file_size"), _pick(row, "bitrate"),
                         _pick(row, "sample_rate"), _pick(row, "bit_depth"), fmt,
                         _pick(row, "verification_status"), row["id"], run_id, file_id),
                    )
            if progress and i % 200 == 0:
                progress("tracks", i, len(track_rows))

        stats.update(_reconcile_legacy_snapshot(cursor, run_id))
        stats["wishlist_tracks"] = seed_wishlist_tracks(cursor, resolver, profile_id)
        stats["linked_duplicates"] = link_single_album_duplicates(cursor)
        apply_monitoring_from_watchlist_wishlist(cursor, profile_id)
        # Mint provider-less stable ids for everything this run inserted
        # (audit P1-12) — the schema-ensure backfill ran before the inserts.
        from core.library2.stable_ids import backfill_stable_ids
        backfill_stable_ids(cursor)
        from core.library2.monitor_rules import (
            project_entity_monitor_rules,
            restore_album_monitor_intent,
        )
        stats["album_monitor_intent_restored"] = restore_album_monitor_intent(
            conn, preserved_album_intent, profile_id=profile_id
        )
        # Import-derived monitored flags are provenance 'legacy_import', never
        # mistaken for deliberate user choices (audit P1-13/P1-14). Recorded
        # intent (re-import over an existing library) is never downgraded.
        from core.library2.monitor_rules import seed_legacy_rules
        seed_legacy_rules(cursor)
        project_entity_monitor_rules(conn, profile_id=profile_id)
        # Materialize the edition/recording shadow model for everything this
        # run inserted (audit P1-04 / ADR-04) — the schema-ensure backfill ran
        # before the inserts, so it has to run again here.
        from core.library2.editions import backfill_editions
        stats["editions"] = backfill_editions(cursor)
        # Rebuild the wanted projection over the imported rules (§11.2).
        from core.library2.wanted import ensure_wanted_schema, recompute_wanted
        ensure_wanted_schema(cursor)
        stats["wanted"] = recompute_wanted(cursor, profile_id=profile_id or 1)
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
        key = (normalize_name(row["artist_name"]), dedup_title_key(row["title"]))
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
    ``profile_id`` is restricted to the admin wishlist (None = admin profile).
    """
    if not _table_exists(cursor, "wishlist_tracks"):
        return 0

    wishlist_columns = _existing_columns(cursor, "wishlist_tracks")
    clause, params = _profile_filter(cursor, "wishlist_tracks", profile_id)
    quality_select = ("quality_profile_id" if "quality_profile_id" in wishlist_columns
                      else "NULL AS quality_profile_id")
    rows = cursor.execute(
        "SELECT id, spotify_track_id, spotify_data, source_type, date_added, "
        + quality_select + " FROM wishlist_tracks"
        + (f" WHERE {clause}" if clause else "")
        + " ORDER BY id",
        params,
    ).fetchall()

    default_profile_id = default_quality_profile_id(cursor.connection)
    valid_profile_ids = {
        int(row[0]) for row in cursor.execute("SELECT id FROM quality_profiles").fetchall()
    }

    def _track_profile(row) -> int:
        raw_profile_id = row["quality_profile_id"]
        if raw_profile_id is None:
            return default_profile_id
        try:
            candidate = int(raw_profile_id)
        except (TypeError, ValueError):
            candidate = None
        if candidate in valid_profile_ids:
            return candidate
        logger.warning(
            "Wishlist row %s references invalid quality profile %r; using default %s",
            row["id"], raw_profile_id, default_profile_id,
        )
        return default_profile_id

    created_or_updated = 0
    assigned_profiles: Dict[Tuple[int, str], Tuple[int, int]] = {}
    album_by_spotify: Dict[Tuple[int, str], int] = {}
    album_by_identity: Dict[Tuple[int, str, str], int] = {}
    for album_row in cursor.execute(
        "SELECT id, primary_artist_id, title, album_type, spotify_id FROM lib2_albums"
    ).fetchall():
        album_id = int(album_row["id"])
        artist_id = int(album_row["primary_artist_id"])
        if album_row["spotify_id"]:
            album_by_spotify[(artist_id, str(album_row["spotify_id"]))] = album_id
        album_by_identity[
            (artist_id, normalize_name(album_row["title"]), album_row["album_type"])
        ] = album_id
    track_by_spotify = {
        (int(track_row["album_id"]), str(track_row["spotify_id"])): int(track_row["id"])
        for track_row in cursor.execute(
            "SELECT id, album_id, spotify_id FROM lib2_tracks "
            "WHERE spotify_id IS NOT NULL AND spotify_id<>''"
        ).fetchall()
    }
    albums_to_recount: Set[int] = set()

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
        quality_profile_id = _track_profile(row)

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

        # monitored=0 here is safe only because apply_monitoring_from_watchlist_
        # wishlist() runs AFTER seeding and re-derives artist flags from the
        # watchlist — a wishlisted song must never monitor the whole artist.
        artist_id = resolver.get_or_create_by_name(primary_name, spotify_id=primary_spotify)
        cursor.execute(
            """
            UPDATE lib2_artists
               SET spotify_id = COALESCE(NULLIF(spotify_id, ''), ?),
                   monitored = 0,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (primary_spotify, artist_id),
        )

        album_id = (
            album_by_spotify.get((artist_id, str(album_spotify)))
            if album_spotify else None
        )
        if album_id is None:
            album_id = album_by_identity.get(
                (artist_id, normalize_name(album_title), album_type)
            )

        album_fields = (
            artist_id, album_title, album_type, release_date, year,
            album_spotify, album_image, total_tracks, total_tracks,
        )
        if album_id is not None:
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
                    expected_track_count, monitored, quality_profile_id)
                VALUES(?,?,?,?,?,?,?,?,?,0,?)
                """,
                (*album_fields, default_profile_id),
            )
            album_id = cursor.lastrowid
        album_by_identity[
            (artist_id, normalize_name(album_title), album_type)
        ] = album_id
        if album_spotify:
            album_by_spotify[(artist_id, str(album_spotify))] = album_id
        cursor.execute(
            "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
            "VALUES(?,?, 'primary')",
                (album_id, artist_id),
        )

        existing_track_id = track_by_spotify.get((int(album_id), track_id))
        entity_key = (album_id, track_id)
        previous_assignment = assigned_profiles.get(entity_key)
        if previous_assignment and previous_assignment[0] != quality_profile_id:
            logger.warning(
                "Wishlist rows %s and %s assign different quality profiles to "
                "track %s on album %s; latest row wins (%s)",
                previous_assignment[1], row["id"], track_id, album_id,
                quality_profile_id,
            )
        assigned_profiles[entity_key] = (quality_profile_id, row["id"])
        track_number = payload.get("track_number")
        disc_number = payload.get("disc_number") or 1
        duration = payload.get("duration_ms")
        if existing_track_id is not None:
            lib2_track_id = existing_track_id
            cursor.execute(
                """
                UPDATE lib2_tracks
                   SET title=?, track_number=COALESCE(track_number, ?),
                       disc_number=COALESCE(disc_number, ?), duration=COALESCE(duration, ?),
                       quality_profile_id=?, monitored=1,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE id=?
                """,
                (title, track_number, disc_number, duration, quality_profile_id,
                 lib2_track_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,
                    duration, spotify_id, quality_profile_id, monitored)
                VALUES(?,?,?,?,?,?,?,1)
                """,
                (album_id, title, track_number, disc_number, duration, track_id,
                 quality_profile_id),
            )
            lib2_track_id = cursor.lastrowid
            track_by_spotify[(int(album_id), track_id)] = lib2_track_id
            created_or_updated += 1

        # Presence in the admin Wishlist is concrete track-level wanted
        # intent. Keep it distinct from a Library-v2 click, but stronger than
        # the parent album's imported unmonitored baseline.
        from core.library2.monitor_rules import PROVENANCE_WISHLIST, record_rule
        record_rule(
            cursor.connection,
            "track",
            lib2_track_id,
            True,
            PROVENANCE_WISHLIST,
            profile_id=profile_id or 1,
        )

        # Wishlist payloads often contain the Spotify release's total_tracks but
        # not the titles for the other release tracks. For albums that only exist
        # because of wishlist rows, keep the expected size to the known wishlist
        # rows so the UI does not invent unnamed "Track N - missing" placeholders.
        # Discography rows are excluded: their expected_track_count came from the
        # provider catalog, and clamping it here would make the later tracklist
        # materialization truncate the release to the wishlisted tracks.
        albums_to_recount.add(int(album_id))

        cursor.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (lib2_track_id,))
        linked_artists: Set[int] = set()
        for pos, artist_payload in enumerate(artists_payload or [primary_payload]):
            name = _artist_name_from_payload(artist_payload)
            if not name:
                continue
            spotify_id = _artist_spotify_from_payload(artist_payload)
            aid = resolver.get_or_create_by_name(name, spotify_id=spotify_id)
            if aid in linked_artists:
                continue
            linked_artists.add(aid)
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

    for album_id in albums_to_recount:
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
               AND COALESCE(origin, 'library') <> 'discography'
               AND NOT EXISTS (
                   SELECT 1
                     FROM lib2_tracks t
                     JOIN lib2_track_files tf ON tf.track_id = t.id
                    WHERE t.album_id = ?
               )
            """,
            (album_id, album_id, album_id, album_id),
        )

    return created_or_updated


def _table_exists(cursor, name: str) -> bool:
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cursor.fetchone() is not None


def _profile_filter(cursor, table: str, profile_id: Optional[int]) -> Tuple[str, tuple]:
    """WHERE fragment that enforces Library-v2's admin-only legacy scope.

    Tables predating the ``profile_id`` column are necessarily treated as the
    single admin library. On profile-aware tables, ``None`` means admin profile
    1, never all profiles.
    """
    if "profile_id" not in _existing_columns(cursor, table):
        return "", ()
    from core.library2 import ADMIN_PROFILE_ID
    effective_profile_id = (ADMIN_PROFILE_ID if profile_id is None
                            else int(profile_id))
    if effective_profile_id != ADMIN_PROFILE_ID:
        raise ValueError(
            f"Library v2 legacy scope is admin-only: got profile_id={profile_id}, "
            f"expected {ADMIN_PROFILE_ID}")
    return "profile_id = ?", (effective_profile_id,)


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

    ``profile_id`` is restricted to the admin watchlist/wishlist so Library v2
    cannot leak another profile's wanted state into its global flags. ``None``
    means admin profile 1.
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
