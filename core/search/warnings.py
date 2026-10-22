"""warnings that ride back next to search results (#1831).

some answers are real but incomplete. prowlarr with one indexer down still
returns what the others found, so it isn't an error, but the list is missing
whatever the dead indexer had. the source notes that here and the basic search
hands it to the page.

a contextvar, so it needs no change to the plugin search signature. it's set
inside the task run_async creates, and every await and ensure_future under it
shares the same list.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Awaitable, Dict, List, Optional, Tuple

_notes: ContextVar[Optional[List[Dict[str, str]]]] = ContextVar(
    "soulsync_search_warnings", default=None,
)


def note_indexer_failure(source: str, indexer: str, error: str) -> None:
    """one indexer that failed while others answered. no-op outside collect().

    a relaxed-query retry can fail the same indexer again, so one row per
    indexer, latest error wins.
    """
    notes = _notes.get()
    if notes is None:
        return
    for note in notes:
        if note["source"] == source and note["indexer"] == indexer:
            note["error"] = error
            return
    notes.append({"source": source, "indexer": indexer, "error": error})


async def collect(awaitable: Awaitable[Any]) -> Tuple[Any, List[Dict[str, str]]]:
    """await a search and return ``(result, warnings)``."""
    token = _notes.set([])
    try:
        result = await awaitable
        return result, list(_notes.get() or [])
    finally:
        _notes.reset(token)
