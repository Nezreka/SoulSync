"""Point a media server playlist's entry at another copy of the same song (#2172).

Keep Best can remove a duplicate whose copy sits in a playlist the kept copy
is not in. Before that file goes, its entry in each such playlist is pointed
at the kept copy, in the same place in the playlist.

Every write adds the kept copy first and takes the old entry out last, so a
failure part way leaves the song in the playlist twice, never not at all.
Smart and read-only playlists are never written: they are reported, and the
copy in them stays. The sync editor's replace-track endpoint does a
rebuild; this keeps the playlist's id, cover and description.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("sync.playlist_entry_move")

SERVERS = ("navidrome", "plex", "jellyfin")


def server_playlists(client: Any) -> List[Any]:
    """every playlist the client sees, [] when it can't say"""
    try:
        return list(client.get_all_playlists() or [])
    except Exception as exc:  # noqa: BLE001 - no listing, nothing to write to
        logger.warning("Could not list playlists for an entry move: %s", exc)
        return []


def playlists_named(playlists: Iterable[Any], titles: Iterable[str]) -> Dict[str, List[Any]]:
    wanted = {str(t) for t in titles}
    found: Dict[str, List[Any]] = {}
    for playlist in playlists:
        title = getattr(playlist, "title", "") or ""
        if title in wanted:
            found.setdefault(title, []).append(playlist)
    return found


def _track_ids(server: str, client: Any, playlist: Any) -> Optional[List[str]]:
    from core.library.playlist_membership import _playlist_track_ids
    try:
        return [str(t) for t in _playlist_track_ids(server, client, playlist)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read playlist %r: %s", getattr(playlist, "title", ""), exc)
        return None


def _navidrome(client: Any, playlist: Any, ids: List[str], old_id: str, new_id: str) -> bool:
    new_ids = [new_id if tid == old_id else tid for tid in ids]
    tracks = [type("T", (), {"ratingKey": tid, "title": ""})() for tid in new_ids]
    # one call rewrites the list by id: the swap lands whole or not at all
    return bool(client.create_playlist(playlist.title, tracks, playlist_id=str(playlist.id)))


def _jellyfin(client: Any, playlist: Any, old_id: str, new_id: str) -> bool:
    import requests
    from core.jellyfin_client import jellyfin_auth_headers
    entries = client._playlist_entries(playlist.id)
    if entries is None:
        return False
    hdr = jellyfin_auth_headers(client.api_key)
    items_url = f"{client.base_url}/Playlists/{playlist.id}/Items"
    for index, (tid, entry_id) in enumerate(list(entries)):
        if tid != old_id:
            continue
        # in first, at the end
        r = requests.post(items_url, params={"Ids": new_id, "UserId": client.user_id},
                          headers=hdr, timeout=30)
        if r.status_code not in (200, 204):
            return False
        fresh = client._playlist_entries(playlist.id) or []
        added = next((eid for t, eid in reversed(fresh) if t == new_id), None)
        if added:
            # then where the old one was
            requests.post(f"{items_url}/{added}/Move/{index}", headers=hdr, timeout=30)
        # out last
        r = requests.delete(items_url, params={"EntryIds": entry_id}, headers=hdr, timeout=30)
        if r.status_code not in (200, 204):
            return False
    return True


def _plex(client: Any, playlist: Any, old_id: str, new_id: str) -> bool:
    server = client.server
    raw = server.fetchItem(int(playlist.id))
    new_item = server.fetchItem(int(new_id))
    if raw is None or new_item is None:
        return False
    for _ in range(50):  # every occurrence, bounded
        items = list(raw.items())
        index = next((i for i, item in enumerate(items) if str(item.ratingKey) == old_id), None)
        if index is None:
            return True
        raw.addItems([new_item])
        raw.reload()
        added = list(raw.items())[-1]
        before = items[index - 1] if index > 0 else None
        try:
            raw.moveItem(added, after=before)
        except Exception as exc:  # noqa: BLE001 - in the playlist, just not in place
            logger.debug("plex move after add failed: %s", exc)
        raw.reload()
        old = next((i for i in raw.items() if str(i.ratingKey) == old_id), None)
        if old is not None:
            raw.removeItems([old])
            raw.reload()
    return False


def move_entries(server: str, client: Any, titles: Iterable[str], old_id: str, new_id: str,
                 playlists: Optional[List[Any]] = None) -> Dict[str, Any]:
    """point ``old_id``'s entries in the playlists named ``titles`` at
    ``new_id``. {'moved': [titles], 'skipped': [(title, why)], 'failed':
    [(title, why)]}. a playlist the old id is no longer in counts as moved"""
    old_id, new_id = str(old_id), str(new_id)
    titles = list(dict.fromkeys(str(t) for t in titles))
    out: Dict[str, Any] = {"moved": [], "skipped": [], "failed": []}
    if server not in SERVERS or client is None:
        out["failed"] = [(t, "no media server to write to") for t in titles]
        return out
    named = playlists_named(playlists if playlists is not None else server_playlists(client), titles)
    for title in titles:
        found = named.get(title) or []
        if not found:
            out["failed"].append((title, "playlist not found on the server"))
            continue
        ok = True
        for playlist in found:
            if getattr(playlist, "readonly", False):
                out["skipped"].append((title, "smart or read-only playlist"))
                ok = False
                break
            ids = _track_ids(server, client, playlist)
            # an empty read is a failed read here: the copy was found in this playlist
            if not ids:
                out["failed"].append((title, "could not read the playlist"))
                ok = False
                break
            if old_id not in ids:
                continue
            try:
                if server == "navidrome":
                    wrote = _navidrome(client, playlist, ids, old_id, new_id)
                elif server == "jellyfin":
                    wrote = _jellyfin(client, playlist, old_id, new_id)
                else:
                    wrote = _plex(client, playlist, old_id, new_id)
            except Exception as exc:  # noqa: BLE001 - this playlist fails, the copy stays
                logger.warning("Playlist entry move in %r failed: %s", title, exc)
                wrote = False
            after = _track_ids(server, client, playlist) if wrote else None
            if not wrote or after is None or old_id in after or new_id not in after:
                out["failed"].append((title, "the server did not take the change"))
                ok = False
                break
        if ok:
            out["moved"].append(title)
            logger.info("Playlist %r now points at %s instead of %s", title, new_id, old_id)
    return out


__all__ = ["move_entries", "playlists_named", "server_playlists"]
