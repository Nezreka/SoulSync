"""Cross-source ID backfill for blocklist entries.

When a user blocks an item, the modal gives us the ID for ONE source (the one
they searched). For the ban to survive a source switch, we resolve the OTHER
sources' IDs too — matching the blocked artist/album/track by name on each
source and taking a confident hit.

The resolution is kept pure + injected so it tests without a network: callers
pass a ``resolvers`` map ``{source: fn(entity_type, name, parent_name) -> id |
None}``. ``core/blocklist/runtime.py`` wires the real metadata clients.

Honest about fragility (acknowledged in design): artist matching is reliable,
album/track cross-source matching is best-effort (editions, common titles), so
a resolver returning None just leaves that source unmatched — the artist
name-fallback in matching.py covers artist gaps; album/track gaps mean that
ban only applies on sources where an ID resolved.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.blocklist.matching import SOURCE_ID_FIELDS


def resolve_missing_ids(
    entry: Dict[str, Any],
    resolvers: Dict[str, Callable[..., Optional[str]]],
) -> Dict[str, str]:
    """Return ``{id_column: resolved_id}`` for the sources currently missing an
    ID on ``entry``. Never raises — a resolver that errors is skipped."""
    out: Dict[str, str] = {}
    entity_type = entry.get("entity_type")
    name = entry.get("name")
    parent = entry.get("parent_name")
    if not entity_type or not name:
        return out
    for source, col in SOURCE_ID_FIELDS.items():
        if entry.get(col):
            continue  # already known
        fn = resolvers.get(source)
        if not fn:
            continue
        try:
            rid = fn(entity_type, name, parent)
        except Exception:
            rid = None
        if rid:
            out[col] = str(rid)
    return out
