"""The legacy enrichment payload declaration, read by the upgrade importer.

One map — ``_ENRICHMENT_PAYLOAD`` — says which legacy column carries which
provider field for artists, albums and tracks. ``core.library2.importer`` reads
it during the one-shot legacy→lib2 migration so a Last.fm wiki, a Discogs
catalogue number or a Bandcamp label crosses instead of being overwritten with
an empty ``{}`` (docs §50.4.4.34). A provider added here is therefore carried by
the migration without a second edit.

This module used to be the legacy↔lib2 mirror: a resync that pushed refreshed
provider data from the legacy row onto its lib2 twin, plus the divergence audit
that checked the two agreed. Both are gone. All fourteen provider workers write
``lib2_*`` directly and ``tests/library2/test_legacy_usage_ratchet.py`` pins
runtime legacy access at ``reads: 0, writes: 0``, so there is no legacy side
left to mirror from. What survives is the declaration alone, and it lives only
as long as the upgrade path does.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple


def _row_get(row: Any, col: str) -> Optional[Any]:
    return row[col] if col in row.keys() else None


def _list(raw):
    """Legacy stores repeated values as a JSON array or a comma string."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else None
    except (TypeError, ValueError):
        parts = [p.strip() for p in str(raw).split(",") if p.strip()]
        return parts or None


# The provider payload each entity type carries, as ``{source: {key: legacy
# column}}``. Values wrapped in ``_AsList`` come through ``_list``.
#
# These columns are folded into one JSON bucket rather than named individually,
# so without this declaration the migration cannot see them — which is how the
# entire album/track payload came to be dropped (docs §50.4.4.3).
class _AsList(str):
    """Marks a legacy column whose value is a list, not a scalar."""


_ENRICHMENT_PAYLOAD: Dict[str, Dict[str, Dict[str, str]]] = {
    "artist": {
        "lastfm": {
            "bio": "lastfm_bio", "listeners": "lastfm_listeners",
            "playcount": "lastfm_playcount", "tags": _AsList("lastfm_tags"),
            "similar": _AsList("lastfm_similar"), "url": "lastfm_url",
        },
        "genius": {
            "description": "genius_description",
            "alt_names": _AsList("genius_alt_names"), "url": "genius_url",
        },
        "discogs": {
            "bio": "discogs_bio", "members": _AsList("discogs_members"),
            "urls": _AsList("discogs_urls"),
        },
    },
    "album": {
        "lastfm": {
            "listeners": "lastfm_listeners", "playcount": "lastfm_playcount",
            "tags": _AsList("lastfm_tags"), "wiki": "lastfm_wiki",
        },
        "discogs": {
            "genres": _AsList("discogs_genres"), "styles": _AsList("discogs_styles"),
            "label": "discogs_label", "catno": "discogs_catno",
            "country": "discogs_country", "rating": "discogs_rating",
            "rating_count": "discogs_rating_count",
        },
        "bandcamp": {
            "id": "bandcamp_id",
            "tags": _AsList("bandcamp_tags"), "label": "bandcamp_label",
        },
    },
    "track": {
        "lastfm": {
            "listeners": "lastfm_listeners", "playcount": "lastfm_playcount",
            "tags": _AsList("lastfm_tags"),
        },
        "genius": {"description": "genius_description", "url": "genius_url"},
        "bandcamp": {
            "id": "bandcamp_id",
            "tags": _AsList("bandcamp_tags"), "label": "bandcamp_label",
        },
    },
}


def enrichment_columns(entity_type: str) -> Tuple[str, ...]:
    """Every legacy column the enrichment bucket reads for one entity type."""
    return tuple(sorted({
        str(column)
        for _source, sources in (_ENRICHMENT_PAYLOAD.get(entity_type) or {}).items()
        for column in sources.values()
    }))


def _enrichment_payload(entity_type: str, legacy_row: Any) -> Dict[str, Dict[str, Any]]:
    """Provider payload keyed by source — the ``bios`` Nezreka named, and the
    album/track equivalent.

    This lives in an ``enrichment`` JSON column rather than in table columns
    because a Last.fm wiki and a Discogs catalogue number are different data,
    not one field from two sources (same reasoning as
    ``importer._artist_enrichment_payload``). A provider that wrote nothing
    leaves no empty bucket behind.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for source, fields in (_ENRICHMENT_PAYLOAD.get(entity_type) or {}).items():
        cleaned = {}
        for key, column in fields.items():
            raw = _row_get(legacy_row, str(column))
            value = _list(raw) if isinstance(column, _AsList) else raw
            if value not in (None, "", []):
                cleaned[key] = value
        if cleaned:
            out[source] = cleaned
    return out


__all__ = [
    "enrichment_columns",
]
