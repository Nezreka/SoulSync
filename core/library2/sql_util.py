"""Shared SQL helpers for Library v2.

A single, chunk-safe home for the ``IN (?, ?, …)`` id-set queries that were
otherwise re-derived inline all over ``core/library2/`` — each copy risking
SQLite's per-statement variable limit (999 before 3.32, 32766 after) once an
id list scales with a large library (review Teil B, reuse). Chunking here once
means that limit is handled in one place instead of being re-fixed at every
call site.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Set

# Well under SQLite's oldest documented SQLITE_MAX_VARIABLE_NUMBER (999), so a
# single chunk's placeholder count is safe on every SQLite build we run on.
_CHUNK = 900

# table/column are meant to be trusted internal literals, never user input —
# but they're interpolated directly into the query text (can't be bound as
# parameters), so a plain identifier check is cheap insurance: today's one
# caller always passes hardcoded strings, but nothing stopped a future caller
# from deriving either from a variable and turning this into an injection
# point. A valid identifier can't break out of the query, whatever it means.
_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def select_existing_ids(
    conn: Any, table: str, ids: Iterable[Any], *, column: str = "id",
    chunk: int = _CHUNK,
) -> Set[int]:
    """Return the subset of ``ids`` that exist in ``table``.``column``.

    Chunk-safe: an ``IN`` list larger than SQLite's variable limit would
    otherwise raise. ``table``/``column`` are trusted internal literals (never
    user input), same as every inline query this replaces — validated as
    plain identifiers regardless, since they're interpolated into the SQL text.
    """
    if not _VALID_IDENTIFIER.match(table) or not _VALID_IDENTIFIER.match(column):
        raise ValueError(f"Invalid table/column identifier: {table!r}.{column!r}")
    unique = list({int(i) for i in ids})
    found: Set[int] = set()
    for start in range(0, len(unique), max(1, chunk)):
        part = unique[start:start + max(1, chunk)]
        marks = ",".join("?" for _ in part)
        found.update(
            int(r[0]) for r in conn.execute(
                f"SELECT {column} FROM {table} WHERE {column} IN ({marks})", part
            )
        )
    return found


# Ownership is live file evidence, never `origin` — that column records where a
# row came from, not whether the user has it. The legacy `artists`/`albums`/
# `tracks` tables *were* the owned library by construction; v2 keeps discography,
# wishlist and provider-only rows beside it, so every query that used to mean
# "the library" has to say so now. Four places had re-derived this; they read the
# same rule from here.
_LIVE_FILE = ("owned_f.path IS NOT NULL AND TRIM(owned_f.path) <> '' "
              "AND COALESCE(owned_f.file_state,'active') = 'active'")

_OWNED = {
    "track": ("EXISTS (SELECT 1 FROM lib2_track_files owned_f"
              "         WHERE owned_f.track_id = {alias}.id AND " + _LIVE_FILE + ")"),
    "album": ("EXISTS (SELECT 1 FROM lib2_tracks owned_t"
              "         JOIN lib2_track_files owned_f ON owned_f.track_id = owned_t.id"
              "        WHERE owned_t.album_id = {alias}.id AND " + _LIVE_FILE + ")"),
    # Either credit counts: the album's primary artist and a featured credit on
    # the track are both in the library.
    "artist": ("(EXISTS (SELECT 1 FROM lib2_tracks owned_t"
               "           JOIN lib2_albums owned_al ON owned_al.id = owned_t.album_id"
               "           JOIN lib2_track_files owned_f ON owned_f.track_id = owned_t.id"
               "          WHERE owned_al.primary_artist_id = {alias}.id AND " + _LIVE_FILE + ")"
               " OR EXISTS (SELECT 1 FROM lib2_tracks owned_t"
               "              JOIN lib2_track_artists owned_ta ON owned_ta.track_id = owned_t.id"
               "              JOIN lib2_track_files owned_f ON owned_f.track_id = owned_t.id"
               "             WHERE owned_ta.artist_id = {alias}.id AND " + _LIVE_FILE + "))"),
}


def owned_sql(entity_type: str, alias: str) -> str:
    """SQL predicate: ``alias`` is a row the user actually owns.

    ``alias`` is an internal literal like ``t`` or ``e``; validated as an
    identifier because it is interpolated, same reasoning as above.
    """
    if not _VALID_IDENTIFIER.match(alias):
        raise ValueError(f"Invalid alias: {alias!r}")
    key = str(entity_type or "").strip().lower().rstrip("s")
    if key not in _OWNED:
        raise ValueError(f"Unknown entity type: {entity_type!r}")
    return _OWNED[key].format(alias=alias)


__all__ = ["owned_sql", "select_existing_ids"]
