"""Which Navidrome albums the hourly update has never fully read.

The incremental update walks Navidrome's "newest" album list and stops after 25
albums in a row it already knows. "newest" is Navidrome's album created date,
which comes from file times, so an import whose files kept old dates sorts deep
in the list, and tracks added to an album Navidrome already had never move it
at all. Neither was seen until a full refresh (TheHomeGuy: a full sync added "a
TON", and the last one was over three months old).

The census reads every album once an hour (500 a request) with its song count
and compares it with what the database holds: an album we don't have, or hold
fewer songs of, gets its artist re-read. An album that stays short after that
(a song SoulSync skips on purpose, say) is remembered with the count it had, so
it isn't re-read every hour; it comes back only when Navidrome's count moves.

Pure: the caller fetches and stores.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

# a library that drifted for months can have thousands; the rest wait an hour
MAX_ALBUMS_PER_RUN = 200


def albums_to_sync(census: Iterable[Dict[str, Any]], held: Dict[str, int],
                   tried: Dict[str, int], cap: int = MAX_ALBUMS_PER_RUN
                   ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """(albums to read now, the tried-at-count record to keep).

    ``held`` is {album id: tracks in the database}; ``tried`` is the record
    this returned last time. An album caught up drops out of the record."""
    picked: List[Dict[str, Any]] = []
    record: Dict[str, int] = {}
    for album in census or ():
        aid = str(album.get("id") or "")
        if not aid:
            continue
        try:
            want = int(album.get("songCount") or 0)
        except (TypeError, ValueError):
            continue
        if want <= 0 or held.get(aid, 0) >= want:
            continue
        if tried.get(aid) == want:
            record[aid] = want                  # read at this count already, still short
            continue
        if len(picked) < cap:
            picked.append(album)
            record[aid] = want
    return picked, record


__all__ = ["albums_to_sync", "MAX_ALBUMS_PER_RUN"]
