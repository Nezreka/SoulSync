"""A library artist's corrected match carries over to the watchlist (#1901).

Ktzenjammer: "when i change the match in the library, does it update the match
in the watchlist?" It didn't. The watchlist keeps its own copy of each
source's artist id, so fixing a wrong "1905" in the library left the watchlist
scanning the wrong one for new releases.

Which watchlist row is "this artist" is decided by ids, never by name: two
different artists called 1905 is exactly the case being fixed. A row is the
library artist's when one of its source ids equals the library artist's id for
that source, read BEFORE the change. Only the fixing profile's own watchlist
follows; another profile may be watching the other 1905 on purpose.
"""

from __future__ import annotations

from typing import Any, Dict, List

# service -> (library artists column, watchlist_artists column). Only the
# sources the watchlist keeps an id for.
COLUMNS: Dict[str, tuple] = {
    'spotify': ('spotify_artist_id', 'spotify_artist_id'),
    'itunes': ('itunes_artist_id', 'itunes_artist_id'),
    'deezer': ('deezer_id', 'deezer_artist_id'),
    'discogs': ('discogs_id', 'discogs_artist_id'),
    'musicbrainz': ('musicbrainz_id', 'musicbrainz_artist_id'),
    'amazon': ('amazon_id', 'amazon_artist_id'),
}


def library_artist_ids(cursor, artist_id: Any) -> Dict[str, str]:
    """The library artist's current id for every source the watchlist tracks."""
    cols = ', '.join(lib for lib, _ in COLUMNS.values())
    cursor.execute(f"SELECT {cols} FROM artists WHERE id = ?", (artist_id,))
    row = cursor.fetchone()
    if not row:
        return {}
    return {svc: str(row[i]) for i, svc in enumerate(COLUMNS) if row[i]}


def follow_library_artist_match(cursor, *, before: Dict[str, str], service: str,
                                new_id: str, profile_id: int) -> Dict[str, Any]:
    """Point this profile's watchlist entry for the artist at ``new_id``.

    ``before`` is ``library_artist_ids`` from before the library changed.
    Answers ``{'updated': [names], 'skipped': [names]}``; skipped means another
    watched artist already holds ``new_id`` (the watchlist's own duplicate
    rule), so that row is left alone rather than given a clashing id.
    """
    result: Dict[str, List[str]] = {'updated': [], 'skipped': []}
    if service not in COLUMNS or not new_id or not before:
        return result
    wl_col = COLUMNS[service][1]
    clauses, params = [], []
    for svc, old in before.items():
        clauses.append(f"{COLUMNS[svc][1]} = ?")
        params.append(old)
    cursor.execute(
        f"SELECT id, artist_name, {wl_col} FROM watchlist_artists "
        f"WHERE profile_id = ? AND ({' OR '.join(clauses)})",
        (profile_id, *params),
    )
    for row_id, name, current in cursor.fetchall():
        if str(current or '') == str(new_id):
            continue
        cursor.execute(
            f"SELECT 1 FROM watchlist_artists WHERE profile_id = ? AND {wl_col} = ? AND id != ?",
            (profile_id, new_id, row_id),
        )
        if cursor.fetchone():
            result['skipped'].append(name)
            continue
        cursor.execute(
            f"UPDATE watchlist_artists SET {wl_col} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_id, row_id),
        )
        result['updated'].append(name)
    return result
