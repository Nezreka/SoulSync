"""SoulSync — video media-server adapters (Plex / Jellyfin).

Turn the live, already-connected media clients (owned by the shared
MediaServerEngine) into normalized dicts the scanner understands. We REUSE the
shared connection/auth (don't reinvent it) but keep all video-section logic here
so music code is untouched.

NOTE: these talk to real Plex/Jellyfin servers and can only be fully validated
against a live server. The scanner itself is server-agnostic and unit-tested
with a fake source; bugs found here against a real library are localized to
these adapters.
"""

from __future__ import annotations

import re

from utils.logging_config import get_logger

logger = get_logger("video_sources")

# Library scans are bulk operations — a far longer per-request timeout than the
# shared client's interactive one, so big libraries don't read-timeout mid-scan.
PLEX_SCAN_TIMEOUT = 120


def _to_int(val):
    if val is None:
        return None
    m = re.match(r"\d+", str(val))
    return int(m.group()) if m else None


def _parse_plex_guids(obj) -> dict:
    """tmdb/imdb/tvdb ids from a Plex item's guids — Plex already matched them."""
    out = {"tmdb_id": None, "imdb_id": None, "tvdb_id": None}
    try:
        for g in (getattr(obj, "guids", None) or []):
            gid = getattr(g, "id", "") or ""
            if "://" not in gid:
                continue
            scheme, value = gid.split("://", 1)
            scheme = scheme.lower()
            if scheme == "imdb":
                out["imdb_id"] = (value.split("?")[0] or None)
            elif scheme == "tmdb":
                out["tmdb_id"] = _to_int(value)
            elif scheme == "tvdb":
                out["tvdb_id"] = _to_int(value)
    except Exception:
        pass
    return out


def _parse_jf_providers(item) -> dict:
    """tmdb/imdb/tvdb ids from a Jellyfin item's ProviderIds."""
    providers = item.get("ProviderIds") or {}
    low = {(k or "").lower(): v for k, v in providers.items()}
    return {
        "imdb_id": low.get("imdb") or None,
        "tmdb_id": _to_int(low.get("tmdb")),
        "tvdb_id": _to_int(low.get("tvdb")),
    }


def _vdb(db=None):
    """The video DB (the given one, or a fresh handle). None if unavailable."""
    if db is not None:
        return db
    try:
        from database.video_database import VideoDatabase
        return VideoDatabase()
    except Exception:
        return None


def video_plex_config(db=None):
    """VIDEO's effective Plex connection: the video side's OWN stored creds
    (video.db) when set, otherwise INHERITED read-only from the music config.
    The video side never writes the music config, so this is one-way."""
    db = _vdb(db)
    try:
        url = (db.get_setting("video_plex_url") or "").strip() if db else ""
        token = (db.get_setting("video_plex_token") or "").strip() if db else ""
    except Exception:
        url, token = "", ""
    if url and token:
        return {"base_url": url, "token": token, "source": "video"}
    try:
        from config.settings import config_manager
        cfg = config_manager.get_plex_config() or {}
        return {"base_url": cfg.get("base_url") or "", "token": cfg.get("token") or "",
                "source": "music"}
    except Exception:
        return {"base_url": "", "token": "", "source": "music"}


def video_jellyfin_config(db=None):
    """VIDEO's effective Jellyfin connection: the video side's OWN stored creds
    when set, otherwise INHERITED read-only from the music config."""
    db = _vdb(db)
    try:
        url = (db.get_setting("video_jellyfin_url") or "").strip() if db else ""
        key = (db.get_setting("video_jellyfin_key") or "").strip() if db else ""
    except Exception:
        url, key = "", ""
    if url and key:
        return {"base_url": url, "api_key": key, "source": "video"}
    try:
        from config.settings import config_manager
        cfg = config_manager.get_jellyfin_config() or {}
        return {"base_url": cfg.get("base_url") or "", "api_key": cfg.get("api_key") or "",
                "source": "music"}
    except Exception:
        return {"base_url": "", "api_key": "", "source": "music"}


def resolve_video_server(db=None):
    """The server the VIDEO side uses — a configured Plex/Jellyfin, resolved
    INDEPENDENTLY of the music 'active server' pointer (so e.g. Navidrome-for-music
    + Plex-for-video works, and music-only servers never apply here). Returns
    'plex' | 'jellyfin' | None. Order: explicit video pick → the single configured
    one → Plex when both → None. 'Configured' means video's EFFECTIVE config
    (its own creds, or inherited from music) has a base_url."""
    db = _vdb(db)
    plex_ok = bool(video_plex_config(db).get("base_url"))
    jelly_ok = bool(video_jellyfin_config(db).get("base_url"))

    pref = None
    if db is not None:
        try:
            pref = db.get_setting("video_server")
        except Exception:
            pref = None
    if pref == "plex" and plex_ok:
        return "plex"
    if pref == "jellyfin" and jelly_ok:
        return "jellyfin"
    # Fully INDEPENDENT of the music 'active server' — only an explicit video pick
    # or the configured server(s) decide, so changing the music server NEVER
    # changes video (and vice-versa). Auto-pick the single configured one; default
    # to Plex when both are set (the user picks Jellyfin via the Video Source panel).
    if plex_ok and not jelly_ok:
        return "plex"
    if jelly_ok and not plex_ok:
        return "jellyfin"
    if plex_ok and jelly_ok:
        return "plex"
    return None


def _video_jellyfin_source(cfg, movies_lib=None, tv_lib=None):
    """A JellyfinVideoSource connected with VIDEO's OWN config — independent of
    music's shared singleton client. The video source only needs base_url/api_key
    (for _make_request) and any valid user_id (for /Users/{id}/Items browsing)."""
    base = (cfg.get("base_url") or "").rstrip("/")
    key = cfg.get("api_key") or ""
    if not base or not key:
        return None
    try:
        from core.jellyfin_client import JellyfinClient
        client = JellyfinClient()
        client.base_url = base
        client.api_key = key
        users = client._make_request("/Users") or []
        if not users:
            return None
        # Jellyfin scopes /Users/{id}/Views to that user's library access, so honor
        # the user the operator explicitly picked (stored video_jellyfin_user). Until
        # they pick, default to an ADMIN (full visibility) so nothing's hidden.
        pref = ""
        try:
            _db = _vdb()
            pref = (_db.get_setting("video_jellyfin_user") or "") if _db else ""
        except Exception:
            pref = ""
        chosen = next((u for u in users if u.get("Id") == pref), None)
        if chosen is None:
            admins = [u for u in users if (u.get("Policy") or {}).get("IsAdministrator")]
            chosen = (admins or users)[0]
        uid = chosen.get("Id")
        if not uid:
            return None
        client.user_id = uid
        return JellyfinVideoSource(client, movies_lib=movies_lib, tv_lib=tv_lib)
    except Exception:
        logger.exception("video sources: Jellyfin connect failed")
        return None


def video_jellyfin_test(cfg):
    """Diagnose the video Jellyfin connection precisely (for the Test button).
    Returns (ok: bool, message: str). Distinguishes 'can't reach the server',
    'API key rejected', and 'no users' instead of one vague failure — reuses the
    same X-Emby-Token header the music client uses (_make_request)."""
    base = (cfg.get("base_url") or "").rstrip("/")
    key = cfg.get("api_key") or ""
    if not base or not key:
        return False, "Jellyfin URL/API key not set"
    import requests
    headers = {"X-Emby-Token": key}
    try:
        info = requests.get(base + "/System/Info", headers=headers, timeout=8)
    except requests.exceptions.ConnectionError:
        return False, "Can't reach Jellyfin at %s — is it running and reachable on that host/port?" % base
    except requests.exceptions.RequestException as e:
        return False, "Couldn't connect to Jellyfin: %s" % (e,)
    if info.status_code in (401, 403):
        return False, "Jellyfin rejected the API key (HTTP %d). Check the key." % info.status_code
    if info.status_code != 200:
        return False, "Jellyfin returned HTTP %d for /System/Info." % info.status_code
    try:
        users = requests.get(base + "/Users", headers=headers, timeout=8).json()
    except Exception:
        users = None
    if not users:
        return False, "Connected, but Jellyfin returned no users for this API key."
    name = (info.json() or {}).get("ServerName") or "Jellyfin"
    return True, "Connected to %s" % name


def _build_source(movies_lib=None, tv_lib=None):
    """Build a media source for the VIDEO server (see resolve_video_server),
    restricted to the named Movies/TV libraries when given. Uses VIDEO's OWN
    effective connection config (its creds, or inherited from music) — never the
    music side's live connection, so the two stay independent."""
    db = _vdb()
    server = resolve_video_server(db)

    if server == "plex":
        cfg = video_plex_config(db)
        base_url, token = cfg.get("base_url"), cfg.get("token")
        if not base_url or not token:
            return None
        try:
            from plexapi.server import PlexServer
            srv = PlexServer(base_url, token, timeout=PLEX_SCAN_TIMEOUT)
            return PlexVideoSource(srv, movies_lib=movies_lib, tv_lib=tv_lib)
        except Exception:
            logger.exception("video sources: Plex connect failed")
            return None

    if server == "jellyfin":
        return _video_jellyfin_source(video_jellyfin_config(db), movies_lib, tv_lib)

    return None


def _load_selection():
    """The user's Movies/TV library choice for the VIDEO server (or {})."""
    try:
        from database.video_database import VideoDatabase
        server = resolve_video_server()
        if not server:
            return {}
        return VideoDatabase().get_library_selection(server)
    except Exception:
        logger.exception("video sources: could not load library selection")
        return {}


def get_active_video_source():
    """Source for SCANNING — restricted to the user-mapped Movies/TV libraries.
    Falls back to all libraries when nothing is mapped yet."""
    sel = _load_selection() or {}
    return _build_source(sel.get("movies") or None, sel.get("tv") or None)


def normalize_media_type(media_type) -> str:
    """'movie'|'show'|'all' — accepts the friendly aliases (movies/tv/series/…) the
    UI and automation configs use. Movies and TV are independent libraries, so the
    scan family is scoped by this everywhere."""
    m = str(media_type or "all").lower()
    if m in ("movie", "movies", "film", "films"):
        return "movie"
    if m in ("show", "shows", "tv", "series", "episode", "episodes"):
        return "show"
    return "all"


def refresh_video_server_sections(media_type="all"):
    """Tell the active media server to rescan its selected VIDEO sections (so newly
    downloaded files get indexed) — the video twin of music's 'Scan Library'.
    ``media_type`` scopes it to one library ('movie' / 'show'); 'all' (default) nudges
    both. Returns {ok, sections} or {ok: False, error}."""
    media_type = normalize_media_type(media_type)
    src = get_active_video_source()
    if src is None:
        return {"ok": False, "error": "No video server configured"}
    if not hasattr(src, "refresh_sections"):
        return {"ok": False, "error": "This server doesn't support a scan trigger"}
    try:
        return src.refresh_sections(media_type)
    except Exception as e:   # noqa: BLE001 - surface any server error to the automation
        logger.exception("video sources: refresh failed")
        return {"ok": False, "error": str(e)}


def list_video_libraries():
    """Discover the active server's video libraries for the mapping UI:
    {'server', 'movies': [{'title'}], 'tv': [{'title'}]} or None."""
    src = _build_source()
    if src is None:
        return None
    out = src.available_libraries()
    out["server"] = src.server_name
    return out


# ── Plex ──────────────────────────────────────────────────────────────────────
class PlexVideoSource:
    server_name = "plex"

    def __init__(self, server, movies_lib=None, tv_lib=None):
        self._server = server
        self._movies_lib = movies_lib
        self._tv_lib = tv_lib

    def _sections(self, kind: str, name=None):
        secs = [s for s in self._server.library.sections() if s.type == kind]
        if name:
            secs = [s for s in secs if s.title == name]
        return secs

    def _scan_sections(self, kind: str, name):
        """Sections to SCAN for a kind. UNLIKE _sections, an empty name means
        'this kind isn't mapped' → scan NOTHING (never fall back to all sections).
        Prevents a missing selection from silently pulling every library."""
        return self._sections(kind, name) if name else []

    def available_libraries(self) -> dict:
        return {
            "movies": [{"title": s.title} for s in self._sections("movie")],
            "tv": [{"title": s.title} for s in self._sections("show")],
        }

    def counts(self, incremental=False) -> dict:
        """Cheap item totals (no full fetch) for the progress bar."""
        m = sum(int(getattr(s, "totalSize", 0) or 0) for s in self._scan_sections("movie", self._movies_lib))
        sh = sum(int(getattr(s, "totalSize", 0) or 0) for s in self._scan_sections("show", self._tv_lib))
        if incremental:
            m, sh = min(m, 100), min(sh, 50)
        return {"movies": m, "shows": sh}

    def iter_movies(self, incremental=False):
        for section in self._scan_sections("movie", self._movies_lib):
            items = section.search(sort="addedAt:desc", maxresults=100) if incremental else section.all()
            for m in items:
                try:
                    yield self._movie(m)
                except Exception:
                    logger.exception("Plex: skipping movie %s", getattr(m, "title", "?"))

    def iter_shows(self, incremental=False):
        for section in self._scan_sections("show", self._tv_lib):
            items = section.search(sort="addedAt:desc", maxresults=50) if incremental else section.all()
            for sh in items:
                try:
                    yield self._show(sh)
                except Exception:
                    logger.exception("Plex: skipping show %s", getattr(sh, "title", "?"))

    def refresh_sections(self, media_type="all") -> dict:
        """Tell Plex to rescan the selected video sections so freshly-downloaded files
        get indexed. (plexapi ``section.update()`` triggers the library scan.)
        ``media_type`` scopes it to the Movie or TV section; 'all' does both."""
        n = 0
        for kind, name in (("movie", self._movies_lib), ("show", self._tv_lib)):
            if media_type != "all" and media_type != kind:
                continue
            for s in self._scan_sections(kind, name):
                try:
                    s.update()
                    n += 1
                except Exception:
                    logger.exception("Plex: refresh failed for section %s", getattr(s, "title", "?"))
        return {"ok": n > 0, "sections": n}

    @staticmethod
    def _part_file(obj):
        try:
            media = obj.media[0]
            part = media.parts[0]
            return {
                "relative_path": part.file,
                "size_bytes": getattr(part, "size", None),
                "resolution": getattr(media, "videoResolution", None),
                "video_codec": getattr(media, "videoCodec", None),
                "audio_codec": getattr(media, "audioCodec", None),
                "runtime_seconds": int(obj.duration / 1000) if getattr(obj, "duration", None) else None,
            }
        except Exception:
            return None

    @staticmethod
    def _tags(seq) -> list:
        """Tag names from a Plex tag list (genres/etc.)."""
        out = []
        for t in (seq or []):
            tag = getattr(t, "tag", None)
            if tag:
                out.append(tag)
        return out

    @staticmethod
    def _date(val):
        try:
            return val.date().isoformat() if val else None
        except Exception:
            return None

    def _movie(self, m) -> dict:
        dur = getattr(m, "duration", None)
        d = {
            "server_id": str(m.ratingKey),
            "title": m.title,
            "year": getattr(m, "year", None),
            "overview": getattr(m, "summary", None),
            "poster_url": getattr(m, "thumb", None),
            "content_rating": getattr(m, "contentRating", None),
            "studio": getattr(m, "studio", None),
            "tagline": getattr(m, "tagline", None),
            "rating": getattr(m, "audienceRating", None),
            "rating_critic": getattr(m, "rating", None),
            "genres": self._tags(getattr(m, "genres", None)),
            "runtime_minutes": int(dur / 60000) if dur else None,
            "file": self._part_file(m),
        }
        d.update(_parse_plex_guids(m))
        return d

    def _episode(self, ep, snum, enum) -> dict:
        dur = getattr(ep, "duration", None)
        aired = getattr(ep, "originallyAvailableAt", None)
        return {
            "server_id": str(ep.ratingKey),
            "season_number": snum,
            "episode_number": enum,
            "title": ep.title,
            "overview": getattr(ep, "summary", None),
            "air_date": aired.date().isoformat() if aired else None,
            "runtime_minutes": int(dur / 60000) if dur else None,
            "still_url": getattr(ep, "thumb", None),
            "rating": getattr(ep, "audienceRating", None),
            "tvdb_id": _parse_plex_guids(ep).get("tvdb_id"),
            "file": self._part_file(ep),
        }

    def _show(self, sh) -> dict:
        # One episodes() call for the whole show (grouped by season) instead of a
        # request per season — far fewer round-trips, much less timeout-prone.
        seasons_map = {}
        try:
            for ep in sh.episodes():
                enum = getattr(ep, "index", None)
                if enum is None:
                    # No episode number (unmatched/special) — can't key it; skip.
                    continue
                snum = ep.parentIndex if getattr(ep, "parentIndex", None) is not None else 0
                seasons_map.setdefault(snum, []).append(self._episode(ep, snum, enum))
        except Exception:
            logger.exception("Plex: failed reading episodes for %s", getattr(sh, "title", "?"))
        # Season metadata (poster/title/overview) — one extra call per show gives
        # real per-season art for the detail page.
        season_meta = {}
        try:
            for se in sh.seasons():
                sidx = getattr(se, "index", None)
                if sidx is None:
                    continue
                season_meta[sidx] = {
                    "server_id": str(getattr(se, "ratingKey", "")) or None,
                    "title": getattr(se, "title", None),
                    "overview": getattr(se, "summary", None),
                    "poster_url": getattr(se, "thumb", None),
                }
        except Exception:
            logger.exception("Plex: failed reading seasons for %s", getattr(sh, "title", "?"))
        seasons = []
        for n, eps in sorted(seasons_map.items()):
            meta = season_meta.get(n, {})
            seasons.append({"server_id": meta.get("server_id"), "season_number": n,
                            "title": meta.get("title"), "overview": meta.get("overview"),
                            "poster_url": meta.get("poster_url"), "episodes": eps})
        d = {
            "server_id": str(sh.ratingKey),
            "title": sh.title,
            "year": getattr(sh, "year", None),
            "overview": getattr(sh, "summary", None),
            "poster_url": getattr(sh, "thumb", None),
            "status": None,
            "network": getattr(sh, "network", None),
            "content_rating": getattr(sh, "contentRating", None),
            "tagline": getattr(sh, "tagline", None),
            "rating": getattr(sh, "audienceRating", None),
            "first_air_date": self._date(getattr(sh, "originallyAvailableAt", None)),
            "last_air_date": None,
            "genres": self._tags(getattr(sh, "genres", None)),
            "seasons": seasons,
        }
        d.update(_parse_plex_guids(sh))
        return d


# ── Jellyfin ────────────────────────────────────────────────────────────────
_JF_MOVIE_FIELDS = ("Overview,Path,MediaSources,ProductionYear,OfficialRating,RunTimeTicks,Studios,"
                    "ProviderIds,Genres,Taglines,CommunityRating,CriticRating")
_JF_EP_FIELDS = ("Overview,Path,MediaSources,PremiereDate,RunTimeTicks,IndexNumber,ParentIndexNumber,"
                 "ProviderIds,CommunityRating")
_JF_SHOW_FIELDS = ("Overview,ProductionYear,OfficialRating,ProviderIds,Genres,Taglines,CommunityRating,"
                   "PremiereDate,EndDate")


class JellyfinVideoSource:
    server_name = "jellyfin"

    def __init__(self, client, movies_lib=None, tv_lib=None):
        self._c = client
        self.uid = client.user_id
        self._movies_lib = movies_lib
        self._tv_lib = tv_lib

    def _req(self, path, params=None):
        return self._c._make_request(path, params=params)

    def _views(self, collection_type: str, name=None):
        resp = self._req(f"/Users/{self.uid}/Views") or {}
        views = [v for v in resp.get("Items", [])
                 if (v.get("CollectionType") or "").lower() == collection_type]
        if name:
            views = [v for v in views if v.get("Name") == name]
        return views

    def _scan_views(self, collection_type: str, name):
        """Views to SCAN. An empty name means this kind isn't mapped → scan NOTHING
        (never fall back to all views), so a missing selection can't pull every
        library. (available_libraries still lists all via _views.)"""
        return self._views(collection_type, name) if name else []

    def available_libraries(self) -> dict:
        return {
            "movies": [{"title": v.get("Name")} for v in self._views("movies")],
            "tv": [{"title": v.get("Name")} for v in self._views("tvshows")],
        }

    def refresh_sections(self, media_type="all") -> dict:
        """Ask Jellyfin to rescan the selected video libraries (POST /Items/{id}/Refresh)
        so freshly-downloaded files get indexed. _make_request is GET-only, so POST direct.
        ``media_type`` scopes it to the Movie or TV view; 'all' does both."""
        import requests
        base = (self._c.base_url or "").rstrip("/")
        if not base:
            return {"ok": False, "sections": 0}
        headers = {"X-Emby-Token": self._c.api_key or ""}
        views = []
        if media_type in ("all", "movie"):
            views += list(self._scan_views("movies", self._movies_lib))
        if media_type in ("all", "show"):
            views += list(self._scan_views("tvshows", self._tv_lib))
        n = 0
        for v in views:
            vid = v.get("Id")
            if not vid:
                continue
            try:
                requests.post(base + "/Items/" + str(vid) + "/Refresh", headers=headers,
                              params={"Recursive": "true", "MetadataRefreshMode": "Default",
                                      "ImageRefreshMode": "Default"}, timeout=15)
                n += 1
            except Exception:
                logger.exception("Jellyfin: refresh failed for view %s", vid)
        return {"ok": n > 0, "sections": n}

    def counts(self, incremental=False) -> dict:
        def total(view, itype):
            resp = self._req(f"/Users/{self.uid}/Items", {
                "ParentId": view["Id"], "IncludeItemTypes": itype,
                "Recursive": "true", "Limit": "0"}) or {}
            return int(resp.get("TotalRecordCount", 0) or 0)
        m = sum(total(v, "Movie") for v in self._scan_views("movies", self._movies_lib))
        sh = sum(total(v, "Series") for v in self._scan_views("tvshows", self._tv_lib))
        if incremental:
            m, sh = min(m, 100), min(sh, 50)
        return {"movies": m, "shows": sh}

    def _paged(self, path, params, page_size=500):
        """Yield items across pages so large libraries aren't capped/truncated."""
        start = 0
        while True:
            p = dict(params)
            p.update({"StartIndex": str(start), "Limit": str(page_size)})
            resp = self._req(path, p) or {}
            batch = resp.get("Items", [])
            for it in batch:
                yield it
            start += len(batch)
            total = resp.get("TotalRecordCount")
            if not batch or len(batch) < page_size or (total is not None and start >= total):
                break

    @staticmethod
    def _ticks_to_seconds(ticks):
        return int(ticks / 10_000_000) if ticks else None

    @staticmethod
    def _file(item):
        sources = item.get("MediaSources") or []
        if not sources:
            path = item.get("Path")
            return {"relative_path": path} if path else None
        src = sources[0]
        streams = src.get("MediaStreams") or []
        vid = next((s for s in streams if s.get("Type") == "Video"), {})
        aud = next((s for s in streams if s.get("Type") == "Audio"), {})
        return {
            "relative_path": src.get("Path") or item.get("Path") or "",
            "size_bytes": src.get("Size"),
            "resolution": (str(vid.get("Height")) + "p") if vid.get("Height") else None,
            "video_codec": vid.get("Codec"),
            "audio_codec": aud.get("Codec"),
            "runtime_seconds": JellyfinVideoSource._ticks_to_seconds(item.get("RunTimeTicks")),
        }

    def iter_movies(self, incremental=False):
        path = f"/Users/{self.uid}/Items"
        for view in self._scan_views("movies", self._movies_lib):
            params = {"ParentId": view["Id"], "IncludeItemTypes": "Movie",
                      "Recursive": "true", "Fields": _JF_MOVIE_FIELDS}
            if incremental:
                params.update({"SortBy": "DateCreated", "SortOrder": "Descending", "Limit": "100"})
                items = (self._req(path, params) or {}).get("Items", [])
            else:
                items = self._paged(path, params)
            for it in items:
                try:
                    yield self._movie(it)
                except Exception:
                    logger.exception("Jellyfin: skipping movie %s", it.get("Name", "?"))

    @staticmethod
    def _first(seq):
        seq = seq or []
        return seq[0] if seq else None

    def _movie(self, it) -> dict:
        studios = it.get("Studios") or []
        ticks = it.get("RunTimeTicks")
        d = {
            "server_id": str(it["Id"]),
            "title": it.get("Name"),
            "year": it.get("ProductionYear"),
            "overview": it.get("Overview"),
            "poster_url": (it.get("ImageTags") or {}).get("Primary"),
            "content_rating": it.get("OfficialRating"),
            "studio": studios[0].get("Name") if studios else None,
            "tagline": self._first(it.get("Taglines")),
            "rating": it.get("CommunityRating"),
            "rating_critic": it.get("CriticRating"),
            "genres": it.get("Genres") or [],
            "runtime_minutes": int(ticks / 600_000_000) if ticks else None,
            "file": self._file(it),
        }
        d.update(_parse_jf_providers(it))
        return d

    def iter_shows(self, incremental=False):
        path = f"/Users/{self.uid}/Items"
        for view in self._scan_views("tvshows", self._tv_lib):
            params = {"ParentId": view["Id"], "IncludeItemTypes": "Series",
                      "Recursive": "true", "Fields": _JF_SHOW_FIELDS}
            if incremental:
                params.update({"SortBy": "DateCreated", "SortOrder": "Descending", "Limit": "50"})
                items = (self._req(path, params) or {}).get("Items", [])
            else:
                items = self._paged(path, params)
            for it in items:
                try:
                    yield self._show(it)
                except Exception:
                    logger.exception("Jellyfin: skipping show %s", it.get("Name", "?"))

    def _show(self, it) -> dict:
        series_id = str(it["Id"])
        seasons = []
        try:
            eps_resp = self._req(f"/Shows/{series_id}/Episodes", {
                "UserId": self.uid, "Fields": _JF_EP_FIELDS}) or {}
            by_season: dict[int, list] = {}
            for ep in eps_resp.get("Items", []):
                enum = ep.get("IndexNumber")
                if enum is None:
                    continue  # unnumbered/special — can't key it
                snum = ep.get("ParentIndexNumber") or 0
                aired = ep.get("PremiereDate")
                ticks = ep.get("RunTimeTicks")
                by_season.setdefault(snum, []).append({
                    "server_id": str(ep["Id"]),
                    "season_number": snum,
                    "episode_number": enum,
                    "title": ep.get("Name"),
                    "overview": ep.get("Overview"),
                    "air_date": aired[:10] if aired else None,
                    "runtime_minutes": int(ticks / 600_000_000) if ticks else None,
                    "still_url": (ep.get("ImageTags") or {}).get("Primary"),
                    "rating": ep.get("CommunityRating"),
                    "tvdb_id": _parse_jf_providers(ep).get("tvdb_id"),
                    "file": self._file(ep),
                })
            # Season metadata (poster/title/overview) — one extra call per show.
            season_meta = {}
            try:
                seas = self._req(f"/Shows/{series_id}/Seasons",
                                 {"UserId": self.uid, "Fields": "Overview"}) or {}
                for se in seas.get("Items", []):
                    sidx = se.get("IndexNumber")
                    if sidx is None:
                        continue
                    season_meta[sidx] = {
                        "server_id": str(se["Id"]) if se.get("Id") else None,
                        "title": se.get("Name"),
                        "overview": se.get("Overview"),
                        "poster_url": (se.get("ImageTags") or {}).get("Primary"),
                    }
            except Exception:
                logger.exception("Jellyfin: failed reading seasons for %s", it.get("Name", "?"))
            for snum, eps in sorted(by_season.items()):
                meta = season_meta.get(snum, {})
                seasons.append({"server_id": meta.get("server_id"), "season_number": snum,
                                "title": meta.get("title"), "overview": meta.get("overview"),
                                "poster_url": meta.get("poster_url"), "episodes": eps})
        except Exception:
            logger.exception("Jellyfin: failed reading episodes for %s", it.get("Name", "?"))
        premiere = it.get("PremiereDate")
        end = it.get("EndDate")
        d = {
            "server_id": series_id,
            "title": it.get("Name"),
            "year": it.get("ProductionYear"),
            "overview": it.get("Overview"),
            "poster_url": (it.get("ImageTags") or {}).get("Primary"),
            "status": None,
            "network": None,
            "content_rating": it.get("OfficialRating"),
            "tagline": self._first(it.get("Taglines")),
            "rating": it.get("CommunityRating"),
            "first_air_date": premiere[:10] if premiere else None,
            "last_air_date": end[:10] if end else None,
            "genres": it.get("Genres") or [],
            "seasons": seasons,
        }
        d.update(_parse_jf_providers(it))
        return d
