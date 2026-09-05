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
) -> Optional[int]:
    """Resolve the catalogue album row a track should join, or None.

    Match precedence:
      1. ``name_key_id`` — the stable name hash the import mints, kept as the
         row's ``server_id`` (a re-import with the identical name hits its own
         row).
      2. the release's own source id — CANONICAL grouping, so a differently
         named import of the same release unifies instead of splitting. v2
         promotes Spotify and MusicBrainz to columns and keeps the rest in
         ``external_ids``.
      3. ``(title, artist)`` — the name match, kept so nothing that grouped
         before stops grouping now.
    """
    row = cursor.execute(
        "SELECT id FROM lib2_albums WHERE server_source = 'soulsync' AND server_id = ?",
        (str(name_key_id),),
    ).fetchone()
    if row:
        return int(row[0])

    provider = (source or '').strip().lower()
    if album_source_id and provider:
        if provider in ('spotify', 'musicbrainz'):
            column = 'spotify_id' if provider == 'spotify' else 'musicbrainz_id'
            where = f"{column} = ?"
        else:
            where = f"json_extract(external_ids, '$.{provider}') = ?"
        try:
            row = cursor.execute(
                f"SELECT id FROM lib2_albums WHERE {where} LIMIT 1",
                (album_source_id,),
            ).fetchone()
            if row:
                return int(row[0])
        except Exception as exc:
            logger.debug("album source-id lookup skipped (%s): %s", provider, exc)

    row = cursor.execute(
        "SELECT id FROM lib2_albums WHERE title COLLATE NOCASE = ? "
        "  AND primary_artist_id = ? LIMIT 1",
        (album_name, artist_id),
    ).fetchone()
    return int(row[0]) if row else None
