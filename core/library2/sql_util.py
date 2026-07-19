"""Shared SQL helpers for Library v2.

A single, chunk-safe home for the ``IN (?, ?, …)`` id-set queries that were
otherwise re-derived inline all over ``core/library2/`` — each copy risking
SQLite's per-statement variable limit (999 before 3.32, 32766 after) once an
id list scales with a large library (review Teil B, reuse). Chunking here once
means that limit is handled in one place instead of being re-fixed at every
call site.
"""

from __future__ import annotations

from typing import Any, Iterable, Set

# Well under SQLite's oldest documented SQLITE_MAX_VARIABLE_NUMBER (999), so a
# single chunk's placeholder count is safe on every SQLite build we run on.
_CHUNK = 900


def select_existing_ids(
    conn: Any, table: str, ids: Iterable[Any], *, column: str = "id",
    chunk: int = _CHUNK,
) -> Set[int]:
    """Return the subset of ``ids`` that exist in ``table``.``column``.

    Chunk-safe: an ``IN`` list larger than SQLite's variable limit would
    otherwise raise. ``table``/``column`` are trusted internal literals (never
    user input), same as every inline query this replaces.
    """
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


__all__ = ["select_existing_ids"]
