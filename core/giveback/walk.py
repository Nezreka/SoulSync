"""one round's walk through the library for a give back kind: the next rows
past the saved cursor, and the cursor moved on (to the top, resting a day,
once the walk reaches the end)."""

from __future__ import annotations

from typing import Any, Dict, List

from core.giveback import confidence, store


def next_rows(conn: Any, kind: str, limit: int) -> List[Dict[str, Any]]:
    if store.resting(conn, kind):
        return []
    return confidence.candidates(conn, store.get_cursor(conn, kind), limit, kind=kind)


def advance(conn: Any, kind: str, rows: List[Dict[str, Any]], limit: int, upto: int = 0) -> None:
    """``upto``: the last file id this round got through (a round that filled
    its batch early stops there, so the rest are read next time)"""
    if not rows:
        store.set_cursor(conn, kind, 0, wrapped=True)
        return
    last = upto or rows[-1]['file_id']
    if last >= rows[-1]['file_id'] and len(rows) < limit:
        store.set_cursor(conn, kind, 0, wrapped=True)
    else:
        store.set_cursor(conn, kind, last)
