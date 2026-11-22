"""did every matched track actually land on the server playlist (#2235).

a mirror read "167 tracks, 167 in library" while the jellyfin playlist held
149. "in library" is soulsync's own answer (the matcher found the file), the
server playlist is the server's. nothing compared the two: the write was
trusted, jellyfin answers 204 to an add it quietly drops (an id it doesn't
know, a track already on the playlist), and the next scheduled run saw
"167 matched last time" and skipped forever.

so after a write the sync reads the playlist back and every matched track
gets one of these:

- on_playlist: the server holds it
- duplicate: it matched the same file as another track of the playlist. a
  server playlist holds a file once, so it rides along with that one
- not_on_server: sent, and the server doesn't hold it. almost always the
  server doesn't know the file under the id soulsync has for it yet (not
  scanned, or rescanned under a new id)

pure, no I/O: the sync reads the ids, this decides.
"""

from __future__ import annotations

from typing import Iterable, Optional, Set

ON_PLAYLIST = "on_playlist"
DUPLICATE = "duplicate"
NOT_ON_SERVER = "not_on_server"

PLACEMENTS = (ON_PLAYLIST, DUPLICATE, NOT_ON_SERVER)


def norm_id(value) -> str:
    """jellyfin hands ids back without hyphens and in lower case whatever
    form they went in as, so compare them in one form"""
    return str(value or "").strip().replace("-", "").lower()


def missing_from_server(sent_ids: Iterable, server_ids: Optional[Iterable]) -> Optional[Set[str]]:
    """the sent ids the server playlist doesn't hold, in norm_id form.

    None when the playlist couldn't be read: no answer is not "all missing"."""
    if server_ids is None:
        return None
    held = {norm_id(s) for s in server_ids if norm_id(s)}
    return {norm_id(s) for s in sent_ids if norm_id(s) and norm_id(s) not in held}


def server_item_id(db_track):
    """the id a playlist write may send for a matched library track, or None.

    with a server asked for, a library row the server has no id for comes
    back carrying its catalogue row id instead (database _rows_to_tracks,
    ``server_id_known`` False). that id means nothing to the server: jellyfin
    drops it without a word, plex resolves it to an unrelated item. such a
    track is in the library, not on the server yet (#2235)."""
    if db_track is None or getattr(db_track, "server_id_known", None) is False:
        return None
    value = getattr(db_track, "id", None)
    return value if norm_id(value) else None


def placement_of(extra) -> Optional[str]:
    """the placement a sync stamped on a mirrored track's extra_data, or None
    when no sync has said (never synced, or the server couldn't be read)"""
    if not isinstance(extra, dict):
        return None
    value = extra.get("server_placement")
    return value if value in PLACEMENTS else None


__all__ = [
    "DUPLICATE",
    "NOT_ON_SERVER",
    "ON_PLAYLIST",
    "PLACEMENTS",
    "missing_from_server",
    "norm_id",
    "placement_of",
    "server_item_id",
]
