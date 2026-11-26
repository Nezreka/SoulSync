"""artwork and avatar proxies. the server's token is added here and never
reaches the browser.

the artwork proxy stays open to every profile (library thumbs on a jellyfin
server load through it), so it only fetches what is shaped like artwork and
only hands back an image (#2155). the avatar proxy is admin only (it sits
behind the blueprint gate) and never takes a url from the browser: it fetches
the picture the server itself named for that user.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional
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


# ── sizes ───────────────────────────────────────────────────────────────────
#
# the drawer asks for the size it draws (twice over for sharp screens); the
# server rounds up to a few buckets so a poster is fetched and cached once per
# bucket, not once per pixel width.

SIZES = (64, 96, 160, 240, 320, 480, 720)
DEFAULT_WIDTH = 480
SQUARE_KINDS = ("track", "audiobook", "podcast", "artist")


def bucket(width: Any) -> int:
    try:
        w = int(width)
    except (TypeError, ValueError):
        return DEFAULT_WIDTH
    return next((s for s in SIZES if s >= w), SIZES[-1])


def _box(width: int, kind: str) -> tuple:
    """the box plex fills: square covers, tall posters, wide backdrops."""
    if kind == "art":
        return width, round(width * 9 / 16)
    return width, (width if kind in SQUARE_KINDS else round(width * 1.5))


# ── one image from one server ───────────────────────────────────────────────

_TIMEOUT = (3.05, sa._PLEX_TIMEOUT)     # connect, read
# a page of history asks for dozens of posters at once; plex and jellyfin get
# a few at a time, the rest wait their turn instead of timing out together
UPSTREAM_SLOTS = 4
_upstream = threading.BoundedSemaphore(UPSTREAM_SLOTS)
_SLOT_WAIT = 20.0


class Busy(Exception):
    """no upstream slot freed in time: a miss for now, not a missing image."""


def _get(url: str, **kw):
    import requests
    if not _upstream.acquire(timeout=_SLOT_WAIT):
        raise Busy(url)
    try:
        return requests.get(url, timeout=_TIMEOUT, **kw)
    finally:
        _upstream.release()


def _fetch_jellyfin_image(item_id: str, db=None, width: Optional[int] = None, kind: str = "",
                          image_type: str = "Primary") -> Optional[tuple]:
    cfg = sa._jellyfin_config(db)
    if not item_id or not _JELLYFIN_ITEM_ID.match(item_id) or not cfg["base_url"] or not cfg["api_key"]:
        return None
    if image_type not in ("Primary", "Backdrop"):
        return None
    try:
        from core.jellyfin_client import jellyfin_auth_headers
        url = cfg["base_url"].rstrip("/") + "/Items/" + item_id + "/Images/" + image_type
        w, h = _box(bucket(width), kind) if width else (None, 480)
        params = {"maxHeight": h, "quality": 90}
        if w:
            params["maxWidth"] = w
        r = _get(url, params=params, headers=jellyfin_auth_headers(cfg["api_key"]))
        return _image_reply(r)
    except Busy:
        raise
    except Exception:   # noqa: BLE001
        logger.debug("jellyfin image proxy failed for %s", item_id, exc_info=True)
    return None


def _fetch_plex_image(path: str, db=None, width: Optional[int] = None, kind: str = "") -> Optional[tuple]:
    """plex's photo transcoder first (a small image at the size drawn, the way
    plex's own apps ask), the artwork itself when the transcoder won't."""
    if not is_image_path(path):
        return None
    cfg = sa._plex_config(db)
    if not cfg["base_url"] or not cfg["token"]:
        return None
    base = cfg["base_url"].rstrip("/")
    try:
        if width:
            w, h = _box(bucket(width), kind)
            r = _get(base + "/photo/:/transcode",
                     params={"url": path, "width": w, "height": h, "minSize": 1, "upscale": 1,
                             "X-Plex-Token": cfg["token"]})
            got = _image_reply(r)
            if got or r.status_code == 404:
                return got
        return _image_reply(_get(base + path, params={"X-Plex-Token": cfg["token"]}))
    except Busy:
        raise
    except Exception:   # noqa: BLE001
        logger.debug("plex image proxy failed for %s", path, exc_info=True)
    return None


def fetch_image(path: str, db=None, width: Optional[int] = None) -> Optional[tuple]:
    """(bytes, content_type) for one plex or jellyfin image. None on any failure."""
    try:
        if path and str(path).startswith("jf:"):
            return _fetch_jellyfin_image(str(path)[3:], db, width)
        return _fetch_plex_image(str(path or ""), db, width)
    except Busy:
        return None


# ── the fallback chain ──────────────────────────────────────────────────────
#
# an item's own picture is often not there: plex has no still for most
# episodes of a channel style library, a show poster goes missing after an
# agent change, an album has no cover (plex names it ``thumb/-1``). the proxy
# walks the item's family until one answers: the show poster, the season, the
# episode; the album, the track, the artist; a movie's poster, then its art.
# keys are plex rating keys or jellyfin item ids, checked like any path.

_KEY = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_PLEX_ANY_IMAGE = re.compile(
    r"^/library/(metadata|collections)/(\d+)/(thumb|art|banner|poster|clearLogo|composite)(?:/-?\d+)?$")
_ORDER = {
    # role -> the family in the order tried after the item's own path
    "episode": (("gk", "thumb"), ("pk", "thumb"), ("rk", "thumb"), ("gk", "art"), ("rk", "art")),
    "movie": (("rk", "thumb"), ("rk", "art")),
    "track": (("pk", "thumb"), ("rk", "thumb"), ("gk", "thumb")),
    "artist": (("gk", "thumb"), ("pk", "thumb"), ("rk", "thumb")),
    "art": (("rk", "art"), ("gk", "art"), ("pk", "art"), ("gk", "thumb"), ("pk", "thumb"), ("rk", "thumb")),
}
_ROLE_FOR_KIND = {"episode": "episode", "movie": "movie", "clip": "movie", "live": "movie", "other": "movie",
                  "track": "track", "audiobook": "track", "podcast": "track"}


def _plex_path(path: str) -> Optional[str]:
    """a stored plex artwork path made fetchable: as is when it passes the
    whitelist, rebuilt from its id when only the tail is odd (``thumb/-1``)."""
    path = str(path or "")
    if is_image_path(path) and not path.startswith("jf:"):
        return path
    m = _PLEX_ANY_IMAGE.match(path)
    return f"/library/{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else None


def candidates(spec: Dict[str, Any]) -> List[tuple]:
    """the ordered (server, path or id, image type) to try for one picture.
    pure; every entry already passes the whitelist."""
    path = str(spec.get("path") or "")
    server = spec.get("server") or ("jellyfin" if path.startswith("jf:") else "plex")
    kind = str(spec.get("kind") or "")
    role = spec.get("role") or _ROLE_FOR_KIND.get(kind, "movie")
    keys = {k: str(spec.get(k) or "") for k in ("rk", "pk", "gk")}
    out: List[tuple] = []
    seen = set()

    def add(server_, ref, image_type):
        # /library/metadata/7/thumb/123 and /library/metadata/7/thumb are one picture
        m = _PLEX_ANY_IMAGE.match(ref) if server_ == "plex" else None
        ident = (server_, m.group(1, 2, 3) if m else ref, image_type)
        if ident in seen:
            return
        seen.add(ident)
        out.append((server_, ref, image_type))

    if server == "jellyfin":
        own = path[3:] if path.startswith("jf:") else ""
        if own and _JELLYFIN_ITEM_ID.match(own) and role != "art":
            add("jellyfin", own, "Primary")
        for which, typ in _ORDER.get(role, _ORDER["movie"]):
            k = keys[which]
            if k and _JELLYFIN_ITEM_ID.match(k):
                add("jellyfin", k, "Backdrop" if typ == "art" else "Primary")
        if own and _JELLYFIN_ITEM_ID.match(own):
            add("jellyfin", own, "Primary")
        return out
    own = _plex_path(path)
    if own:
        add("plex", own, "")
    for which, typ in _ORDER.get(role, _ORDER["movie"]):
        k = keys[which]
        if k.isdigit():
            add("plex", f"/library/metadata/{k}/{typ}", "")
    return [c for c in out if c[0] == "jellyfin" or is_image_path(c[1])]


def spec_from_args(args) -> Dict[str, Any]:
    """the proxy's query string -> a spec. anything odd is dropped, not trusted."""
    spec: Dict[str, Any] = {"path": str(args.get("path") or "")[:200]}
    if args.get("s") in ("plex", "jellyfin"):
        spec["server"] = args.get("s")
    kind = str(args.get("k") or "")
    if kind in _ROLE_FOR_KIND:
        spec["kind"] = kind
    role = str(args.get("r") or "")
    if role in _ORDER:
        spec["role"] = role
    for k in ("rk", "pk", "gk"):
        v = str(args.get(k) or "")
        if _KEY.match(v):
            spec[k] = v
    return spec


def image_query(server: Optional[str], kind: Optional[str], thumb: Optional[str], rk: Any = None,
                pk: Any = None, gk: Any = None, role: Optional[str] = None) -> Optional[str]:
    """the proxy query for one picture of a row, built server side so the
    browser only adds the width. None when there is nothing to try, and
    always for soulsync's own player: its art is a soulsync url the browser
    loads itself (its ids aren't plex's, so no plex fallback)."""
    from urllib.parse import urlencode
    if server and server not in ("plex", "jellyfin"):
        return None
    spec = {"path": thumb or "", "s": server or "", "k": kind or "", "r": role or "",
            "rk": str(rk or ""), "pk": str(pk or ""), "gk": str(gk or "")}
    if not candidates(spec_from_args(spec)):
        return None
    return urlencode({k: v for k, v in spec.items() if v})


def _plex_key_from_thumb(thumb: Optional[str]) -> Optional[str]:
    m = _PLEX_ANY_IMAGE.match(str(thumb or ""))
    return m.group(2) if m and m.group(1) == "metadata" else None


def row_image(row: Dict[str, Any], role: Optional[str] = None) -> Optional[str]:
    """image_query for a play row (or a live session with its keys)."""
    keys = row.get("keys") or {}
    rk = row.get("rating_key") or keys.get("rating_key")
    pk = row.get("parent_key") or keys.get("parent_key")
    gk = row.get("grandparent_key") or keys.get("grandparent_key")
    return image_query(row.get("server"), row.get("kind"), row.get("thumb"), rk, pk, gk, role)


def resolve(spec: Dict[str, Any], width: Optional[int] = None, db=None) -> Optional[tuple]:
    """(bytes, content type, which candidate answered) walking the chain, or
    None when every one is missing. Busy when the servers are too slow now."""
    role = spec.get("role")
    kind = role if role in ("artist", "art") else str(spec.get("kind") or "")
    busy = False
    for i, (server, ref, image_type) in enumerate(candidates(spec)):
        try:
            if server == "jellyfin":
                got = _fetch_jellyfin_image(ref, db, width, kind, image_type or "Primary")
            else:
                got = _fetch_plex_image(ref, db, width, kind)
        except Busy:
            busy = True
            continue
        if got:
            return got[0], got[1], "own" if i == 0 else f"fallback {i}"
    if busy:
        raise Busy("no slot")
    return None


# ── the disk cache ──────────────────────────────────────────────────────────
#
# one file per (server, picture, size bucket), kept a week and capped in size
# (oldest first out). a miss is remembered in memory for a while so a list of
# rows with no art never hammers the server. identical requests in flight wait
# for the first instead of each asking upstream.

CACHE_TTL = 7 * 86400
MISS_TTL = 15 * 60
BUSY_TTL = 20
CACHE_MAX_BYTES = int(os.environ.get("SOULSYNC_SA_IMAGE_MB") or 256) * 1024 * 1024
_misses: "OrderedDict[str, float]" = OrderedDict()
_MISS_MAX = 5000
_inflight: Dict[str, threading.Event] = {}
_state_lock = threading.Lock()
_disk = {"dir": None, "bytes": None}


def cache_dir() -> Optional[Path]:
    """SOULSYNC_SA_IMAGE_DIR, else storage/server_activity_images."""
    if _disk["dir"] is not None:
        return _disk["dir"]
    d = os.environ.get("SOULSYNC_SA_IMAGE_DIR")
    if not d:
        try:
            from core.settings import config_manager
            d = str(Path(config_manager.base_dir) / "storage" / "server_activity_images")
        except Exception:   # noqa: BLE001
            return None
    _disk["dir"] = Path(d)
    return _disk["dir"]


def use_cache_dir(path: Optional[str]) -> None:
    """tests point the cache at a temp folder (None: back to the default)."""
    with _state_lock:
        _disk.update(dir=Path(path) if path else None, bytes=None)
        _misses.clear()


def cache_key(spec: Dict[str, Any], width: Optional[int]) -> str:
    cfg_bits = ""
    try:
        if (spec.get("server") or ("jellyfin" if str(spec.get("path") or "").startswith("jf:") else "plex")) \
                == "jellyfin":
            cfg_bits = sa._jellyfin_config()["base_url"]
        else:
            cfg_bits = sa._plex_config()["base_url"]
    except Exception:   # noqa: BLE001
        cfg_bits = ""
    chain = "|".join(f"{s}:{r}:{t}" for s, r, t in candidates(spec))
    raw = f"{cfg_bits}|{spec.get('role') or ''}|{chain}|{bucket(width) if width else 0}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file(key: str) -> Optional[Path]:
    d = cache_dir()
    return d / key[:2] / f"{key}.img" if d is not None else None


def _read(key: str, now: float) -> Optional[tuple]:
    f = _file(key)
    if f is None:
        return None
    try:
        st = f.stat()
        if now - st.st_mtime > CACHE_TTL:
            return None
        blob = f.read_bytes()
        head, _, body = blob.partition(b"\n")
        ctype, _, etag = head.decode("ascii", "replace").partition(" ")
        if not body or not ctype.startswith("image/"):
            return None
        return body, ctype, etag
    except (OSError, ValueError):
        return None


def _write(key: str, content: bytes, ctype: str, etag: str) -> None:
    f = _file(key)
    if f is None:
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_bytes(f"{ctype} {etag}\n".encode("ascii") + content)
        os.replace(tmp, f)
    except OSError:
        logger.debug("server activity image cache write failed", exc_info=True)
        return
    with _state_lock:
        if _disk["bytes"] is not None:
            _disk["bytes"] += len(content)
    _maybe_evict()


def _maybe_evict() -> None:
    d = cache_dir()
    if d is None:
        return
    with _state_lock:
        known = _disk["bytes"]
    if known is not None and known <= CACHE_MAX_BYTES:
        return
    try:
        files = [(p.stat().st_mtime, p.stat().st_size, p) for p in d.glob("*/*.img")]
    except OSError:
        return
    total = sum(sz for _, sz, _ in files)
    if total > CACHE_MAX_BYTES:
        for _, sz, p in sorted(files, key=lambda x: x[0]):
            if total <= CACHE_MAX_BYTES * 0.9:
                break
            try:
                p.unlink()
                total -= sz
            except OSError:
                pass
    with _state_lock:
        _disk["bytes"] = total


def _miss_get(key: str, now: float) -> bool:
    with _state_lock:
        until = _misses.get(key)
        if until is None:
            return False
        if until > now:
            return True
        _misses.pop(key, None)
        return False


def _miss_put(key: str, until: float) -> None:
    with _state_lock:
        _misses[key] = until
        _misses.move_to_end(key)
        while len(_misses) > _MISS_MAX:
            _misses.popitem(last=False)


def serve_image(spec: Dict[str, Any], width: Optional[int] = None, db=None,
                now: Optional[float] = None) -> Dict[str, Any]:
    """what the proxy route sends: {"status": 200, content, ctype, etag, source}
    or {"status": 404} (nothing in the chain has a picture) or {"status": 503}
    (the servers are busy; try again soon)."""
    now = time.time() if now is None else now
    if not candidates(spec):
        return {"status": 404}
    key = cache_key(spec, width)
    hit = _read(key, now)
    if hit:
        return {"status": 200, "content": hit[0], "ctype": hit[1], "etag": hit[2], "source": "cache"}
    if _miss_get(key, now):
        return {"status": 404, "source": "miss cache"}
    with _state_lock:
        ev = _inflight.get(key)
        leader = ev is None
        if leader:
            ev = _inflight[key] = threading.Event()
    if not leader:
        ev.wait(_SLOT_WAIT + 2 * sum(_TIMEOUT))
        hit = _read(key, time.time())
        if hit:
            return {"status": 200, "content": hit[0], "ctype": hit[1], "etag": hit[2], "source": "cache"}
        return {"status": 404 if _miss_get(key, time.time()) else 503}
    try:
        try:
            got = resolve(spec, width, db)
        except Busy:
            return {"status": 503}
        if not got:
            _miss_put(key, now + MISS_TTL)
            logger.debug("server activity image: nothing in the chain for %s", spec)
            return {"status": 404}
        content, ctype, source = got
        etag = hashlib.sha1(content).hexdigest()[:20]
        _write(key, content, ctype, etag)
        return {"status": 200, "content": content, "ctype": ctype, "etag": etag, "source": source}
    finally:
        with _state_lock:
            _inflight.pop(key, None)
        ev.set()


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
