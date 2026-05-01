"""Active-source-aware artist ID picker for bulk watchlist add.

The bulk "Add unwatched library artists to watchlist" endpoint used
to drop artists silently whenever they didn't carry an ID for the
user's currently active metadata source. A Spotify-primary user with
library artists matched only against iTunes/Deezer would see them
counted as ``skipped_no_id`` and never make it onto the watchlist —
surfacing on Discord as "Library and Watchlist not syncing
correctly". The per-artist Enhanced View sync sometimes "fixed" them
because it re-ran enrichment that occasionally populated the missing
ID, but that workaround couldn't help artists Spotify simply doesn't
have.

This helper picks the active source's ID first, then falls back
through every other supported source so an artist makes it onto the
watchlist as long as ANY metadata source can identify them.
"""

from typing import Any, Dict, Optional, Tuple


# (source_name, library-artist-row column). Order is also the
# fallback priority — Spotify > iTunes > Deezer > Discogs by default
# coverage. The active source moves to the front of the queue inside
# pick_artist_id_for_watchlist.
SOURCE_ID_COLUMNS = (
    ('spotify', 'spotify_artist_id'),
    ('itunes', 'itunes_artist_id'),
    ('deezer', 'deezer_id'),
    ('discogs', 'discogs_id'),
)


def pick_artist_id_for_watchlist(
    artist: Dict[str, Any],
    active_source: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Pick a (source-id, source-name) pair for adding ``artist`` to
    the watchlist.

    Tries ``active_source`` first when it appears in
    ``SOURCE_ID_COLUMNS``, then falls back through every other source
    in registration order. Empty strings count as missing. Returns
    ``(None, None)`` only when the artist truly has no usable source
    ID — that's the only legitimate skip reason for the bulk-add
    flow.

    The returned ID is always coerced to ``str`` because watchlist
    columns are TEXT and SQLite will happily store the original int
    type otherwise (which then breaks ID-based equality checks
    between watchlist and library code paths).
    """
    preferred = next(
        ((src, col) for src, col in SOURCE_ID_COLUMNS if src == active_source),
        None,
    )
    ordered = [preferred] if preferred else []
    ordered.extend(
        (src, col) for src, col in SOURCE_ID_COLUMNS if (src, col) != preferred
    )
    for src, col in ordered:
        if not src:
            continue
        value = artist.get(col)
        if value:
            return str(value), src
    return None, None
