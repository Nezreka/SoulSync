"""Source-artist → library lookup helpers.

Extracted from `web_server.py` so the logic can be imported and unit-tested
without booting the Flask app, Spotify client, Soulseek connection, etc.

Two concepts live here:

  * ``SOURCE_ID_FIELD`` — the per-source column on the ``artists`` table that
    stores the external service ID (Spotify track ID, Deezer artist ID, …).
    This map is what ties a result clicked in the source-aware Search results
    back to a library record so we can serve the richer library view.

  * ``find_library_artist_for_source`` — given a source-aware click (e.g.
    ``deezer:525046``), try to locate a matching library artist. First by
    direct column match against the source's ID column, then by case-
    insensitive name match scoped to the active media server.
"""

from __future__ import annotations

import logging
from typing import Optional

from core.source_ids import id_column as _artist_id_column

logger = logging.getLogger("artist_source_lookup")


SOURCE_ONLY_ARTIST_SOURCES = frozenset({
    "spotify", "itunes", "deezer", "discogs", "hydrabase", "musicbrainz", "amazon",
})


# The per-source column on the ``artists`` table, derived from the canonical
# source-ID registry (the single source of truth). Values are unchanged from the
# previous hardcoded map — this just stops duplicating that knowledge here.
SOURCE_ID_FIELD = {
    source: _artist_id_column(source, "artist")
    for source in (
        "spotify", "itunes", "deezer", "discogs", "hydrabase", "musicbrainz", "amazon",
    )
}


def find_library_artist_for_source(
    database,
    source: str,
    source_artist_id: str,
    artist_name: Optional[str] = None,
    active_server: Optional[str] = None,
) -> Optional[str]:
    """Return the library PK of an artist matching the source-aware click.

    Lookup order:
      1. Direct match on the source-specific ID column (server-agnostic — any
         library record with the right external ID is a hit). If that id is
         stamped on MORE than one library artist, the mapping is corrupt /
         ambiguous (e.g. an enrichment bug wrote one Deezer id onto several
         artists) — we refuse to guess and fall through, so the caller can
         show the source artist directly instead of an arbitrary wrong one.
      2. Case-insensitive name match within ``active_server`` (defaults to the
         active media server when not provided), so we don't jump the user
         across server contexts on a name collision.

    Returns ``None`` on miss or on any database error.
    """
    column = SOURCE_ID_FIELD.get(source)
    if not column:
        return None

    try:
        with database._get_connection() as conn:
            cursor = conn.cursor()
            # LIMIT 2 so we can tell a unique match from an ambiguous one.
            cursor.execute(
                f"SELECT id FROM artists WHERE {column} = ? LIMIT 2",
                (str(source_artist_id),),
            )
            rows = cursor.fetchall()
            if len(rows) == 1:
                return rows[0][0]
            if len(rows) > 1:
                # Same source id on multiple artists — corrupt mapping. Don't
                # upgrade on the id; fall through to the name match (and, if
                # that misses, let the caller render the source artist).
                logger.warning(
                    f"Source id {source}:{source_artist_id} maps to "
                    f"{len(rows)}+ library artists — ambiguous, skipping "
                    f"id-based library upgrade"
                )

            if artist_name and active_server:
                cursor.execute(
                    "SELECT id FROM artists "
                    "WHERE LOWER(name) = LOWER(?) AND server_source = ? LIMIT 1",
                    (artist_name, active_server),
                )
                row = cursor.fetchone()
                if row:
                    return row[0]
    except Exception as e:
        logger.debug(
            f"Library upgrade lookup failed for {source}:{source_artist_id}: {e}"
        )
    return None
