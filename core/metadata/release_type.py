"""Canonical mapping from raw provider release-type vocabulary to the
internal `album_type` field that drives discography binning + UI.

Why this exists
---------------
Three sites historically duplicated the same "best-effort primary-type
→ album_type" mapping, each with a slightly different vocabulary:

    core/musicbrainz_search.py: `_map_release_type` knew about
        {album, single, ep, compilation}, defaulted unknown → 'album'.
    core/metadata/types.py: inline `{single: single, ep: ep}.get(...)` —
        didn't even know about 'compilation', also defaulted → 'album'.
    core/metadata/cache.py: Deezer-specific record_type validator —
        intentionally narrow, kept here for its provider.

Issue #650 (S-Bryce) reported that MusicBrainz tags music videos and
some legitimate singles with primary-type=`Other`, which both mappers
silently routed to `album_type='album'`. Combined with the API-level
filter at `musicbrainz_search.search_albums` (which only requested
`type=album|ep|single` from MB and dropped 'Other' entirely), users
with MB-as-primary saw entire release-groups go missing from artist
discography views, and downloaded tracks from those release-groups
appeared as orphan "ghost" tracks bound to no album card.

Fix shape: one shared mapper consumed by every provider's
`raw → Album dataclass` projection. Knows about 'other' and
'broadcast' (MB's two remaining primary-type vocabulary words) and
maps them to 'single' so they land in the Singles section of the
artist detail page — they're almost always single-track music
releases (music videos, broadcast singles, one-off web releases).
Falling through to 'album' was the original sin — places them in
Albums view where they look misleading and clutter the proper LP
list.
"""

from __future__ import annotations

from typing import List, Optional


# MB primary-type vocabulary as of 2026 — `Album | Single | EP |
# Broadcast | Other`. Compilation is a *secondary* type; querying MB
# with type=compilation silently breaks (returns ~10% of expected
# results) — see musicbrainz_client.browse_artist_release_groups docs.
_AUDIO_OTHER_PRIMARY_TYPES = frozenset({'other', 'broadcast'})


def map_release_group_type(primary_type: Optional[str],
                           secondary_types: Optional[List[str]] = None) -> str:
    """Project a raw provider release-group primary-type + secondary-types
    into the internal `album_type` value the UI binning expects.

    Returns one of: `'album'`, `'single'`, `'ep'`, `'compilation'`.

    Mapping rules:

    - `single` / `ep` pass through unchanged.
    - `compilation` (primary or secondary) becomes `'compilation'`. The
      compilation secondary-type check is required because MB's
      canonical pattern is `primary=Album, secondary=[Compilation]`.
    - `other` / `broadcast` become `'single'`. Almost always
      single-track music releases (music videos, one-off web drops,
      broadcast singles). Placing them in Singles is the pragmatic
      bucket — they're not LPs, but excluding them entirely (the
      pre-fix behaviour) hid legitimate tracks.
    - Anything else (including empty/None) → `'album'`. Matches the
      pre-fix default so no existing classifications shift.

    `secondary_types` is optional because the legacy types.py call site
    doesn't have access to it from the same level of raw structure.
    Pass `None` (or omit) for the secondary-types-unavailable path.
    """
    pt = (primary_type or '').strip().lower()

    if pt == 'single':
        return 'single'
    if pt == 'ep':
        return 'ep'
    if pt == 'compilation':
        return 'compilation'

    # Secondary-type override: MB's compilation albums always carry
    # `primary=Album` with `secondary=[Compilation]`, so the primary
    # check above can't catch them.
    if secondary_types:
        normalized = {str(s).strip().lower() for s in secondary_types if s}
        if 'compilation' in normalized:
            return 'compilation'

    if pt in _AUDIO_OTHER_PRIMARY_TYPES:
        return 'single'

    return 'album'
