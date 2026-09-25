"""Every artist a metadata source credits on a track or release (upstream 3.4.6).

A track is filed under one album artist, so "calvin harris feat. rihanna" did
not show on rihanna's page, and "watch the throne" not on kanye's. Spotify and
Deezer hand back the full credit list, with ids, when a worker matches a track
or an album. Library v2 already has the junctions for it -- ``lib2_track_artists``
and ``lib2_album_artists`` -- and every artist page reads through them, so a
credit is one more junction row.

Only for artists already in the library: a featured artist nobody owns a
release of does not become an artist row (the grid and the enrichment workers
would fill up with them). Upstream keeps such credits on a side table and links
them later; here they are dropped, and the next match after that artist joins
the library records them.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.provider_credits")

# how a source's artist id is found on a lib2 artist row
_ARTIST_ID_SQL = {
    "spotify": "SELECT id FROM lib2_artists WHERE spotify_id = ? LIMIT 1",
    "deezer": ("SELECT id FROM lib2_artists"
               " WHERE json_extract(external_ids, '$.deezer') = ? LIMIT 1"),
}


def _credited_ids(artists: Optional[Iterable[Any]]) -> list:
    out = []
    for artist in artists or []:
        if isinstance(artist, dict):
            pid = artist.get("id")
            if pid not in (None, ""):
                out.append(str(pid))
    return out


def link_credited_artists(conn: Any, entity_type: str, entity_id: int,
                          service: str, artists: Optional[Iterable[Any]]) -> int:
    """Add a featured credit for every artist ``service`` credits on this
    track/album who is a library artist. Returns how many rows were added.
    Never removes a credit and never raises: a credit is decoration on a
    match that already succeeded."""
    sql = _ARTIST_ID_SQL.get(str(service or "").lower())
    ids = _credited_ids(artists)
    if not sql or len(ids) < 2 or entity_type not in ("track", "album"):
        return 0  # a sole artist is the one it is filed under
    added = 0
    try:
        for position, provider_id in enumerate(ids):
            row = conn.execute(sql, (provider_id,)).fetchone()
            if not row:
                continue
            if entity_type == "track":
                cur = conn.execute(
                    "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position)"
                    " VALUES(?, ?, 'featured', ?)", (int(entity_id), int(row[0]), position))
            else:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)"
                    " VALUES(?, ?, 'featured')", (int(entity_id), int(row[0])))
            added += max(int(cur.rowcount or 0), 0)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.debug("credits for %s %s from %s skipped: %s", entity_type, entity_id, service, exc)
    return added


__all__ = ["link_credited_artists"]
