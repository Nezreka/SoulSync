"""The Quality Profile a download started from an artist's discography uses.

#2022: Download Discography queued every release on the app-wide default
profile, ignoring the profile the user set on the artist. There are two places
an artist carries one, and both are honoured:

- the Library v2 catalogue (track > album > artist, admin wide), set from the
  library artist page, and
- the watchlist row (per profile), set under the artist's watchlist rules.
  It is the profile the watchlist scan already queues this artist's new
  releases on.

Order: an explicit catalogue track or album choice (it names exactly this
release), then the watchlist row of the person downloading, then the
catalogue's explicit artist choice, then ``None`` (the app default). A profile
id that no longer exists is skipped, never passed on: ``add_to_wishlist``
rejects an unknown id, which would drop the track instead of downgrading it.

Never raises. A lookup that fails answers ``None``, which is where these
downloads already were.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("quality.artist_profile")

_LIBRARY_SOURCES = ("library", "lib2")


def _norm_source(source: Any) -> Optional[str]:
    from core.watchlist_sources import normalize_source

    text = str(source or "").strip().lower()
    if text in _LIBRARY_SOURCES:
        return text
    return normalize_source(text)


def _columns(conn, table: str) -> set:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:  # noqa: BLE001 - a missing table is an empty one
        return set()


class ArtistQualityProfile:
    """Resolve once per artist, then answer per release and per track."""

    def __init__(self, db, *, artist_id: Any, artist_name: str, source: Any = None,
                 profile_id: int = 1) -> None:
        self._db = db
        self._artist_id = str(artist_id or "").strip()
        self._name = str(artist_name or "").strip()
        self._source = _norm_source(source)
        self._profile_id = int(profile_id or 1)
        self._known: set = set()
        self._watchlist_profile: Optional[int] = None
        self._lib2_artists: List[int] = []
        self._album_cache: Dict[tuple, Optional[int]] = {}
        self._track_rows: Dict[int, list] = {}
        try:
            with closing(db._get_connection()) as conn:
                self._known = {int(r[0]) for r in conn.execute("SELECT id FROM quality_profiles")}
                if self._source in _LIBRARY_SOURCES:
                    # a library row id, not a provider's: the catalogue artist
                    # first, then its provider ids find the watchlist row
                    self._lib2_artists = self._lib2_by_row_id(conn)
                    self._load_watchlist(conn, self._lib2_provider_ids(conn))
                else:
                    hint = ({self._source: self._artist_id}
                            if self._source and self._artist_id else {})
                    row_ids = self._load_watchlist(conn, {}, hint)
                    # the page's id is not always in the page source's
                    # namespace (the modal sends a spotify id over a deezer
                    # discography), so the watchlist row's own ids go first
                    guesses = list(row_ids.items())
                    if self._artist_id:
                        if self._source:
                            guesses.append((self._source, self._artist_id))
                        guesses.append(("spotify", self._artist_id))
                    self._lib2_artists = self._find_lib2_artists(conn, guesses, row_ids)
        except Exception as exc:  # noqa: BLE001 - the default is always safe
            logger.debug("artist quality profile lookup failed for %r: %s", self._name, exc)

    # -- watchlist -----------------------------------------------------------

    def _load_watchlist(self, conn, trusted: Dict[str, str],
                        hint: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """The active profile's watchlist row for this artist. Sets its profile
        and returns the provider ids it carries (they find the catalogue artist).

        ``trusted`` are namespaced ids (from the catalogue row). Without them
        the page's bare id is matched against every id column, the way the
        watchlist check endpoint matches it; the name stands in only when it
        names one watched artist and contradicts no id we were given (``hint``
        is the page's id in the page's namespace: good enough to refuse a
        namesake, not to match one)."""
        from core.library2.importer import normalize_name
        from core.library2.monitor_sync import _artist_identity_matches
        from core.watchlist_sources import SOURCE_COLUMNS

        cols = _columns(conn, "watchlist_artists")
        if "quality_profile_id" not in cols:
            return {}
        id_cols = {src: col for src, col in SOURCE_COLUMNS.items() if col in cols}
        rows = conn.execute(
            f"SELECT artist_name, quality_profile_id{''.join(', ' + c for c in id_cols.values())} "  # noqa: S608 - columns from a fixed map
            "FROM watchlist_artists WHERE profile_id=?",
            (self._profile_id,),
        ).fetchall()

        def row_ids(row) -> Dict[str, str]:
            return {src: str(row[col]).strip() for src, col in id_cols.items()
                    if row[col] is not None and str(row[col]).strip()}

        if trusted:
            matched = [row for row in rows
                       if any(row_ids(row).get(src) == value for src, value in trusted.items())]
        elif self._source in _LIBRARY_SOURCES:
            matched = []  # a library row id is nobody's provider id
        else:
            matched = [row for row in rows
                       if self._artist_id and self._artist_id in row_ids(row).values()]
        if not matched and normalize_name(self._name):
            named = [row for row in rows
                     if normalize_name(row["artist_name"]) == normalize_name(self._name)]
            if len(named) == 1 and _artist_identity_matches(
                    self._name, {**(hint or {}), **trusted}, named[0]["artist_name"],
                    row_ids(named[0])):
                matched = named
        wanted = {int(row["quality_profile_id"]) for row in matched
                  if row["quality_profile_id"] is not None}
        # two rows asking for different profiles: no basis to pick one, the
        # same rule the library import uses (_adopt_watchlist_profile)
        if len(wanted) == 1:
            self._watchlist_profile = self._usable(wanted.pop())
        return row_ids(matched[0]) if len(matched) == 1 else {}

    # -- library v2 ----------------------------------------------------------

    def _lib2_by_row_id(self, conn) -> List[int]:
        from core.library2.artist_aliases import resolve_alias_group

        aid = self._artist_id.removeprefix("lib2:")
        if not aid.isdigit() or not _columns(conn, "lib2_artists"):
            return []
        if conn.execute("SELECT 1 FROM lib2_artists WHERE id=?", (int(aid),)).fetchone() is None:
            return []
        return resolve_alias_group(conn, int(aid))

    def _find_lib2_artists(self, conn, guesses, row_ids: Dict[str, str]) -> List[int]:
        """The catalogue artist (and its aliases) for these provider ids."""
        if not _columns(conn, "lib2_artists"):
            return []
        from core.library2.artist_aliases import resolve_alias_group
        from core.library2.provider_ids import external_id_sql, source_ids_from_values

        found: List[int] = []
        for source, value in guesses:
            source = _norm_source(source)
            if not source or source in _LIBRARY_SOURCES:
                continue
            column = {"spotify": "spotify_id", "musicbrainz": "musicbrainz_id"}.get(source)
            if column is None:
                if not source.isalnum():
                    continue
                column = external_id_sql("external_ids", source)
            found = [int(r[0]) for r in conn.execute(
                f"SELECT id FROM lib2_artists WHERE {column} = ?",  # noqa: S608 - column from a fixed map
                (value,))]
            if found:
                break
        if not found and self._name:
            # a bare name only when it names one artist, and that artist holds
            # no different id in a namespace the watchlist row knows
            from core.library2.monitor_sync import _artist_identity_matches

            rows = conn.execute(
                "SELECT id, name, spotify_id, musicbrainz_id, external_ids FROM lib2_artists "
                "WHERE LOWER(name) = LOWER(?)", (self._name,)).fetchall()
            if len(rows) == 1 and _artist_identity_matches(
                    rows[0]["name"], source_ids_from_values(
                        spotify_id=rows[0]["spotify_id"],
                        musicbrainz_id=rows[0]["musicbrainz_id"],
                        external_ids=rows[0]["external_ids"]),
                    self._name, row_ids):
                found = [int(rows[0]["id"])]
        group: List[int] = []
        for artist in found:
            for member in resolve_alias_group(conn, artist):
                if member not in group:
                    group.append(member)
        return group

    def _lib2_provider_ids(self, conn) -> Dict[str, str]:
        from core.library2.provider_ids import source_ids_from_values

        if not self._lib2_artists:
            return {}
        row = conn.execute(
            "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_artists WHERE id=?",
            (self._lib2_artists[0],)).fetchone()
        if row is None:
            return {}
        ids = source_ids_from_values(spotify_id=row["spotify_id"],
                                     musicbrainz_id=row["musicbrainz_id"],
                                     external_ids=row["external_ids"])
        return {src: value for src, value in ids.items() if _norm_source(src)}

    def _match_album(self, conn, album_ids: Iterable[Any]) -> Optional[int]:
        from core.library2.provider_ids import source_ids_from_values

        wanted = {str(a).strip() for a in album_ids if str(a or "").strip()}
        if not wanted or not self._lib2_artists:
            return None
        marks = ",".join("?" for _ in self._lib2_artists)
        hits = []
        for row in conn.execute(
                f"SELECT id, spotify_id, musicbrainz_id, external_ids FROM lib2_albums "
                f"WHERE primary_artist_id IN ({marks})", self._lib2_artists):
            ids = source_ids_from_values(spotify_id=row["spotify_id"],
                                         musicbrainz_id=row["musicbrainz_id"],
                                         external_ids=row["external_ids"])
            if wanted & set(ids.values()):
                hits.append(int(row["id"]))
        return hits[0] if len(hits) == 1 else None

    def _match_track(self, conn, album_id: int, track: Dict[str, Any]) -> Optional[int]:
        from core.library2.provider_ids import source_ids_from_values

        wanted = {str(track.get("id") or "").strip()}
        isrc = (track.get("external_ids") or {}).get("isrc") if isinstance(
            track.get("external_ids"), dict) else None
        isrc = str(isrc or track.get("isrc") or "").strip()
        wanted.discard("")
        if not wanted and not isrc:
            return None
        if album_id not in self._track_rows:
            self._track_rows[album_id] = conn.execute(
                "SELECT id, spotify_id, isrc, external_ids FROM lib2_tracks WHERE album_id=?",
                (album_id,)).fetchall()
        hits = []
        for row in self._track_rows[album_id]:
            ids = source_ids_from_values(spotify_id=row["spotify_id"],
                                         external_ids=row["external_ids"])
            if wanted & set(ids.values()) or (isrc and str(row["isrc"] or "").strip() == isrc):
                hits.append(int(row["id"]))
        return hits[0] if len(hits) == 1 else None

    # -- answers -------------------------------------------------------------

    def _usable(self, profile_id: Any) -> Optional[int]:
        try:
            value = int(profile_id)
        except (TypeError, ValueError):
            return None
        return value if value in self._known else None

    def _catalogue(self, conn, *, album_ids=(), track=None) -> Dict[str, Any]:
        """The catalogue's own answer: the deepest entity matched."""
        from core.library2.profile_lookup import effective_quality_profile

        if not self._lib2_artists:
            return {"source": "global"}
        key = tuple(sorted({str(a) for a in album_ids if a}))
        if key not in self._album_cache:
            self._album_cache[key] = self._match_album(conn, album_ids)
        album = self._album_cache[key]
        if album is not None and track:
            track_id = self._match_track(conn, album, track)
            if track_id is not None:
                return effective_quality_profile(conn, "tracks", track_id)
        if album is not None:
            return effective_quality_profile(conn, "albums", album)
        return effective_quality_profile(conn, "artists", self._lib2_artists[0])

    def for_release(self, album_ids: Iterable[Any], tracks: Iterable[Dict[str, Any]]
                    ) -> List[Optional[int]]:
        """One profile per track of a release, in order. One connection."""
        album_ids = list(album_ids)
        tracks = list(tracks)
        catalogue: List[Dict[str, Any]] = [{"source": "global"}] * len(tracks)
        if self._lib2_artists:
            try:
                with closing(self._db._get_connection()) as conn:
                    catalogue = [self._catalogue(conn, album_ids=album_ids, track=track)
                                 for track in tracks]
            except Exception as exc:  # noqa: BLE001 - fall through to the artist level
                logger.debug("catalogue profile lookup failed for %r: %s", self._name, exc)
        return [self._pick(answer) for answer in catalogue]

    def profile_for(self, *, album_ids: Iterable[Any] = (), track: Optional[Dict[str, Any]] = None
                    ) -> Optional[int]:
        """The profile for one release (and one of its tracks, when given)."""
        catalogue: Dict[str, Any] = {"source": "global"}
        if self._lib2_artists:
            try:
                with closing(self._db._get_connection()) as conn:
                    catalogue = self._catalogue(conn, album_ids=list(album_ids), track=track)
            except Exception as exc:  # noqa: BLE001 - fall through to the artist level
                logger.debug("catalogue profile lookup failed for %r: %s", self._name, exc)
        return self._pick(catalogue)

    def _pick(self, catalogue: Dict[str, Any]) -> Optional[int]:
        if catalogue.get("source") in ("track", "album"):
            chosen = self._usable(catalogue.get("id"))
            if chosen is not None:
                return chosen
        if self._watchlist_profile is not None:
            return self._watchlist_profile
        if catalogue.get("source") == "artist":
            return self._usable(catalogue.get("id"))
        return None


__all__ = ["ArtistQualityProfile"]
