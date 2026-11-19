"""Canonical album grouping for the SoulSync standalone import.

SoulSync grouped imported tracks into albums by the album NAME string
(``_stable_soulsync_id("artist::album_name")``). That splits one release into
several album rows whenever the name string drifts between imports (case,
punctuation, ``(Deluxe Edition)`` suffixes, source-A-vs-B spelling), and every
downstream tool (Library Re-tag, Cover-Art Filler) then dresses each split row
in its own cover — so songs that belong to one album end up with different art
(Sokhi).

This module is the pure, seam-testable heart of "group by canonical id, not
name": when an imported track carries a metadata-source RELEASE id, prefer
matching an existing album row by that id over the fragile name string, so the
SAME release always lands in ONE album row regardless of how its name was typed.

Scope (deliberate): this unifies differently-named imports of the SAME release.
It does NOT merge a track that genuinely matched a SINGLE release (a different
release id) into its parent album — that needs single->album resolution upstream
and is a separate change. New imports only; existing rows are left untouched.

Pure SQL-over-a-cursor; no app singletons, so it tests against an in-memory DB.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("imports.album_grouping")

# Album source-id columns this grouping may key on. An allowlist (not arbitrary
# interpolation) — the column name IS spliced into SQL, so it must be a known,
# trusted identifier. Mirrors get_library_source_id_columns()' 'album' values.
ALLOWED_ALBUM_SOURCE_COLS = frozenset({
    "spotify_album_id",
    "itunes_album_id",
    "deezer_id",
    "soul_id",
    "discogs_id",
    "musicbrainz_release_id",
})


def find_existing_soulsync_album_id(
    cursor: Any,
    *,
    name_key_id: str,
    artist_id: Any,
    album_name: str,
    album_source_col: Optional[str] = None,
    album_source_id: Optional[str] = None,
    source: Optional[str] = None,
    release_id: Optional[str] = None,
    release_year: Any = None,
    file_folder: Optional[str] = None,
) -> Optional[int]:
    """Resolve the catalogue album row a track should join, or None.

    Match precedence:
      0. ``release_id`` — the MusicBrainz release this import IS. The sharpest
         edition identity there is, and the only one a MusicBrainz import has.
      1. ``name_key_id`` — the stable name hash the import mints, kept as the
         row's ``server_id`` (a re-import with the identical name hits its own
         row).
      2. the release's own source id — CANONICAL grouping, so a differently
         named import of the same release unifies instead of splitting. v2
         promotes Spotify and MusicBrainz to columns and keeps the rest in
         ``external_ids``.
      3. ``(title, artist)`` — the name match, kept so nothing that grouped
         before stops grouping now.

    1 to 3 skip a row that already carries a DIFFERENT release id. Two
    releases can share a title and an artist and differ only by MusicBrainz's
    disambiguation (#1299); matching them by name merged both into one album
    with every shared song twice. A row with no release id yet still matches.

    ``release_year`` / ``file_folder``: for an import that carries no release
    or provider id at all, the year and folder of the file. A same-named row
    whose files all sit in other folders under another year is another
    release (#2110), the rule the scan and refresh writers follow.
    """
    release = (release_id or "").strip().casefold()
    provider = (source or '').strip().lower()
    own_id = str(album_source_id or "").strip()

    def _other_provider_release(album_id) -> bool:
        # weezer's three self-titled albums share the name hash; the
        # provider's own album id and the year tell them apart (#2110). the
        # id alone does not: a provider lists one album under several ids
        # (clean and explicit, one per region), and those are one album here
        if not own_id or not provider or provider == 'musicbrainz':
            return False
        held = cursor.execute(
            "SELECT spotify_id, external_ids, release_date, year FROM lib2_albums WHERE id = ?",
            (album_id,)).fetchone()
        if not held:
            return False
        from core.library2.release_split import tag_year
        wanted_year, held_year = tag_year(release_year), tag_year(held[2]) or tag_year(held[3])
        if not wanted_year or not held_year or wanted_year == held_year:
            return False
        if provider == 'spotify':
            stored = held[0]
        else:
            try:
                stored = (json.loads(held[1] or "{}") or {}).get(provider)
            except (TypeError, ValueError, AttributeError):
                stored = None
        stored = str(stored or "").strip()
        return bool(stored) and stored != own_id

    def _usable(row) -> bool:
        if not row:
            return False
        stored = str(row[1] or "").strip().casefold()
        if release and stored and stored != release:
            return False
        if _other_provider_release(row[0]):
            return False
        if not release and not own_id and release_year and file_folder:
            from core.library2.release_split import is_other_release_by_year
            if is_other_release_by_year(cursor, row[0], release_year, file_folder):
                return False
        return True

    if release:
        row = cursor.execute(
            "SELECT id FROM lib2_albums WHERE LOWER(musicbrainz_id) = ? LIMIT 1",
            (release,),
        ).fetchone()
        if row:
            return int(row[0])

    row = cursor.execute(
        "SELECT id, musicbrainz_id FROM lib2_albums"
        " WHERE server_source = 'soulsync' AND server_id = ?",
        (str(name_key_id),),
    ).fetchone()
    if _usable(row):
        return int(row[0])

    where, params = None, (album_source_id,)
    if album_source_id and provider:
        if provider in ('spotify', 'musicbrainz'):
            # musicbrainz through LOWER(): lib2_albums only indexes that form
            where = ("spotify_id = ?" if provider == 'spotify'
                     else "LOWER(musicbrainz_id) = LOWER(?)")
        else:
            # the indexed form (idx_lib2_albums_ext_*): the bare json_extract
            # parsed all 72k albums on every miss (~190ms an import). only a
            # provider with an index: any other name walked every album per
            # imported track, inside the import's write (review 5 W3-TD-4)
            from core.library2.provider_ids import ALBUM_EXTERNAL_ID_INDEXED, external_id_sql
            if provider in ALBUM_EXTERNAL_ID_INDEXED:
                where = f"{external_id_sql('external_ids', provider)} = ?"
    if where:
        try:
            row = cursor.execute(
                f"SELECT id, musicbrainz_id FROM lib2_albums WHERE {where} LIMIT 1",
                params,
            ).fetchone()
            if _usable(row):
                return int(row[0])
        except Exception as exc:
            logger.debug("album source-id lookup skipped (%s): %s", provider, exc)

    for row in cursor.execute(
        "SELECT id, musicbrainz_id FROM lib2_albums WHERE title COLLATE NOCASE = ? "
        "  AND primary_artist_id = ? ORDER BY id",
        (album_name, artist_id),
    ).fetchall():
        if _usable(row):
            return int(row[0])
    return None
