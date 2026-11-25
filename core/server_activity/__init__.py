"""server activity: what's playing on plex and jellyfin, who, how and from where,
plus soulsync's own play log (history and stats) built from it.

the package keeps its seams here (server configs, the cached plex connection)
so a test patches one place and every submodule sees it. submodules import this
package and look the seams up at call time.

layout:
  plex.py       plex session xml -> the shared session shape
  jellyfin.py   jellyfin /Sessions json -> the same shape, plus stop / message
  reasons.py    why a stream is converted, in plain words (one table)
  quality.py    labels shared by both servers (4K, HDR10, FLAC 24-bit · 96 kHz)
  geo.py        city / region for remote viewers, cached
  links.py      the soulsync library row for what's playing
  live.py       get_activity, the pulse, the bandwidth series, the sampler
  recorder.py   turns live sessions into plays
  store.py      the server_plays tables
  history.py    history rows from the play log, plex history backfill
  stats.py      the stats sheet
  images.py     artwork and avatar proxies
  settings.py   the drawer's settings
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Dict

from utils.logging_config import get_logger

logger = get_logger("server_activity")

_PLEX_TIMEOUT = 8          # a live view must never hang the ui on a slow server


def _g(obj: Any, attr: str, default: Any = None) -> Any:
    """getattr that never raises."""
    try:
        v = getattr(obj, attr, default)
        return v if v is not None else default
    except Exception:   # noqa: BLE001
        return default


def _first(seq: Any) -> Any:
    try:
        return seq[0] if seq else None
    except Exception:   # noqa: BLE001
        return None


def _plex_config(db=None) -> Dict[str, str]:
    """any working plex connection: the music config first, then the video
    side's. both usually point at the same server and sessions() is server wide."""
    try:
        from core.settings import config_manager
        cfg = config_manager.get_plex_config() or {}
        if cfg.get("base_url") and cfg.get("token"):
            return {"base_url": cfg["base_url"], "token": cfg["token"]}
    except Exception:   # noqa: BLE001
        logger.debug("no music plex config", exc_info=True)
    try:
        from core.video.sources import video_plex_config
        cfg = video_plex_config(db)
        if cfg.get("base_url") and cfg.get("token"):
            return {"base_url": cfg["base_url"], "token": cfg["token"]}
    except Exception:   # noqa: BLE001
        logger.debug("no video plex config", exc_info=True)
    return {"base_url": "", "token": ""}


def _jellyfin_config(db=None) -> Dict[str, str]:
    """any working jellyfin config: music first, then video's."""
    try:
        from core.settings import config_manager
        cfg = config_manager.get_jellyfin_config() or {}
        if cfg.get("base_url") and cfg.get("api_key"):
            return {"base_url": cfg["base_url"], "api_key": cfg["api_key"]}
    except Exception:   # noqa: BLE001
        logger.debug("no music jellyfin config", exc_info=True)
    try:
        from core.video.sources import video_jellyfin_config
        cfg = video_jellyfin_config(db)
        if cfg.get("base_url") and cfg.get("api_key"):
            return {"base_url": cfg["base_url"], "api_key": cfg["api_key"]}
    except Exception:   # noqa: BLE001
        logger.debug("no video jellyfin config", exc_info=True)
    return {"base_url": "", "api_key": ""}


def any_server_configured(db=None) -> bool:
    """cheap: reads config only, never the network."""
    p, j = _plex_config(db), _jellyfin_config(db)
    return bool((p["base_url"] and p["token"]) or (j["base_url"] and j["api_key"]))


_server_cache: Dict[str, Any] = {"srv": None, "at": 0.0, "key": ""}
_server_cache_lock = threading.RLock()


def invalidate_plex_server_cache() -> None:
    """force the next _plex_server call to reconnect."""
    with _server_cache_lock:
        _server_cache.update(srv=None, at=0.0, key="")


def _plex_server(db=None):
    """a connected PlexServer, or None when plex isn't configured or is down.

    a working connection is kept until a call on it fails or the credentials
    change. a failure is remembered for 60 s so a down server isn't hammered."""
    cfg = _plex_config(db)
    if not cfg["base_url"] or not cfg["token"]:
        return None
    key = hashlib.sha256(f"{cfg['base_url']}|{cfg['token']}".encode()).hexdigest()
    with _server_cache_lock:
        now = time.time()
        if _server_cache["key"] == key:
            if _server_cache["srv"] is not None:
                return _server_cache["srv"]
            if 0 <= now - _server_cache["at"] < 60:
                return None
        try:
            from plexapi.server import PlexServer
            srv = PlexServer(cfg["base_url"], cfg["token"], timeout=_PLEX_TIMEOUT)
            _server_cache.update(srv=srv, at=time.time(), key=key)
            return srv
        except Exception:   # noqa: BLE001 - unreachable server is an expected state
            logger.debug("plex connect failed for activity", exc_info=True)
            _server_cache.update(srv=None, at=time.time(), key=key)
            return None


def _mark_plex_unreachable(srv):
    # a late failure from an old credential's client must not poison a newer one.
    with _server_cache_lock:
        if _server_cache["srv"] is srv:
            _server_cache.update(srv=None, at=time.time())


# public api. imported last: the submodules look the seams above up through
# this package at call time.
from core.server_activity.images import (  # noqa: E402
    _fetch_jellyfin_image,  # noqa: F401 - #1159's test reaches for it here
    fetch_avatar,
    fetch_image,
    is_image_path,
    serve_image,
    spec_from_args,
)
from core.server_activity.live import (  # noqa: E402
    get_activity,
    message_session,
    run_sampler_forever,
    stop_session,
)
from core.server_activity.history import get_history, get_history_group  # noqa: E402
from core.server_activity.stats import get_stats  # noqa: E402
from core.server_activity.settings import get_settings, save_settings  # noqa: E402
from core.server_activity.store import clear_play_log  # noqa: E402

__all__ = [
    "any_server_configured", "clear_play_log", "fetch_avatar", "fetch_image",
    "get_activity", "get_history", "get_history_group", "get_settings", "get_stats",
    "invalidate_plex_server_cache", "is_image_path", "message_session",
    "run_sampler_forever", "save_settings", "serve_image", "spec_from_args", "stop_session",
]
