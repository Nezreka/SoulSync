"""The library at a glance (#2107): the numbers over the artist list.

Everything here comes from what the list already keeps: the member ids
(core.library2.member_version), each artist's completeness
(core.library2.artist_completeness) and the could-be-better counts
(core.library2.artist_reader). Nothing walks the library on its own.
"""
from __future__ import annotations

from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("library2.library_overview")


def _member_ids(conn) -> List[int]:
    from .member_version import cached_member_ids
    from .sql_util import library_index_artist_ids_sql
    member_sql = library_index_artist_ids_sql()
    ids = cached_member_ids(conn, member_sql)
    if ids is None:
        ids = [int(r[0]) for r in conn.execute(member_sql) if r[0] is not None]
    return sorted(set(ids))


def _watchlist_count(conn, profile_id: int) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist_artists'"
    ).fetchone()
    if not exists:
        return 0
    return int(conn.execute(
        "SELECT COUNT(*) FROM watchlist_artists WHERE profile_id = ?", (int(profile_id),)
    ).fetchone()[0])


def library_overview(conn, *, profile_id: int = 1) -> Dict[str, Any]:
    from .artist_completeness import Completeness, library_facts
    facts = library_facts(conn)
    members = _member_ids(conn)
    states = {"complete": 0, "incomplete": 0, "unchecked": 0, "unknown": 0}
    empty = Completeness()
    for artist_id in members:
        states[facts.artists.get(artist_id, empty).state] += 1
    return {
        "artists": len(members),
        "owned_tracks": facts.owned_tracks,
        "missing_tracks": facts.missing_tracks,
        "complete_artists": states["complete"],
        "incomplete_artists": states["incomplete"],
        # nothing expected yet, or a release of unknown size
        "unchecked_artists": states["unchecked"] + states["unknown"],
        "watchlist_artists": _watchlist_count(conn, profile_id),
    }


def upgrades_overview(conn) -> Dict[str, Any]:
    """Library artists with tracks below their quality profile, and how many
    such tracks they hold between them (a track two of them share counts for
    each)."""
    from .artist_reader import library_upgradable_counts
    counts = library_upgradable_counts(conn)
    members = set(_member_ids(conn))
    held = {i: n for i, n in counts.items() if i in members and n > 0}
    return {"upgrade_artists": len(held), "upgrade_tracks": sum(held.values())}


__all__ = ["library_overview", "upgrades_overview"]
