"""artwork and avatar proxies. the server's token is added here and never
reaches the browser.

the artwork proxy stays open to every profile (library thumbs on a jellyfin
server load through it), so it only fetches what is shaped like artwork and
only hands back an image (#2155). the avatar proxy is admin only (it sits
behind the blueprint gate) and never takes a url from the browser: it fetches
the picture the server itself named for that user.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Dict, Optional
from urllib.parse import urlparse

import core.server_activity as sa

logger = sa.logger

_PLEX_IMAGE_PATH = re.compile(
    r"^/library/(?:metadata|collections)/\d+/(?:thumb|art|banner|poster|clearLogo|composite)(?:/\d+)?$")
_JELLYFIN_ITEM_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_USER_KEY = re.compile(r"^(plex|jellyfin):([A-Za-z0-9-]{1,64})$")


def _kids(root):
    """children of an xml reply. never `or` an Element: one with no children is falsy."""
    return list(root) if root is not None else []


def is_image_path(path: str) -> bool:
    """True for a path the image proxy may fetch: a plex artwork path or a
    jellyfin item id (``jf:<id>``)."""
    path = str(path or "")
    if path.startswith("jf:"):
        return bool(_JELLYFIN_ITEM_ID.match(path[3:]))
    return bool(_PLEX_IMAGE_PATH.match(path))


def _image_reply(r) -> Optional[tuple]:
    """(bytes, content type) when the upstream answered with an image."""
    ctype = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip().lower()
    if r.status_code == 200 and r.content and ctype.startswith("image/"):
        return r.content, ctype
    return None


def _fetch_jellyfin_image(item_id: str, db=None) -> Optional[tuple]:
    cfg = sa._jellyfin_config(db)
    if not item_id or not _JELLYFIN_ITEM_ID.match(item_id) or not cfg["base_url"] or not cfg["api_key"]:
        return None
    try:
        import requests
        url = cfg["base_url"].rstrip("/") + "/Items/" + item_id + "/Images/Primary"
        r = requests.get(url, params={"api_key": cfg["api_key"], "maxHeight": 480}, timeout=sa._PLEX_TIMEOUT)
        return _image_reply(r)
    except Exception:   # noqa: BLE001
        logger.debug("jellyfin image proxy failed for %s", item_id, exc_info=True)
    return None


def fetch_image(path: str, db=None) -> Optional[tuple]:
    """(bytes, content_type) for a plex or jellyfin image. None on any failure."""
    if path and str(path).startswith("jf:"):
        return _fetch_jellyfin_image(str(path)[3:], db)
    if not is_image_path(path):
        return None
    cfg = sa._plex_config(db)
    if not cfg["base_url"] or not cfg["token"]:
        return None
    try:
        import requests
        url = cfg["base_url"].rstrip("/") + path
        r = requests.get(url, params={"X-Plex-Token": cfg["token"]}, timeout=sa._PLEX_TIMEOUT)
        return _image_reply(r)
    except Exception:   # noqa: BLE001
        logger.debug("plex image proxy failed for %s", path, exc_info=True)
    return None


# ── avatars ─────────────────────────────────────────────────────────────────

def is_plex_avatar_url(url: str) -> bool:
    """plex names a user's picture on plex.tv. nothing else is fetched."""
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "plex.tv" or host.endswith(".plex.tv")) and not u.username


_accounts: Dict[str, object] = {"at": 0.0, "thumbs": {}}
_accounts_lock = threading.Lock()


def _plex_account_thumb(user_id: str) -> Optional[str]:
    """a user not seen live since the start (history rows): plex's account list
    names their picture. cached an hour."""
    now = time.time()
    with _accounts_lock:
        if now - float(_accounts["at"]) < 3600:
            return _accounts["thumbs"].get(user_id)   # type: ignore[union-attr]
    thumbs: Dict[str, str] = {}
    srv = sa._plex_server()
    if srv is not None:
        try:
            for a in _kids(srv.query("/accounts")):
                if a.get("thumb"):
                    thumbs[a.get("id", "")] = a.get("thumb")
        except Exception:   # noqa: BLE001
            logger.debug("plex accounts for avatars failed", exc_info=True)
    with _accounts_lock:
        _accounts.update(at=now, thumbs=thumbs)
    return thumbs.get(user_id)


def fetch_avatar(user_key: str, db=None) -> Optional[tuple]:
    """(bytes, content type) for ``plex:<id>`` or ``jellyfin:<id>``."""
    m = _USER_KEY.match(str(user_key or ""))
    if not m:
        return None
    server, uid = m.group(1), m.group(2)
    try:
        import requests
        if server == "plex":
            from core.server_activity.live import avatar_url
            url = avatar_url(user_key) or _plex_account_thumb(uid)
            if not url or not is_plex_avatar_url(url):
                return None
            return _image_reply(requests.get(url, timeout=sa._PLEX_TIMEOUT))
        cfg = sa._jellyfin_config(db)
        if not cfg["base_url"] or not cfg["api_key"]:
            return None
        from core.jellyfin_client import jellyfin_auth_headers
        headers = jellyfin_auth_headers(cfg["api_key"])
        base = cfg["base_url"].rstrip("/")
        got = _image_reply(requests.get(base + "/UserImage", params={"userId": uid}, headers=headers,
                                        timeout=sa._PLEX_TIMEOUT))
        if got:
            return got
        # servers before 10.9
        return _image_reply(requests.get(f"{base}/Users/{uid}/Images/Primary", headers=headers,
                                         timeout=sa._PLEX_TIMEOUT))
    except Exception:   # noqa: BLE001
        logger.debug("avatar proxy failed for %s", user_key, exc_info=True)
    return None
