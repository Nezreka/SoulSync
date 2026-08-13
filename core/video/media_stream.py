"""Where to fetch a library item's bytes from, for in-browser playback.

Movie night originally streamed off the resolved filesystem path, which quietly
assumed SoulSync can *see* the file. On a real install it often cannot: Boulder's
Plex reports files under **eleven** mount roots (/mnt/easystore1-5,
/mnt/plex_20tb, /mnt/seagate_expansion1-2, /mnt/20tb_drive2, /mnt/md2 — 120,821
files) while SoulSync knows a single SMB share. Under 10% of the library was
reachable, so the watch party answered "that file is in your library but this
server can't reach it" for almost everything.

The media server already solves this. It knows exactly where every file lives —
it is the thing that reported the path in the first place — and SoulSync already
holds its URL, its token, and each item's ``server_id``. So the honest order is:

  1. a LOCAL file, when the path happens to resolve here — no proxy, no load on
     the media server, and it still works if Plex/Jellyfin is down;
  2. otherwise the MEDIA SERVER, proxied through SoulSync so the browser never
     sees the server's token.

Only step 2 needed building; step 1 is the path resolver that was already there.

Note what this does NOT fix: the codec question. Both routes serve the ORIGINAL
file, so an HEVC/AC3 copy still won't decode in a browser — see
:mod:`core.video.direct_play`. Asking the media server to TRANSCODE would fix
that too and is the natural next step; it is deliberately not done here, because
transcoding is a resource decision the user should opt into rather than something
a chat room silently starts on their server.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("video.media_stream")


def jellyfin_stream_url(base_url: Any, item_id: Any, api_key: Any) -> str:
    """The original-file stream for a Jellyfin item.

    ``static=true`` is the important part: it tells Jellyfin to hand over the
    file as stored rather than starting a transcode, which keeps Range requests
    honest (a transcoded stream has no stable byte offsets, so seeking — how a
    latecomer joins a showing already in progress — would break)."""
    base = str(base_url or "").rstrip("/")
    return "%s/Videos/%s/stream?static=true&api_key=%s" % (base, item_id, api_key)


def plex_part_url(base_url: Any, part_key: Any, token: Any) -> str:
    """The original-file stream for a Plex part key ('/library/parts/…/file.mkv')."""
    base = str(base_url or "").rstrip("/")
    key = str(part_key or "")
    if not key.startswith("/"):
        key = "/" + key
    joiner = "&" if "?" in key else "?"
    return "%s%s%sX-Plex-Token=%s" % (base, key, joiner, token)


def _plex_part_key(base_url: str, token: str, rating_key: Any,
                   want_size: Any = None, want_path: Any = None) -> Optional[str]:
    """The key of the Plex part that matches the file SoulSync judged.

    Taking the first part is wrong whenever an item has several versions, which
    is not an edge case: 2,199 movies in Boulder's library carry more than one
    media file (up to five). ``video_stored_file_path`` deliberately picks the
    LARGEST, and the codec verdict is computed from that row — so streaming a
    different version would mean judging one file and playing another, and the
    "this is HEVC, your browser won't like it" warning would describe a copy
    nobody is watching.

    Matched on size first (exact, and the identity proof the path resolver
    already trusts), then on the stored path, then first-part as a last resort
    so a mismatch degrades to playing *something* rather than nothing."""
    try:
        from plexapi.server import PlexServer
        item = PlexServer(base_url, token, timeout=10).fetchItem(int(rating_key))
        parts = [p for m in (getattr(item, "media", None) or [])
                 for p in (getattr(m, "parts", None) or []) if getattr(p, "key", None)]
        if not parts:
            return None
        try:
            want_size = int(want_size) if want_size else None
        except (TypeError, ValueError):
            want_size = None
        if want_size:
            for p in parts:
                if int(getattr(p, "size", 0) or 0) == want_size:
                    return p.key
        if want_path:
            tail = str(want_path).replace("\\", "/").rsplit("/", 1)[-1]
            for p in parts:
                if str(getattr(p, "file", "") or "").replace("\\", "/").endswith(tail):
                    return p.key
        if len(parts) > 1:
            logger.info("plex item %s has %d parts and none matched the stored file — "
                        "streaming the first", rating_key, len(parts))
        return parts[0].key
    except Exception:   # noqa: BLE001 - a lookup failure is 'no stream', not a 500
        logger.debug("plex part lookup failed for ratingKey %s", rating_key, exc_info=True)
    return None


def server_stream_target(db, kind: str, tmdb_id: Any, season: Any = None,
                         episode: Any = None, want_size: Any = None,
                         want_path: Any = None) -> Optional[Dict[str, Any]]:
    """Where the MEDIA SERVER will serve this item's bytes from.

    Returns ``{"url", "server"}`` or None. The URL carries the server's
    credentials, so it is used server-side only — the endpoint proxies it and
    never hands it to a browser."""
    from core.video.sources import video_jellyfin_config, video_plex_config
    try:
        ref = db.video_server_ref(kind, tmdb_id=tmdb_id, season=season, episode=episode)
    except Exception:   # noqa: BLE001
        logger.debug("server ref lookup failed", exc_info=True)
        return None
    if not ref or not ref.get("server_id"):
        return None
    src = str(ref.get("server_source") or "").lower()

    if src == "plex":
        cfg = video_plex_config(db)
        if not cfg.get("base_url") or not cfg.get("token"):
            return None
        part = _plex_part_key(cfg["base_url"], cfg["token"], ref["server_id"],
                              want_size=want_size, want_path=want_path)
        if not part:
            return None
        return {"url": plex_part_url(cfg["base_url"], part, cfg["token"]), "server": "plex"}

    if src in ("jellyfin", "emby"):
        cfg = video_jellyfin_config(db)
        if not cfg.get("base_url") or not cfg.get("api_key"):
            return None
        return {"url": jellyfin_stream_url(cfg["base_url"], ref["server_id"], cfg["api_key"]),
                "server": src}
    return None


__all__ = ["server_stream_target", "jellyfin_stream_url", "plex_part_url"]
