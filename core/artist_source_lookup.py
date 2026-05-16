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

logger = logging.getLogger("artist_source_lookup")


SOURCE_ONLY_ARTIST_SOURCES = frozenset({
    "spotify", "itunes", "deezer", "discogs", "hydrabase", "musicbrainz", "amazon",
})


SOURCE_ID_FIELD = {
    "spotify": "spotify_artist_id",
    "itunes": "itunes_artist_id",
    "deezer": "deezer_id",
    "discogs": "discogs_id",
    "hydrabase": "soul_id",
    "musicbrainz": "musicbrainz_id",
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
         library record with the right external ID is a hit).
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
            cursor.execute(
                f"SELECT id, name FROM artists WHERE {column} = ? LIMIT 1",
                (str(source_artist_id),),
            )
            row = cursor.fetchone()
            if row:
                return row[0]

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
