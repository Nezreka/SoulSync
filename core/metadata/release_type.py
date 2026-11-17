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

import re
from typing import Any, List, Optional


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


# ---------------------------------------------------------------------------
# release kind from the release's shape (count + runtime)
# ---------------------------------------------------------------------------
# the store rule (apple, spotify, distrokid): 1 to 3 tracks under 30 minutes is
# a single, 4 to 6 tracks under 30 minutes is an ep, anything else is an album.
# count alone made wish you were here (5 tracks, 44 min) an ep and close to the
# edge (3 tracks, 38 min) a single. only for sources that didn't say: a
# provider's own explicit type always wins over this.
SHORT_RELEASE_MS = 30 * 60 * 1000


def _as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def release_type_from_shape(track_count: Any, total_ms: Any = None) -> str:
    """single / ep / album from track count and total runtime in ms.

    runtime unknown (None or 0): count alone, which is right for most short
    releases and the best there is. unknown count: album, never guess."""
    count = _as_int(track_count)
    if count <= 0:
        return 'album'
    runtime = _as_int(total_ms)
    if runtime >= SHORT_RELEASE_MS:
        return 'album'
    if count <= 3:
        return 'single'
    if count <= 6:
        return 'ep'
    return 'album'


def _track_ms(track: Any, seconds: bool = False) -> int:
    if isinstance(track, dict):
        get = track.get
    else:
        def get(key):
            return getattr(track, key, None)
    for key in ('duration_ms', 'trackTimeMillis'):
        ms = _as_int(get(key))
        if ms > 0:
            return ms
    if not seconds:
        return 0
    # deezer carries seconds under 'duration', discogs a clock string ('4:35')
    secs = get('duration')
    if isinstance(secs, str) and ':' in secs:
        return parse_clock_ms(secs)
    secs = _as_int(secs)
    return secs * 1000 if secs > 0 else 0


def parse_clock_ms(text: Any) -> int:
    """'4:35' or '1:02:03' to ms, 0 when it isn't a clock."""
    parts = str(text or '').strip().split(':')
    if len(parts) < 2 or not all(p.strip().isdigit() for p in parts):
        return 0
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total * 1000


def tracklist_runtime_ms(tracks: Any, expected_count: Any = None, *,
                         seconds: bool = False) -> Optional[int]:
    """sum of the track durations, or None unless the whole tracklist is at
    hand with a duration on every track (a partial sum would read short and
    call an album an ep). ``seconds``: the tracks keep a 'duration' in
    seconds or as '4:35' (deezer, discogs) rather than duration_ms."""
    if not tracks or not isinstance(tracks, (list, tuple)):
        return None
    if _as_int(expected_count) > len(tracks):
        return None
    total = 0
    for track in tracks:
        ms = _track_ms(track, seconds)
        if ms <= 0:
            return None
        total += ms
    return total


_ITUNES_SUFFIX = re.compile(r'\s-\s(Single|EP)\b')
_TITLE_SAYS_EP = re.compile(r'(?:^|[\s(\[-])(?:EP|E\.P\.?)(?=$|[\s)\]:,.-])')


def title_says_ep(title: Any) -> bool:
    """'The Heat EP', 'Hope For Something EP (Remixes)', 'The Def Leppard E.P.'.
    case sensitive on purpose: a lowercase 'ep' is a word, not a label."""
    return bool(_TITLE_SAYS_EP.search(str(title or '')))


def itunes_release_type(collection_name: Any, collection_type: Any = None,
                        track_count: Any = None, total_ms: Any = None) -> str:
    """apple names its own singles and eps: 'Song - Single', 'Thing - EP'
    (checked on 26k cached albums: 23.4k of 23.8k 1-3 track releases carry
    ' - Single'). apple applies the store rule itself, so no suffix means
    apple calls it an album, unless the title says EP outright. track count
    and runtime are only a fallback when there is no name to read."""
    name = str(collection_name or '')
    if 'compilation' in str(collection_type or '').lower():
        return 'compilation'
    m = _ITUNES_SUFFIX.search(name)
    if m:
        return 'single' if m.group(1) == 'Single' else 'ep'
    if title_says_ep(name):
        return 'ep'
    if name:
        return 'album'
    return release_type_from_shape(track_count, total_ms)
