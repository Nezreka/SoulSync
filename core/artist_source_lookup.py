"""Source-artist → library lookup helpers.

Extracted from `web_server.py` so the logic can be imported and unit-tested
without booting the Flask app, Spotify client, Soulseek connection, etc.

Two concepts live here:

  * ``SOURCE_ID_FIELD`` — the field name each source's external service ID
    (Spotify artist ID, Deezer artist ID, …) travels under. It names the
    sources eligible for a library upgrade, and it is the key the source-only
    artist payload stamps its id under so the right service badge renders.

  * ``find_library_artist_for_source`` — given a source-aware click (e.g.
    ``deezer:525046``), try to locate a matching library artist. First by
    direct match against wherever the catalogue keeps that source's id, then
    by folded name match scoped to the active media server.
"""

from __future__ import annotations

import logging
from typing import Optional

from core.source_ids import id_column as _artist_id_column

logger = logging.getLogger("artist_source_lookup")


SOURCE_ONLY_ARTIST_SOURCES = frozenset({
    "spotify", "itunes", "deezer", "discogs", "hydrabase", "musicbrainz", "amazon", "jiosaavn", "bandcamp",
})


# The per-source column on the ``artists`` table, derived from the canonical
# source-ID registry (the single source of truth). Values are unchanged from the
# previous hardcoded map — this just stops duplicating that knowledge here.
SOURCE_ID_FIELD = {
    source: _artist_id_column(source, "artist")
    for source in SOURCE_ONLY_ARTIST_SOURCES
    if _artist_id_column(source, "artist")
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
    from core.library2.provider_ids import provider_id_sql

    if source not in SOURCE_ID_FIELD:
        return None
    id_expression = provider_id_sql(source)
    if not id_expression:
        return None

    try:
        with database._get_connection() as conn:
            cursor = conn.cursor()
            # LIMIT 2 so we can tell a unique match from an ambiguous one.
            cursor.execute(
                f"SELECT id FROM lib2_artists WHERE {id_expression} = ? LIMIT 2",
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
                from core.library2.importer import normalize_name

                # `name_key` is the stored casefold. SQLite's LOWER() only
                # folds A-Z, so a searched "björk" never met a stored "Björk".
                cursor.execute(
                    "SELECT id FROM lib2_artists "
                    "WHERE name_key = ? AND (server_source = ? OR EXISTS ("
                    "SELECT 1 FROM lib2_media_server_mappings m "
                    "WHERE m.entity_type='artist' AND m.entity_id=lib2_artists.id "
                    "AND m.server_source=?)) LIMIT 1",
                    (normalize_name(artist_name), active_server, active_server),
                )
                row = cursor.fetchone()
                if row:
                    return row[0]
    except Exception as e:
        logger.debug(
            f"Library upgrade lookup failed for {source}:{source_artist_id}: {e}"
        )
    return None

def sources_resolvable_in_library(database, id_map):
    """Which of ``id_map``'s sources resolve to a library artist by ID alone.

    ``id_map`` is ``{source: source_artist_id}``. A source counts only when
    its id matches EXACTLY ONE library artist — the same bar the artist-
    detail route applies before upgrading to the library view.

    Deliberately no name matching: this feeds the watchlist panel's
    discography link, whose navigation carries source+id and nothing else,
    and the route's own name-retry needs the source's CLIENT to resolve a
    name — exactly what a switched-off provider doesn't have. Mirroring
    anything more optimistic than the id-column step would promise a page
    the route can't deliver.
    """
    out = []
    for source, source_id in (id_map or {}).items():
        if not source_id:
            continue
        if find_library_artist_for_source(database, source, str(source_id)) is not None:
            out.append(source)
    return out
