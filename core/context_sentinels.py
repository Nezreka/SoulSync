"""Placeholder ids the download and import paths invent when no real source id is known.

They stand in for context ('this came from the sync modal'), they are not ids —
nothing can be looked up by one, and two different artists share the same string.

They leak. A watchlist row was once written with 'from_sync_modal' in its spotify
id column; the scanner then keyed 25 similar artists by it, twice, and those rows
seeded the discovery pool for a month (#1284). So anything that stores an id needs
to be able to recognise one.

Two older local copies live in core/downloads/track_metadata_backfill.py and
core/imports/side_effects.py. Fold them in here when one of them next needs a change.
"""

from __future__ import annotations

from typing import Any

# every placeholder either path is known to produce
CONTEXT_SENTINEL_IDS = frozenset({
    'auto_import',
    'explicit_album',
    'explicit_artist',
    'from_sync_modal',
})


def is_context_sentinel(value: Any) -> bool:
    """True for a placeholder id, or for a blank one.

    a blank counts: an empty id is just as unqueryable, and the paths that produce
    these placeholders produce '' in the same breath. a real artist NAME is not a
    sentinel — the artist map legitimately keys a cached row by name when the
    artist matched no provider at all.
    """
    if value is None:
        return True
    text = str(value).strip()
    return not text or text.lower() in CONTEXT_SENTINEL_IDS
