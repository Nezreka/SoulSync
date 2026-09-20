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
              "AND COALESCE(owned_f.file_state,'active') = 'active'{owner}")

# "whose library" is a second question on top of "is there a live file", and the
# two have to be asked together or a caller gets rows it does not own. The
# answer is one of:
#
#   ANY_OWNER   every library. What the enrichment workers want: metadata is
#               shared, so a row is worth enriching whoever has the file.
#   'shared'    the shared library only (owner IS NULL).
#   <int>       that profile's own library.
#
# Callers that leave `scope` alone get the AMBIENT scope: whoever is asking
# right now. While core.library_scope.SCOPE_PARKED is true that resolves to
# ANY_OWNER -- the feature is off, so nothing filters and every query is byte
# for byte what it was. Flipping the switch turns scoping on everywhere at
# once, which is the point of routing it through here.
ANY_OWNER = object()
_AMBIENT = object()


def _resolve_scope(scope):
    if scope is not _AMBIENT:
        return scope
    try:
        from core.library_scope import SCOPE_PARKED, current_library_scope
        return ANY_OWNER if SCOPE_PARKED else current_library_scope()
    except Exception:  # noqa: BLE001 - unreadable scope means do not filter
        return ANY_OWNER


def owner_clause(scope=_AMBIENT, column: str = "owned_f.owner_profile_id") -> str:
    """The owner half of the ownership predicate, ready to append.

    The leading ``+`` keeps SQLite off the owner index: the scope filters the
    rows a query's real predicates found, it is never the index to walk.
    Upstream measured 2 ms becoming 18 s on a 300k-track library without it.
    The profile id is an int we validate and inline, because `owned_sql`
    returns a plain string and threading parameters through every call site
    would be the only reason it could not.
    """
    resolved = _resolve_scope(scope)
    if resolved is ANY_OWNER or resolved is None:
        return ""
    if resolved == "shared":
        return f" AND +{column} IS NULL"
    return f" AND +{column} = {int(resolved)}"


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


def scope_visibility_sql(entity_type: str, alias: str, *, scope=_AMBIENT) -> str:
    """Which rows of ``alias`` this scope may see at all, or "" for no filter.

    Two halves, and both are needed (E-03). A row is visible when a file IN
    SCOPE hangs off it -- otherwise a profile sees the whole house's catalogue
    -- OR when this profile has monitoring intent on it, because a wanted album
    with nothing downloaded yet has no file to be found by and hiding it would
    remove exactly the rows the user is waiting for.

    Empty string when the scope is every library, which is also what a parked
    build gets: the page then filters nothing and shows what it always showed.
    """
    resolved = _resolve_scope(scope)
    if resolved is ANY_OWNER or resolved is None:
        return ""
    entity = str(entity_type or "").strip().lower().rstrip("s")
    if entity not in _OWNED:
        raise ValueError(f"Unknown entity type: {entity_type!r}")
    if not _VALID_IDENTIFIER.match(alias):
        raise ValueError(f"Invalid alias: {alias!r}")
    intent = intent_profile_id(resolved)
    has_intent = (
        f"EXISTS (SELECT 1 FROM lib2_monitor_rules mr"
        f"         WHERE mr.entity_type='{entity}' AND mr.entity_id={alias}.id"
        f"           AND mr.profile_id={intent} AND mr.monitored=1)"
    )
    mine = owned_sql(entity, alias, scope=resolved)
    if resolved != "shared":
        # An own library is an exception carved out of the house: it holds what
        # its owner fetched, plus what they asked for and have not got yet.
        return f"({mine} OR {has_intent})"
    # The shared library is the default home, so it is defined by what it is
    # NOT: only a row whose files all belong to someone else drops out. A row
    # with no file at all -- a discography entry, a wishlist artist, anything
    # the catalogue knows and nobody has fetched -- stays, which is the whole
    # page on an install where nobody keeps a library of their own.
    anyones = owned_sql(entity, alias, scope=ANY_OWNER)
    return f"(NOT {anyones} OR {mine} OR {has_intent})"


def intent_profile_id(scope=_AMBIENT) -> int:
    """Whose monitoring/wanted state a query in this scope should read.

    Ownership lives on the file, but INTENT -- monitored, wanted -- is already
    keyed per profile in lib2_monitor_rules and lib2_wanted_tracks. The two
    have to agree, or a scoped page shows one profile's files beside another
    profile's "I want this".

    'shared' is the admin profile, an own library is its own profile, and while
    the feature is parked this is 1 whatever the scope says: every caller
    joined `w.profile_id = 1` literally before, and a parked build must read
    exactly the rows it read yesterday.
    """
    resolved = _resolve_scope(scope)
    if resolved is ANY_OWNER or resolved is None or resolved == "shared":
        return 1
    return int(resolved)


def owned_sql(entity_type: str, alias: str, *, scope=_AMBIENT) -> str:
    """SQL predicate: ``alias`` is a row the caller actually owns.

    ``alias`` is an internal literal like ``t`` or ``e``; validated as an
    identifier because it is interpolated, same reasoning as above. ``scope``
    says whose library counts -- see ANY_OWNER above; pass it explicitly from
    anything that must see every library regardless of who is asking.
    """
    if not _VALID_IDENTIFIER.match(alias):
        raise ValueError(f"Invalid alias: {alias!r}")
    key = str(entity_type or "").strip().lower().rstrip("s")
    if key not in _OWNED:
        raise ValueError(f"Unknown entity type: {entity_type!r}")
    return _OWNED[key].format(alias=alias, owner=owner_clause(scope))


__all__ = ["ANY_OWNER", "intent_profile_id", "owned_sql", "owner_clause",
           "scope_visibility_sql", "select_existing_ids"]
