"""Read-side helpers for browsing the items an enrichment source hasn't matched.

The dashboard "Manage Enrichment Workers" modal lists, per source, the
artists / albums / tracks whose ``<service>_match_status`` is ``'not_found'``
(or still pending = ``NULL``) so the user can manually match them. Every
enrichment source writes a uniform ``<service>_match_status`` column, so one
parametric query serves all 11 workers.

This module owns the column mapping and SQL construction. ``service`` and
``entity_type`` are whitelisted against :data:`SERVICE_ENTITY_SUPPORT` and the
entity table map before any column name is interpolated — user-supplied values
(the search term, pagination) are always bound parameters, never interpolated.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

# Which entity types each enrichment source covers. Mirrors the authoritative
# ``_SERVICE_ID_COLUMNS`` map in web_server.py (used by manual-match), kept here
# so the unmatched browser is self-contained and unit-testable. Singular keys
# ('artist'/'album'/'track') match the manual-match entity_type vocabulary.
SERVICE_ENTITY_SUPPORT = {
    'spotify': ('artist', 'album', 'track'),
    'musicbrainz': ('artist', 'album', 'track'),
    'deezer': ('artist', 'album', 'track'),
    'audiodb': ('artist', 'album', 'track'),
    'discogs': ('artist', 'album'),          # no track-level id column
    'itunes': ('artist', 'album', 'track'),
    'lastfm': ('artist', 'album', 'track'),
    'genius': ('artist', 'track'),           # no album-level id column
    'tidal': ('artist', 'album', 'track'),
    'qobuz': ('artist', 'album', 'track'),
    'amazon': ('artist', 'album', 'track'),
}

# entity_type -> table / display-name column / image expression / optional join
# / parent-context expression (the artist an album belongs to; the album a
# track belongs to) so the UI can disambiguate same-named items.
# tracks carry no artwork column of their own, so we borrow the parent album's.
_ENTITY_TABLE = {
    'artist': {
        'table': 'artists', 'name': 'name',
        'image': 'artists.thumb_url', 'join': '', 'parent': None,
    },
    'album': {
        'table': 'albums', 'name': 'title',
        'image': 'albums.thumb_url',
        'join': 'LEFT JOIN artists par ON albums.artist_id = par.id',
        'parent': 'par.name',
    },
    'track': {
        'table': 'tracks', 'name': 'title',
        'image': 'al.thumb_url',
        'join': 'LEFT JOIN albums al ON tracks.album_id = al.id',
        'parent': 'al.title',
    },
}

# 'unmatched' = not yet matched at all (pending OR explicitly not_found).
VALID_STATUSES = ('not_found', 'pending', 'unmatched')

# Hard cap so a malicious/buggy caller can't ask for the whole library at once.
MAX_LIMIT = 200


class UnmatchedQueryError(ValueError):
    """Raised for an unknown service / unsupported entity type / bad status."""


def supported_entity_types(service: str) -> Tuple[str, ...]:
    """Return the entity types a source enriches, or () for an unknown source."""
    return SERVICE_ENTITY_SUPPORT.get(service, ())


def match_status_column(service: str) -> str:
    return f"{service}_match_status"


def last_attempted_column(service: str) -> str:
    return f"{service}_last_attempted"


def _validate(service: str, entity_type: str) -> None:
    support = SERVICE_ENTITY_SUPPORT.get(service)
    if support is None:
        raise UnmatchedQueryError(f"Unknown enrichment service: {service!r}")
    if entity_type not in support:
        raise UnmatchedQueryError(
            f"{service} does not enrich {entity_type!r} entities"
        )
    if entity_type not in _ENTITY_TABLE:  # defensive — support map drift
        raise UnmatchedQueryError(f"No table mapping for entity type {entity_type!r}")


def _status_predicate(service: str, status: str, qualifier: str) -> str:
    """SQL predicate selecting rows in the requested match state.

    ``qualifier`` (the table name/alias) is always prefixed so the predicate is
    unambiguous even when the query joins a second table that also carries a
    ``<service>_match_status`` column (tracks LEFT JOIN albums).
    """
    col = f"{qualifier}.{match_status_column(service)}"
    if status == 'not_found':
        return f"{col} = 'not_found'"
    if status == 'pending':
        return f"{col} IS NULL"
    # 'unmatched'
    return f"({col} IS NULL OR {col} = 'not_found')"


def build_unmatched_query(
    service: str,
    entity_type: str,
    status: str = 'not_found',
    query: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[str, List]:
    """Build the paginated SELECT for one (service, entity_type, status) view.

    Returns ``(sql, params)``. Selected columns: id, name, image_url, status,
    last_attempted.
    """
    _validate(service, entity_type)
    if status not in VALID_STATUSES:
        raise UnmatchedQueryError(f"Invalid status: {status!r}")

    meta = _ENTITY_TABLE[entity_type]
    table, name_col, image_expr, join = (
        meta['table'], meta['name'], meta['image'], meta['join'],
    )
    ms = match_status_column(service)
    la = last_attempted_column(service)

    where = [_status_predicate(service, status, table)]
    params: List = []
    if query:
        where.append(f"{table}.{name_col} LIKE ?")
        params.append(f"%{query}%")

    parent_expr = meta.get('parent')
    parent_select = f"{parent_expr} AS parent" if parent_expr else "NULL AS parent"
    sql = (
        f"SELECT {table}.id AS id, {table}.{name_col} AS name, "
        f"{image_expr} AS image_url, {parent_select}, {table}.{ms} AS status, "
        f"{table}.{la} AS last_attempted "
        f"FROM {table} {join} "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {table}.{name_col} COLLATE NOCASE "
        f"LIMIT ? OFFSET ?"
    ).replace('  ', ' ')

    params.append(_clamp_limit(limit))
    params.append(max(int(offset or 0), 0))
    return sql, params


def build_count_query(
    service: str,
    entity_type: str,
    status: str = 'not_found',
    query: Optional[str] = None,
) -> Tuple[str, List]:
    """Build the COUNT(*) matching :func:`build_unmatched_query`'s filters."""
    _validate(service, entity_type)
    if status not in VALID_STATUSES:
        raise UnmatchedQueryError(f"Invalid status: {status!r}")

    meta = _ENTITY_TABLE[entity_type]
    table, name_col = meta['table'], meta['name']

    where = [_status_predicate(service, status, table)]
    params: List = []
    if query:
        where.append(f"{table}.{name_col} LIKE ?")
        params.append(f"%{query}%")

    sql = f"SELECT COUNT(*) FROM {table} WHERE {' AND '.join(where)}"
    return sql, params


# Reset scopes for re-queuing items so the worker re-attempts them.
RESET_SCOPES = ('item', 'failed')


def build_reset_query(
    service: str,
    entity_type: str,
    scope: str = 'item',
    entity_id=None,
) -> Tuple[str, List]:
    """Build the UPDATE that re-queues item(s) for enrichment.

    Re-queuing means clearing ``<service>_match_status`` back to NULL (and
    ``<service>_last_attempted`` to NULL): every worker's pending query selects
    ``match_status IS NULL`` first, so the item is retried on the next pass.
    Nulling last_attempted alone is NOT enough — the not_found retry path uses
    ``last_attempted < cutoff`` and ``NULL < cutoff`` is false, so the item
    would never be picked up.

      * scope='item'   -> a single row (requires entity_id)
      * scope='failed' -> every 'not_found' row for this entity type
    """
    _validate(service, entity_type)
    if scope not in RESET_SCOPES:
        raise UnmatchedQueryError(f"Invalid reset scope: {scope!r}")

    meta = _ENTITY_TABLE[entity_type]
    table = meta['table']
    ms = match_status_column(service)
    la = last_attempted_column(service)
    set_clause = f"SET {ms} = NULL, {la} = NULL"

    if scope == 'item':
        if not entity_id:
            raise UnmatchedQueryError("entity_id is required for an item reset")
        return f"UPDATE {table} {set_clause} WHERE id = ?", [entity_id]
    # 'failed' — re-queue everything this source explicitly gave up on.
    return f"UPDATE {table} {set_clause} WHERE {ms} = 'not_found'", []


def build_breakdown_query(service: str, entity_type: str) -> Tuple[str, List]:
    """Build the matched / not_found / pending / total tally for one entity type."""
    _validate(service, entity_type)
    meta = _ENTITY_TABLE[entity_type]
    table = meta['table']
    ms = f"{table}.{match_status_column(service)}"
    sql = (
        "SELECT "
        f"SUM(CASE WHEN {ms} = 'matched' THEN 1 ELSE 0 END) AS matched, "
        f"SUM(CASE WHEN {ms} = 'not_found' THEN 1 ELSE 0 END) AS not_found, "
        f"SUM(CASE WHEN {ms} IS NULL THEN 1 ELSE 0 END) AS pending, "
        f"COUNT(*) AS total "
        f"FROM {table}"
    )
    return sql, []


def _clamp_limit(limit) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return 50
    if n <= 0:
        return 50
    return min(n, MAX_LIMIT)


__all__ = [
    'SERVICE_ENTITY_SUPPORT',
    'VALID_STATUSES',
    'MAX_LIMIT',
    'UnmatchedQueryError',
    'supported_entity_types',
    'match_status_column',
    'last_attempted_column',
    'build_unmatched_query',
    'build_count_query',
    'build_breakdown_query',
    'build_reset_query',
    'RESET_SCOPES',
]
