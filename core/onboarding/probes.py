"""connection checks that never touch the config.

core.connection_test writes the candidate settings into the live config,
tests, then writes the old ones back. fine for the settings page, wrong for
a setup flow that has to stay save-free (and for its developer preview). so
these talk to each service directly with the values they're handed.

every probe returns {"ok", "message"} plus whatever it learned on the way
("detail"). no probe raises, a dead service is just ok False.
"""

from __future__ import annotations

import hashlib
import secrets
from collections import Counter
from typing import Any, Callable, Optional

import requests

from utils.logging_config import get_logger

logger = get_logger("onboarding.probes")

TIMEOUT = 8
_JWT_PREFIX = "eyJ"


def _base(url: Any) -> str:
    return str(url or "").strip().rstrip("/")


def _result(ok: bool, message: str, **detail) -> dict:
    return {"ok": bool(ok), "message": message, "detail": detail}


def _unreachable(name: str, url: str) -> dict:
    return _result(False, f"Can't reach {name} at {url}" if url else f"Enter {name}'s address")


def _subsonic_params(username: str, password: str) -> dict:
    salt = secrets.token_hex(6)
    token = hashlib.md5((password + salt).encode()).hexdigest()  # noqa: S324 - the subsonic protocol
    return {"u": username, "t": token, "s": salt, "v": "1.16.1", "c": "SoulSync", "f": "json"}


def _jellyfin_headers(api_key: str) -> dict:
    from core.jellyfin_client import jellyfin_auth_headers
    return jellyfin_auth_headers(api_key)


class Prober:
    """http and the docker url fix are injected so tests run offline"""

    def __init__(self, http=requests, resolve_url: Optional[Callable[[str], str]] = None):
        self.http = http
        self.resolve_url = resolve_url or (lambda url: url)

    def _url(self, url: Any) -> str:
        base = _base(url)
        return self.resolve_url(base) if base else base

    # ---- music sources ---------------------------------------------------

    def slskd(self, cfg: dict) -> dict:
        url = self._url(cfg.get("slskd_url") or cfg.get("url"))
        key = str(cfg.get("api_key") or "").strip()
        if not url:
            return _unreachable("slskd", url)
        try:
            r = self.http.get(f"{url}/api/v0/application", headers={"X-API-Key": key}, timeout=TIMEOUT)
        except Exception:
            return _unreachable("slskd", _base(cfg.get("slskd_url") or cfg.get("url")))
        if r.status_code in (401, 403):
            return _result(False, "slskd answered but refused that API key")
        if r.status_code != 200:
            return _result(False, f"slskd answered with HTTP {r.status_code}")
        version = ""
        try:
            version = str((r.json() or {}).get("version", {}).get("full") or "")
        except Exception as exc:  # noqa: BLE001 - the name is a nicety, the probe still passed
            logger.debug("probe detail unreadable: %s", exc)
        return _result(True, "Connected to slskd", version=version)

    def slskd_shared_files(self, cfg: dict) -> Optional[int]:
        """how many files slskd shares, None when it can't be told. newer slskd
        reports the count in its application state, older ones only list the
        shares themselves"""
        url = self._url(cfg.get("slskd_url") or cfg.get("url"))
        if not url:
            return None
        headers = {"X-API-Key": str(cfg.get("api_key") or "").strip()}
        try:
            r = self.http.get(f"{url}/api/v0/application", headers=headers, timeout=TIMEOUT)
            shares = (r.json() or {}).get("shares") if r.status_code == 200 else None
            if isinstance(shares, dict) and isinstance(shares.get("files"), int):
                return shares["files"]
        except Exception as exc:  # noqa: BLE001 - fall through to the share list
            logger.debug("slskd application state unreadable: %s", exc)
        try:
            r = self.http.get(f"{url}/api/v0/shares", headers=headers, timeout=TIMEOUT)
            if r.status_code != 200:
                return None
            body = r.json()
        except Exception as exc:  # noqa: BLE001
            logger.debug("slskd share list unreadable: %s", exc)
            return None
        # {"local": [{"localPath", "files", ...}]}, keyed by host
        groups = body.values() if isinstance(body, dict) else [body]
        total, known = 0, False
        for group in groups:
            for share in group if isinstance(group, list) else []:
                if isinstance(share, dict) and isinstance(share.get("files"), int):
                    total += share["files"]
                    known = True
        return total if known else None

    # ---- media servers ---------------------------------------------------

    def plex(self, cfg: dict) -> dict:
        url = self._url(cfg.get("base_url"))
        token = str(cfg.get("token") or "").strip()
        if not url:
            return _unreachable("Plex", url)
        try:
            r = self.http.get(f"{url}/", headers={"X-Plex-Token": token, "Accept": "application/json"},
                              timeout=TIMEOUT)
        except Exception:
            return _unreachable("Plex", _base(cfg.get("base_url")))
        if r.status_code == 401:
            return _result(False, "Plex refused that sign-in")
        if r.status_code != 200:
            return _result(False, f"Plex answered with HTTP {r.status_code}")
        name = ""
        try:
            name = str((r.json() or {}).get("MediaContainer", {}).get("friendlyName") or "")
        except Exception as exc:  # noqa: BLE001 - the name is a nicety, the probe still passed
            logger.debug("probe detail unreadable: %s", exc)
        return _result(True, f"Connected to {name}" if name else "Connected to Plex", server_name=name)

    def jellyfin(self, cfg: dict) -> dict:
        url = self._url(cfg.get("base_url"))
        key = str(cfg.get("api_key") or "").strip()
        if not url:
            return _unreachable("Jellyfin", url)
        try:
            r = self.http.get(f"{url}/System/Info", headers=_jellyfin_headers(key), timeout=TIMEOUT)
        except Exception:
            return _unreachable("Jellyfin", _base(cfg.get("base_url")))
        if r.status_code in (401, 403):
            return _result(False, "Jellyfin refused that API key")
        if r.status_code != 200:
            return _result(False, f"Jellyfin answered with HTTP {r.status_code}")
        name = ""
        try:
            name = str((r.json() or {}).get("ServerName") or "")
        except Exception as exc:  # noqa: BLE001 - the name is a nicety, the probe still passed
            logger.debug("probe detail unreadable: %s", exc)
        return _result(True, f"Connected to {name}" if name else "Connected to Jellyfin", server_name=name)

    def navidrome(self, cfg: dict) -> dict:
        url = self._url(cfg.get("base_url"))
        user = str(cfg.get("username") or "").strip()
        password = str(cfg.get("password") or "")
        if not url:
            return _unreachable("Navidrome", url)
        try:
            r = self.http.get(f"{url}/rest/ping", params=_subsonic_params(user, password), timeout=TIMEOUT)
            body = (r.json() or {}).get("subsonic-response", {})
        except Exception:
            return _unreachable("Navidrome", _base(cfg.get("base_url")))
        if body.get("status") != "ok":
            return _result(False, "Navidrome refused that username or password")
        return _result(True, "Connected to Navidrome")

    # ---- video and indexers ----------------------------------------------

    def prowlarr(self, cfg: dict) -> dict:
        url = self._url(cfg.get("url"))
        key = str(cfg.get("api_key") or "").strip()
        if not url:
            return _unreachable("Prowlarr", url)
        headers = {"X-Api-Key": key}
        try:
            r = self.http.get(f"{url}/api/v1/indexer", headers=headers, timeout=TIMEOUT)
        except Exception:
            return _unreachable("Prowlarr", _base(cfg.get("url")))
        if r.status_code == 401:
            return _result(False, "Prowlarr refused that API key")
        if r.status_code != 200:
            return _result(False, f"Prowlarr answered with HTTP {r.status_code}")
        try:
            indexers = [i for i in (r.json() or []) if i.get("enable", True)]
        except Exception:
            indexers = []
        if not indexers:
            return _result(True, "Connected, but Prowlarr has no indexers yet", indexers=0)
        n = len(indexers)
        return _result(True, f"Connected, {n} indexer{'s' if n != 1 else ''}", indexers=n)

    def qbittorrent(self, cfg: dict) -> dict:
        url = self._url(cfg.get("url"))
        if not url:
            return _unreachable("qBittorrent", url)
        try:
            r = self.http.post(f"{url}/api/v2/auth/login",
                               data={"username": cfg.get("username") or "", "password": cfg.get("password") or ""},
                               headers={"Referer": url}, timeout=TIMEOUT)
        except Exception:
            return _unreachable("qBittorrent", _base(cfg.get("url")))
        if r.status_code == 403:
            return _result(False, "qBittorrent has blocked this address after failed logins")
        if r.status_code != 200 or "ok" not in (r.text or "").strip().lower():
            return _result(False, "qBittorrent refused that username or password")
        return _result(True, "Connected to qBittorrent")

    def sabnzbd(self, cfg: dict) -> dict:
        url = self._url(cfg.get("url"))
        if not url:
            return _unreachable("SABnzbd", url)
        try:
            r = self.http.get(f"{url}/api", params={"mode": "queue", "output": "json",
                                                    "apikey": cfg.get("api_key") or ""}, timeout=TIMEOUT)
            body = r.json() or {}
        except Exception:
            return _unreachable("SABnzbd", _base(cfg.get("url")))
        if body.get("error") or body.get("status") is False:
            return _result(False, "SABnzbd refused that API key")
        return _result(True, "Connected to SABnzbd")

    def tmdb(self, cfg: dict) -> dict:
        key = str(cfg.get("api_key") or "").strip()
        if not key:
            return _result(False, "Paste your TMDB API key")
        if key.startswith(_JWT_PREFIX):
            return _result(False, "That's the read access token. Paste the shorter API key above it")
        try:
            r = self.http.get("https://api.themoviedb.org/3/configuration",
                              params={"api_key": key}, timeout=TIMEOUT)
        except Exception:
            return _result(False, "Can't reach TMDB right now")
        if r.status_code == 401:
            return _result(False, "TMDB refused that key")
        if r.status_code != 200:
            return _result(False, f"TMDB answered with HTTP {r.status_code}")
        return _result(True, "TMDB key works")

    # ---- accounts --------------------------------------------------------

    def listenbrainz(self, cfg: dict) -> dict:
        token = str(cfg.get("token") or "").strip()
        if not token:
            return _result(False, "Paste your ListenBrainz token")
        try:
            r = self.http.get("https://api.listenbrainz.org/1/validate-token",
                              headers={"Authorization": f"Token {token}"}, timeout=TIMEOUT)
            body = r.json() or {}
        except Exception:
            return _result(False, "Can't reach ListenBrainz right now")
        if not body.get("valid"):
            return _result(False, "ListenBrainz says that token isn't valid")
        user = str(body.get("user_name") or "")
        return _result(True, f"Linked as {user}" if user else "Linked", user=user)

    # ---- libraries -------------------------------------------------------

    def libraries(self, server: str, cfg: dict) -> dict:
        """{"music": [...], "movies": [...], "tv": [...]}, each {id, title,
        count, locations}. empty lists when the server can't be read"""
        empty = {"music": [], "movies": [], "tv": []}
        try:
            if server == "plex":
                return self._plex_libraries(cfg)
            if server == "jellyfin":
                return self._jellyfin_libraries(cfg)
            if server == "navidrome":
                return self._navidrome_libraries(cfg)
        except Exception:
            return empty
        return empty

    def _plex_libraries(self, cfg: dict) -> dict:
        url = self._url(cfg.get("base_url"))
        headers = {"X-Plex-Token": str(cfg.get("token") or ""), "Accept": "application/json"}
        r = self.http.get(f"{url}/library/sections", headers=headers, timeout=TIMEOUT)
        dirs = (r.json() or {}).get("MediaContainer", {}).get("Directory") or []
        kinds = {"artist": ("music", 9), "movie": ("movies", 1), "show": ("tv", 2)}
        out = {"music": [], "movies": [], "tv": []}
        for d in dirs:
            kind = kinds.get(d.get("type"))
            if not kind:
                continue
            bucket, item_type = kind
            count = None
            try:
                c = self.http.get(f"{url}/library/sections/{d.get('key')}/all", headers=headers,
                                  params={"type": item_type, "X-Plex-Container-Start": 0,
                                          "X-Plex-Container-Size": 0}, timeout=TIMEOUT)
                count = int((c.json() or {}).get("MediaContainer", {}).get("totalSize"))
            except Exception:
                count = None
            out[bucket].append({
                "id": str(d.get("key")),
                "title": str(d.get("title") or ""),
                "count": count,
                "locations": [str(loc.get("path")) for loc in (d.get("Location") or []) if loc.get("path")],
            })
        return out

    def _jellyfin_libraries(self, cfg: dict) -> dict:
        url = self._url(cfg.get("base_url"))
        headers = _jellyfin_headers(str(cfg.get("api_key") or ""))
        r = self.http.get(f"{url}/Library/VirtualFolders", headers=headers, timeout=TIMEOUT)
        kinds = {"music": ("music", "MusicAlbum"), "movies": ("movies", "Movie"), "tvshows": ("tv", "Series")}
        out = {"music": [], "movies": [], "tv": []}
        for f in (r.json() or []):
            kind = kinds.get(str(f.get("CollectionType") or "").lower())
            if not kind:
                continue
            bucket, item_type = kind
            count = None
            try:
                c = self.http.get(f"{url}/Items", headers=headers,
                                  params={"ParentId": f.get("ItemId"), "Recursive": "true",
                                          "IncludeItemTypes": item_type, "Limit": 0}, timeout=TIMEOUT)
                count = int((c.json() or {}).get("TotalRecordCount"))
            except Exception:
                count = None
            out[bucket].append({
                "id": str(f.get("ItemId") or f.get("Name")),
                "title": str(f.get("Name") or ""),
                "count": count,
                "locations": [str(p) for p in (f.get("Locations") or [])],
            })
        return out

    def _navidrome_libraries(self, cfg: dict) -> dict:
        url = self._url(cfg.get("base_url"))
        params = _subsonic_params(str(cfg.get("username") or ""), str(cfg.get("password") or ""))
        r = self.http.get(f"{url}/rest/getMusicFolders", params=params, timeout=TIMEOUT)
        body = (r.json() or {}).get("subsonic-response", {})
        folders = (body.get("musicFolders") or {}).get("musicFolder") or []
        return {"music": [{"id": str(f.get("id")), "title": str(f.get("name") or ""),
                           "count": None, "locations": []} for f in folders],
                "movies": [], "tv": []}

    # ---- what you listen to ---------------------------------------------

    def top_artists(self, server: str, cfg: dict, library_id: str = "", limit: int = 18) -> list:
        """artist names, most played first. [] when nothing could be read"""
        try:
            if server == "plex":
                names = self._plex_top_artists(cfg, library_id, limit)
            elif server == "jellyfin":
                names = self._jellyfin_top_artists(cfg, limit)
            elif server == "navidrome":
                names = self._navidrome_top_artists(cfg, limit)
            else:
                names = []
        except Exception:
            names = []
        out, seen = [], set()
        for name in names:
            key = name.strip().casefold()
            if key and key not in seen and key not in ("various artists", "unknown artist"):
                seen.add(key)
                out.append(name.strip())
        return out[:limit]

    def _plex_top_artists(self, cfg: dict, library_id: str, limit: int) -> list:
        url = self._url(cfg.get("base_url"))
        headers = {"X-Plex-Token": str(cfg.get("token") or ""), "Accept": "application/json"}
        sections = [library_id] if library_id else [
            lib["id"] for lib in self._plex_libraries(cfg)["music"]]
        names: list = []
        for key in sections:
            r = self.http.get(f"{url}/library/sections/{key}/all", headers=headers,
                              params={"type": 8, "sort": "viewCount:desc",
                                      "X-Plex-Container-Start": 0, "X-Plex-Container-Size": limit * 2},
                              timeout=TIMEOUT)
            items = (r.json() or {}).get("MediaContainer", {}).get("Metadata") or []
            names.extend(str(i.get("title") or "") for i in items)
        return names

    def _jellyfin_top_artists(self, cfg: dict, limit: int) -> list:
        url = self._url(cfg.get("base_url"))
        headers = _jellyfin_headers(str(cfg.get("api_key") or ""))
        users = self.http.get(f"{url}/Users", headers=headers, timeout=TIMEOUT).json() or []
        admin = next((u for u in users if (u.get("Policy") or {}).get("IsAdministrator")), None) or (users[0] if users else None)
        if not admin:
            return []
        r = self.http.get(f"{url}/Users/{admin.get('Id')}/Items", headers=headers,
                          params={"IncludeItemTypes": "Audio", "Recursive": "true", "SortBy": "PlayCount",
                                  "SortOrder": "Descending", "Filters": "IsPlayed", "Limit": 400},
                          timeout=TIMEOUT)
        counts: Counter = Counter()
        for item in (r.json() or {}).get("Items") or []:
            artist = item.get("AlbumArtist") or (item.get("Artists") or [None])[0]
            if artist:
                counts[artist] += int((item.get("UserData") or {}).get("PlayCount") or 1)
        return [name for name, _ in counts.most_common(limit * 2)]

    def _navidrome_top_artists(self, cfg: dict, limit: int) -> list:
        url = self._url(cfg.get("base_url"))
        params = _subsonic_params(str(cfg.get("username") or ""), str(cfg.get("password") or ""))
        params.update({"type": "frequent", "size": 200})
        r = self.http.get(f"{url}/rest/getAlbumList2", params=params, timeout=TIMEOUT)
        albums = ((r.json() or {}).get("subsonic-response", {}).get("albumList2") or {}).get("album") or []
        counts: Counter = Counter()
        for album in albums:
            if album.get("artist"):
                counts[album["artist"]] += int(album.get("playCount") or 1)
        return [name for name, _ in counts.most_common(limit * 2)]


SERVICES = ("slskd", "plex", "jellyfin", "navidrome", "prowlarr", "qbittorrent", "sabnzbd",
            "tmdb", "listenbrainz")


def probe(service: str, cfg: dict, prober: Optional[Prober] = None) -> dict:
    prober = prober or Prober()
    if service not in SERVICES:
        return _result(False, f"Unknown service {service}")
    try:
        return getattr(prober, service)(cfg or {})
    except Exception:
        return _result(False, f"Couldn't check {service}")
