"""Write Tags' "Sync to Plex/Jellyfin": the media server sees the new tags now.

3.6.1's tag preview had the option (web_server ``_sync_tracks_to_server``):
Plex got each track's title/artist/album/year pushed, Jellyfin a library
scan, Navidrome nothing (it reads tag changes itself). Library v2's Write
Tags dropped it, so a Plex user who fixed titles kept seeing the old ones
until Plex's own refresh (third #1062 review G15-5).

The values are the ones the tag writer just wrote (the effective metadata,
hand edits included), and a Plex track is found through its lib2 server
mapping, not a legacy id. Never raises; the counts go in the job result.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any, Callable, Dict, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.server_tag_sync")


def _active_server_and_client():
    from core.library.playlist_membership import _active_server_and_client as resolve
    return resolve()


def active_server_type() -> Optional[str]:
    """the active media server's name, or None when there is none"""
    try:
        server, _client = _active_server_and_client()
        return server or None
    except Exception:  # noqa: BLE001
        return None


def sync_tracks_to_server(database, track_ids: Iterable[int], *,
                          resolve: Optional[Callable[[], tuple]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"server": None, "synced": 0, "failed": 0, "skipped": 0, "errors": []}
    ids = [int(t) for t in dict.fromkeys(track_ids)]
    if not ids:
        return result
    try:
        server, client = (resolve or _active_server_and_client)()
    except Exception as exc:  # noqa: BLE001
        server, client = None, None
        logger.warning("no media server for tag sync: %s", exc)
    result["server"] = server
    if not server or client is None:
        result["skipped"] = len(ids)
        result["errors"].append({"error": "No media server is connected"})
        return result
    if server == "navidrome":
        # reads tag changes on its own
        result["synced"] = len(ids)
        return result
    if server == "plex":
        _sync_plex(database, ids, client, result)
        return result
    # jellyfin (and emby): one library scan picks up every written file
    try:
        if client.trigger_library_scan():
            result["synced"] = len(ids)
        else:
            result["failed"] = len(ids)
            result["errors"].append({"error": f"{server.title()} library scan failed"})
    except Exception as exc:  # noqa: BLE001
        result["failed"] = len(ids)
        result["errors"].append({"error": f"{server.title()} scan error: {exc}"})
    return result


def _sync_plex(database, ids, client, result) -> None:
    from core.library2.retag import _db_data_for_row, _track_rows
    for track_id in ids:
        try:
            with closing(database._get_connection()) as conn:
                # review 4 R4-B-10: the shared library's keys only. an own
                # library's ratingKey came from that profile's scan, possibly
                # of another Plex server, and on the active one it can name an
                # unrelated track (whose title and artist the edit rewrote).
                # the written file tags reach Plex on its next scan anyway
                keys = [r[0] for r in conn.execute(
                    "SELECT server_id FROM lib2_media_server_mappings "
                    "WHERE entity_type='track' AND entity_id=? AND server_source='plex' "
                    "AND COALESCE(server_library_id,'') NOT LIKE 'own:%' "
                    "ORDER BY last_seen_at DESC", (track_id,))]
                rows = _track_rows(conn, [track_id])
                data = _db_data_for_row(conn, rows[0]) if rows and keys else None
            if not data:
                # never seen on Plex: nothing there to update
                result["skipped"] += 1
                continue
            metadata = {k: v for k, v in (("title", data.get("title")),
                                          ("artist", data.get("artist_name")),
                                          ("album", data.get("album_title")),
                                          ("year", data.get("year"))) if v}
            if not metadata:
                result["skipped"] += 1
                continue
            # one track can sit in two Plex libraries (a 4K / hi-res split)
            if all([client.update_track_metadata(str(key), metadata) for key in keys]):
                result["synced"] += 1
            else:
                result["failed"] += 1
                result["errors"].append({"track_id": track_id, "error": "Plex update returned false"})
        except Exception as exc:  # noqa: BLE001
            result["failed"] += 1
            result["errors"].append({"track_id": track_id, "error": str(exc)})
