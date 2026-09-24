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
    artist_id: str,
    album_name: str,
    album_source_col: Optional[str] = None,
    album_source_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve the existing ``soulsync`` album row a track should join, or None
    (caller inserts a new row).

    Match precedence:
      1. ``album_source_col == album_source_id`` — CANONICAL grouping: the row
         already carrying THIS release's id, so a differently-named import of
         the same release unifies instead of splitting. Only when the column is
         allow-listed and the id is non-empty.
      2. ``name_key_id`` — the prior stable-name-hash id.
      3. ``(title, artist_id)`` — the legacy name match.

    2 and 3 skip a row that already carries a DIFFERENT release id in that
    column. two releases can share a title and artist and differ only by
    musicbrainz's disambiguation (#1299); matching them by name merged both into
    one album with every shared song twice. a row with no id yet still matches,
    so nothing that grouped before stops grouping now.
    """
    source_col = album_source_col if album_source_col in ALLOWED_ALBUM_SOURCE_COLS else None
    source_id = (album_source_id or "").strip() if source_col else ""

    if source_id:
        try:
            cursor.execute(
                f"SELECT id FROM albums WHERE {source_col} = ? "
                "AND server_source = 'soulsync' LIMIT 1",
                (source_id,),
            )
            row = cursor.fetchone()
            if row:
                return row[0]
        except Exception as exc:
            # That source has no dedicated album column on this DB (e.g. Deezer
            # doesn't split per-entity id columns) — fall through to the name
            # match rather than break the import. Mirrors the guarded source-id
            # UPDATE the caller already does on insert.
            logger.debug("album source-id lookup skipped (%s): %s", source_col, exc)
            source_id = ""

    for sql, params in (
        ("SELECT id{extra} FROM albums WHERE id = ? AND server_source = 'soulsync'",
         (name_key_id,)),
        ("SELECT id{extra} FROM albums WHERE title COLLATE NOCASE = ? AND artist_id = ? "
         "AND server_source = 'soulsync'",
         (album_name, artist_id)),
    ):
        rows = None
        if source_id:
            try:
                cursor.execute(sql.format(extra=f", {source_col}"), params)
                rows = cursor.fetchall()
            except Exception as exc:
                logger.debug("album release check skipped (%s): %s", source_col, exc)
                source_id = ""
        if rows is None:
            cursor.execute(sql.format(extra=""), params)
            rows = cursor.fetchall()
        for row in rows:
            if source_id:
                stored = str(row[1] or "").strip()
                if stored and stored.casefold() != source_id.casefold():
                    continue  # another release that happens to share the name
            return row[0]
    return None
