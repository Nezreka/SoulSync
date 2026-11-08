"""each linked profile's own video watch state, read from its own Plex account (#1941).

the library scan reads watch state once, through the server account, into the
shared columns (the server owner's). after a Plex scan this module reads the
same mapped libraries once more per profile linked to its own Plex account,
with that account's stored server token, and keeps the result in
video_watch_state. core.video.watch_scope decides who reads what.

Plex is asked only for what the account has touched (lastViewedAt), not for
the whole library, so a pass costs a couple of searches per profile. a full
or deep scan reads everything touched and replaces the profile's rows; an
incremental scan reads what was viewed since the last scan and merges it.

the same connection is how a linked profile's watched click is played on its
own account (member_source).
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("video_member_watch")

_KIND_FOR_MEDIA = {"movie": ("movie",), "show": ("episode",), "all": ("movie", "episode")}
_MEDIA_FOR_KIND = {"movie": "movie", "episode": "show"}

# one PlexServer per member token, reused for a minute: building one is an
# HTTP round trip, and a click or a pass over several profiles needs it again
_srv_cache: Dict[str, Tuple[Any, float]] = {}
_srv_lock = threading.Lock()
_SRV_TTL = 60


def linked_profiles() -> List[Tuple[int, str]]:
    """(profile id, server token) for every profile linked to its own Plex account."""
    from core.media_accounts import plex_linked_profiles
    return plex_linked_profiles()


def _plex_server(base_url: str, token: str):
    from plexapi.server import PlexServer

    from core.video.sources import PLEX_CONNECT_TIMEOUT, PLEX_SCAN_TIMEOUT
    key = hashlib.sha256(f"{base_url}|{token}".encode()).hexdigest()
    with _srv_lock:
        hit = _srv_cache.get(key)
        if hit and 0 <= time.time() - hit[1] < _SRV_TTL:
            return hit[0]
    srv = PlexServer(base_url, token, timeout=(PLEX_CONNECT_TIMEOUT, PLEX_CONNECT_TIMEOUT))
    srv._timeout = (PLEX_CONNECT_TIMEOUT, PLEX_SCAN_TIMEOUT)
    with _srv_lock:
        _srv_cache[key] = (srv, time.time())
    return srv


def member_source(token: str, movies_lib=None, tv_lib=None, db=None):
    """a PlexVideoSource acting as the token's own user on the video server,
    or None when the video server isn't Plex or can't be reached."""
    from core.video.sources import PlexVideoSource, resolve_video_server, video_plex_config
    if not token or resolve_video_server(db) != "plex":
        return None
    base_url = (video_plex_config(db) or {}).get("base_url")
    if not base_url:
        return None
    try:
        srv = _plex_server(base_url, token)
    except Exception as e:   # noqa: BLE001
        logger.warning("member watch: Plex connect failed: %s", e)
        return None
    return PlexVideoSource(srv, movies_lib=movies_lib, tv_lib=tv_lib)


def _rows_for(db, items: List[dict], server_source: str) -> List[tuple]:
    """the server's watch rows matched to library rows; titles the library
    doesn't hold are dropped (the next scan brings them in)."""
    out = []
    for kind in ("movie", "episode"):
        mine = [it for it in items if it["kind"] == kind]
        ids = db.watch_item_ids(kind, server_source, [it["server_id"] for it in mine])
        for it in mine:
            item_id = ids.get(it["server_id"])
            if item_id is not None:
                out.append((kind, item_id, it["play_count"], it["last_viewed_at"],
                            it["view_offset_ms"]))
    return out


def sync_member_watch_state(db, scan_source, *, since=None, media_type="all",
                            profiles: Optional[List[Tuple[int, str]]] = None) -> dict:
    """after a Plex scan: read every linked profile's own watch state from the
    libraries ``scan_source`` reads, and store it. ``since`` None reads all of
    it and replaces the profile's rows for those kinds; a time reads only what
    was viewed after it and merges. a profile with no rows of a kind yet is
    read in full either way. one profile's failure never stops the others,
    and a read that fails leaves the rows it was reading as they were.
    returns {profiles, rows, failed}."""
    out = {"profiles": 0, "rows": 0, "failed": 0}
    if getattr(scan_source, "server_name", None) != "plex":
        return out
    media_type = media_type if media_type in _KIND_FOR_MEDIA else "all"
    profiles = linked_profiles() if profiles is None else profiles
    movies_lib = getattr(scan_source, "_movies_lib", None)
    tv_lib = getattr(scan_source, "_tv_lib", None)
    for pid, token in profiles:
        try:
            src = member_source(token, movies_lib=movies_lib, tv_lib=tv_lib, db=db)
            if src is None:
                out["failed"] += 1
                continue
            # a kind the profile has nothing stored for yet (just linked, or
            # the first scan after this shipped) is read in full even on an
            # incremental scan: a delta alone would leave out everything it
            # watched before then. one that has simply never watched a movie
            # costs one empty search each time, nothing more.
            n = 0
            for kind in _KIND_FOR_MEDIA[media_type]:
                window = since if since is not None and \
                    db.has_profile_watch_state(pid, (kind,)) else None
                items = list(src.iter_watch_state(since=window, media_type=_MEDIA_FOR_KIND[kind]))
                rows = _rows_for(db, items, "plex")
                db.save_profile_watch_state(pid, rows, replace_kinds=() if window else (kind,))
                n += len(rows)
            out["rows"] += n
            out["profiles"] += 1
        except Exception as e:   # noqa: BLE001 - one member never sinks the scan
            out["failed"] += 1
            logger.warning("member watch: reading profile %s's watch state failed: %s", pid, e)
    if profiles:
        logger.info("Member watch state: %d profile(s), %d row(s), %d failed",
                    out["profiles"], out["rows"], out["failed"])
    return out
