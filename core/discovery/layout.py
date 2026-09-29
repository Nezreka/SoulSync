"""Discover page layout: the single source of truth for section ids, zones, order.

The 19 discover sections live in 4 zones, top to bottom: For You, New &
Missing, Library Signals, Explore & Build. The frontend mirrors these ids for
rendering; the API validates and persists per-profile layouts against them.

A layout is a list of ``{'id', 'zone', 'enabled', 'position'}`` entries.
``sanitize`` validates a proposed layout (unknown ids / missing sections are
400s; duplicates are deduped; invalid zones fall back to defaults).
``merge_over_defaults`` merges saved rows over the defaults so newly shipped
sections appear even when the saved layout predates them.
"""

from __future__ import annotations

from typing import Any, Dict, List

# The four zones, top to bottom on the page.
ZONES = ('for-you', 'new-missing', 'library', 'tools')

ZONE_LABELS = {
    'for-you': 'For You',
    'new-missing': 'New & Missing',
    'library': 'Library Signals',
    'tools': 'Explore & Build',
}

# All 19 sections, in default page order (zone by zone, matching the page).
SECTION_IDS = (
    # for-you: the most personal rows lead, then the dial and its targets
    'your-mixes-section',
    'adv-wave',
    'listening-recs-section',
    'recommended-artists-section',
    'discover-bylt-sections',
    # new-missing: fresh releases and collection gaps
    'recent-releases',
    'cache-genre-releases',
    'seasonal-albums-section',
    'cache-undiscovered',
    'cache-label-explorer',
    'your-albums-section',
    # library: saved artists, eras, deep cuts
    'your-artists-section',
    'year-mixes-section',
    'cache-deep-cuts',
    # tools: the lab — genres, radio, ListenBrainz, custom builder
    'cache-genre-explorer',
    'lastfm-radio',
    'listenbrainz',
    'deezer-editorial',
    'build-a-playlist',
)

_IDS = frozenset(SECTION_IDS)

DEFAULT_ZONE = {
    'your-mixes-section': 'for-you',
    'adv-wave': 'for-you',
    'listening-recs-section': 'for-you',
    'recommended-artists-section': 'for-you',
    'discover-bylt-sections': 'for-you',
    'recent-releases': 'new-missing',
    'cache-genre-releases': 'new-missing',
    'seasonal-albums-section': 'new-missing',
    'cache-undiscovered': 'new-missing',
    'cache-label-explorer': 'new-missing',
    'your-albums-section': 'new-missing',
    'your-artists-section': 'library',
    'year-mixes-section': 'library',
    'cache-deep-cuts': 'library',
    'cache-genre-explorer': 'tools',
    'lastfm-radio': 'tools',
    'listenbrainz': 'tools',
    'deezer-editorial': 'tools',
    'build-a-playlist': 'tools',
}


class LayoutValidationError(ValueError):
    """A proposed layout failed validation. The API answers 400."""


def default_layout() -> List[Dict[str, Any]]:
    """The full 19-entry layout: no saved preferences == exactly the current
    page order, every section enabled."""
    entries: List[Dict[str, Any]] = []
    for zone in ZONES:
        position = 0
        for sid in SECTION_IDS:
            if DEFAULT_ZONE[sid] != zone:
                continue
            entries.append({'id': sid, 'zone': zone, 'enabled': True,
                            'position': position})
            position += 1
    return entries


def sanitize(sections: Any) -> List[Dict[str, Any]]:
    """Validate a proposed layout. Raises LayoutValidationError (-> 400) for
    unknown section ids or missing sections. Duplicates are deduped (first
    wins); invalid zones fall back to the section's default zone. Positions
    are per-zone ordinals derived from the given order."""
    if not isinstance(sections, list) or not sections:
        raise LayoutValidationError('sections must be a non-empty list')
    entries: List[Dict[str, Any]] = []
    seen = set()
    for raw in sections:
        if not isinstance(raw, dict):
            raise LayoutValidationError('each section must be an object')
        sid = raw.get('id')
        if sid not in _IDS:
            raise LayoutValidationError(f'unknown section id: {sid!r}')
        if sid in seen:
            continue  # duplicates deduped: the first occurrence wins
        seen.add(sid)
        zone = raw.get('zone')
        if zone not in ZONES:
            zone = DEFAULT_ZONE[sid]
        entries.append({'id': sid, 'zone': zone,
                        'enabled': bool(raw.get('enabled', True))})
    missing = [s for s in SECTION_IDS if s not in seen]
    if missing:
        raise LayoutValidationError('missing sections: ' + ', '.join(missing))
    counters = {z: 0 for z in ZONES}
    for entry in entries:
        entry['position'] = counters[entry['zone']]
        counters[entry['zone']] += 1
    return entries


def merge_over_defaults(saved_rows: Any) -> List[Dict[str, Any]]:
    """Merge saved DB rows over the defaults. Sections absent from the saved
    layout (newly shipped after the user saved) appear enabled in their
    default zone; saved sections keep their zone/enabled/position."""
    saved: Dict[str, Dict[str, Any]] = {}
    for row in saved_rows or []:
        if not isinstance(row, dict):
            continue
        sid = row.get('section_id', row.get('id'))
        if sid not in _IDS or sid in saved:
            continue
        zone = row.get('zone')
        position = row.get('position')
        saved[sid] = {
            'id': sid,
            'zone': zone if zone in ZONES else DEFAULT_ZONE[sid],
            'enabled': bool(row.get('enabled', True)),
            'position': position if isinstance(position, int) else 0,
        }
    out: List[Dict[str, Any]] = []
    for zone in ZONES:
        zone_saved = sorted(
            (e for e in saved.values() if e['zone'] == zone),
            key=lambda e: (e['position'], SECTION_IDS.index(e['id'])))
        zone_ids = [e['id'] for e in zone_saved]
        for sid in SECTION_IDS:
            if DEFAULT_ZONE[sid] == zone and sid not in saved:
                zone_ids.append(sid)
        for position, sid in enumerate(zone_ids):
            entry = saved.get(sid)
            out.append({'id': sid, 'zone': zone,
                        'enabled': entry['enabled'] if entry else True,
                        'position': position})
    return out
