"""Wire real metadata clients to the blocklist backfill resolvers.

Resolves a blocked item's ID on each metadata source by searching that source
for the name and taking a confidently name-matched hit. Confidence = exact
significant-token match (drops articles/punctuation) so we never hang a wrong
ID on an entry. Albums/tracks additionally require the parent artist to match
when both sides expose one.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("blocklist.runtime")

_STOP = {"the", "a", "an", "feat", "ft", "featuring", "with"}


def _tokens(text: Any) -> frozenset:
    words = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split()
    return frozenset(w for w in words if w not in _STOP)


def _name_of(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("name") or obj.get("title") or "")
    return str(getattr(obj, "name", None) or getattr(obj, "title", None) or "")


def _id_of(obj: Any) -> Optional[str]:
    val = obj.get("id") if isinstance(obj, dict) else getattr(obj, "id", None)
    return str(val) if val else None


def _confident(result_name: str, want_name: str) -> bool:
    rt, wt = _tokens(result_name), _tokens(want_name)
    return bool(rt) and rt == wt


def _make_resolver(source: str) -> Callable[..., Optional[str]]:
    def resolve(entity_type: str, name: str, parent_name: Optional[str] = None) -> Optional[str]:
        from core.metadata.registry import get_client_for_source
        client = get_client_for_source(source)
        if not client:
            return None
        method = {
            "artist": "search_artists",
            "album": "search_albums",
            "track": "search_tracks",
        }.get(entity_type)
        fn = getattr(client, method, None) if method else None
        if not fn:
            return None
        try:
            results = fn(name, limit=5) or []
        except Exception as e:
            logger.debug("%s %s search failed for %r: %s", source, entity_type, name, e)
            return None
        for r in results:
            if not _confident(_name_of(r), name):
                continue
            # For album/track, also require the artist to line up when known.
            if entity_type in ("album", "track") and parent_name:
                artists = (r.get("artists") if isinstance(r, dict) else getattr(r, "artists", None)) or []
                cand_artists = " ".join(
                    a.get("name", "") if isinstance(a, dict) else str(a) for a in artists)
                if _tokens(parent_name) and not (_tokens(parent_name) & _tokens(cand_artists)):
                    continue
            rid = _id_of(r)
            if rid:
                return rid
        return None

    return resolve


def build_resolvers() -> Dict[str, Callable[..., Optional[str]]]:
    """Source→resolver map for core.blocklist.backfill.resolve_missing_ids."""
    return {s: _make_resolver(s) for s in ("spotify", "itunes", "deezer", "musicbrainz")}
