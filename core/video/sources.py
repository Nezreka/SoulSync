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


def _iso_dt(v):
    """ISO 'YYYY-MM-DD HH:MM:SS' from a plexapi datetime, an ISO string
    (Jellyfin LastPlayedDate), or None. Never raises."""
    try:
        if not v:
            return None
        if isinstance(v, str):
            return v[:19].replace("T", " ") or None
        return v.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:   # noqa: BLE001
        return None


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


def set_video_poster(server_id, *, image_url=None, image_bytes=None, kind="movie",
                     delete_key=None) -> dict:
    """Push a poster to the active media server (Plex/Jellyfin) for the given item.
    ``delete_key`` lets an overlay re-apply drop its previous Plex upload first (ignored by
    Jellyfin, which replaces in place)."""
    src = get_active_video_source()
    if src is None:
        return {"ok": False, "error": "No video server configured"}
    if not hasattr(src, "set_poster"):
        return {"ok": False, "error": "This server doesn't support setting a poster"}
    try:
        return src.set_poster(server_id, image_url=image_url, image_bytes=image_bytes, kind=kind,
                              delete_key=delete_key)
    except Exception as e:   # noqa: BLE001 - surface any server error to the caller
        logger.exception("video sources: set_poster failed")
        return {"ok": False, "error": str(e)}


def video_server_scan_in_progress(media_type="all"):
    """True if the active video server is mid-scan for the given library (or either,
    for 'all'); False if idle; None if it can't be determined — no server, or an
    adapter that can't report scan state. Callers fall back to a fixed wait on None."""
    media_type = normalize_media_type(media_type)
    src = get_active_video_source()
    if src is None or not hasattr(src, "is_scanning"):
        return None
    try:
        return bool(src.is_scanning(media_type))
    except Exception:
        logger.debug("video sources: scan-status check failed", exc_info=True)
        return None


def video_server_has_item(media_type, item) -> bool:
    """True if the active server already has this specific grab indexed — the signal
    for the post-download scan to skip a library's expensive crawl. Conservative: any
    uncertainty (no server, unsupported, error, no match) → False, so we scan."""
    media_type = normalize_media_type(media_type)
    if media_type not in ("movie", "show") or not item:
        return False
    src = get_active_video_source()
    if src is None or not hasattr(src, "has_item"):
        return False
    try:
        return bool(src.has_item(media_type, item))
    except Exception:
        logger.debug("video sources: has_item probe failed", exc_info=True)
        return False


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
def _union_episode_delta_shows(section, since, shows):
    """Union into ``shows`` the parent shows of any EPISODE ADDED since ``since``.

    A new episode doesn't change its show's addedAt, so the show-level addedAt delta misses
    freshly-added episodes. Searching at the EPISODE level by ``addedAt`` and pulling the parent
    shows catches them. (addedAt, not updatedAt — Plex's nightly refresh bumps updatedAt on
    ~every episode, which would drag in the whole library.) De-duplicated by ratingKey. Best-
    effort: any hiccup returns ``shows`` unchanged so the show-level delta still stands."""
    try:
        shows = list(shows)
        seen = {str(getattr(s, "ratingKey", "") or "") for s in shows}
        extra_keys = []
        try:
            eps = section.search(libtype="episode", filters={"addedAt>>": since},
                                 sort="addedAt:desc", maxresults=2000)
        except Exception:   # noqa: BLE001 - the episode delta is an assist over the show delta
            logger.debug("Plex: episode addedAt delta search failed", exc_info=True)
            eps = []
        for ep in eps:
            gk = str(getattr(ep, "grandparentRatingKey", "") or "")
            if gk and gk not in seen:
                seen.add(gk)
                extra_keys.append(gk)
        for gk in extra_keys:
            try:
                shows.append(section.fetchItem(int(gk)))
            except Exception:   # noqa: BLE001 - a show we can't re-fetch is skipped, not fatal
                logger.debug("Plex: could not fetch show %s for episode delta", gk, exc_info=True)
        return shows
    except Exception:   # noqa: BLE001 - the episode-level delta is an assist over the show delta
        logger.debug("Plex: episode-level delta failed", exc_info=True)
        return shows


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

    def iter_movies(self, incremental=False, since=None):
        for section in self._scan_sections("movie", self._movies_lib):
            if not incremental:
                items = section.all()
            elif since is not None:
                # Delta = everything ADDED to Plex since our last scan. NOTE: use addedAt, NOT
                # updatedAt — Plex bumps updatedAt on nearly every item during its nightly
                # metadata refresh (a 3389-show library returns ~3373 for updatedAt>>, but only
                # the 3 genuinely-new ones for addedAt>>), so an updatedAt delta is pure noise
                # AND a new item's bumped updatedAt gets consumed by the advancing baseline
                # before it's caught. addedAt is the immutable "when added" — the real signal.
                try:
                    items = section.search(filters={"addedAt>>": since}, sort="addedAt:desc")
                    # Watch-state changes bump lastViewedAt — a separate delta so an incremental
                    # still refreshes play_count/last_viewed_at on old items (watched-cleanup
                    # relies on it). Deduped by ratingKey; best-effort.
                    try:
                        seen_keys = {str(getattr(x, "ratingKey", "")) for x in items}
                        items = list(items) + [
                            w for w in section.search(filters={"lastViewedAt>>": since},
                                                      sort="lastViewedAt:desc")
                            if str(getattr(w, "ratingKey", "")) not in seen_keys]
                    except Exception:   # noqa: BLE001 - the watch delta is an assist
                        logger.debug("Plex: lastViewedAt delta failed", exc_info=True)
                except Exception:
                    logger.warning("Plex: addedAt delta filter failed; using recent window", exc_info=True)
                    items = section.search(sort="addedAt:desc", maxresults=200)
            else:
                items = section.search(sort="addedAt:desc", maxresults=100)   # first run: recent window
            for m in items:
                try:
                    yield self._movie(m)
                except Exception:
                    logger.exception("Plex: skipping movie %s", getattr(m, "title", "?"))

    def show_tree(self, server_id):
        """The full tree for ONE show (same shape iter_shows yields) — the
        per-show Synchronize. Returns None ONLY when Plex positively says the
        item is gone (NotFound); any other failure raises, so a server hiccup
        can never read as 'show removed'."""
        from plexapi.exceptions import NotFound
        try:
            item = self._server.fetchItem(int(server_id))
        except NotFound:
            return None
        if getattr(item, "type", "") != "show":
            return None   # re-keyed to something else entirely — treat as gone
        return self._show(item)

    def iter_shows(self, incremental=False, since=None):
        for section in self._scan_sections("show", self._tv_lib):
            # Delta = shows ADDED since our last scan (addedAt, not updatedAt — Plex's nightly
            # refresh bumps updatedAt on ~everything, so it's noise; see iter_movies).
            if not incremental:
                items = section.all()
            elif since is not None:
                try:
                    items = section.search(filters={"addedAt>>": since}, sort="addedAt:desc")
                    # CRUCIAL: a NEW EPISODE of an existing show doesn't change the SHOW's
                    # addedAt, so the show-level delta misses freshly-added episodes. Union in
                    # the parent shows of any episode ADDED since the baseline.
                    items = _union_episode_delta_shows(section, since, items)
                except Exception:
                    logger.warning("Plex: addedAt delta filter failed (shows); using recent window", exc_info=True)
                    items = section.search(sort="addedAt:desc", maxresults=300)
            else:
                items = section.search(sort="addedAt:desc", maxresults=200)
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

    def set_poster(self, server_id, *, image_url=None, image_bytes=None, kind="movie",
                   delete_key=None) -> dict:
        """Set the poster on a Plex item (by ratingKey). Plex fetches a URL itself;
        raw bytes upload via a temp file. Returns {ok, poster_key[, error]}.

        ``delete_key`` (an overlay re-apply): if the CURRENTLY-selected poster is the one we
        uploaded last time (its ratingKey matches), delete it BEFORE uploading the new render —
        Plex keeps every uploaded poster otherwise, so overlays would pile up. We only ever
        delete a poster we know is ours; a poster the user picked by hand is left untouched.
        ``poster_key`` in the result is the newly-uploaded poster's ratingKey, to store for the
        next re-apply."""
        try:
            item = self._server.fetchItem(int(server_id))
            if delete_key:
                try:
                    cur = next((p for p in item.posters() if getattr(p, "selected", False)), None)
                    if cur is not None and str(getattr(cur, "ratingKey", "")) == str(delete_key):
                        item.deletePoster()   # deletes the current /thumb = our previous overlay
                except Exception:   # noqa: BLE001 - cleanup is best-effort; never block the upload
                    logger.debug("Plex: pre-upload poster delete skipped for %s", server_id, exc_info=True)
            if image_url:
                item.uploadPoster(url=image_url)
            elif image_bytes:
                import os as _os
                import tempfile as _tf
                fd, tmp = _tf.mkstemp(suffix=".jpg")
                try:
                    with _os.fdopen(fd, "wb") as f:
                        f.write(image_bytes)
                    item.uploadPoster(filepath=tmp)
                finally:
                    try:
                        _os.unlink(tmp)
                    except OSError:
                        pass
            else:
                return {"ok": False, "error": "No image provided"}
            # The key of the poster now selected (the one we just uploaded) — for next time.
            poster_key = None
            try:
                sel = next((p for p in item.posters() if getattr(p, "selected", False)), None)
                poster_key = str(getattr(sel, "ratingKey", "")) or None
            except Exception:   # noqa: BLE001 - key read is best-effort
                logger.debug("Plex: could not read new poster key for %s", server_id, exc_info=True)
            return {"ok": True, "poster_key": poster_key}
        except Exception as e:   # noqa: BLE001 - surface any Plex/network error
            logger.exception("Plex: set_poster failed for %s", server_id)
            return {"ok": False, "error": str(e)}

    # ── User metadata edits (Manage sidebar) ──────────────────────────────────
    # SoulSync editable field -> Plex edit field. Every pushed field is also
    # LOCKED on Plex ('field.locked': 1) so Plex's own agents don't undo the
    # user's edit on their next refresh — the lock travels with the value.
    _EDIT_FIELDS = {
        "title": "title", "sort_title": "titleSort", "year": "year",
        "content_rating": "contentRating", "overview": "summary",
        "tagline": "tagline", "studio": "studio",
    }

    def edit_item_metadata(self, server_id, changes: dict, kind: str = "movie",
                           unlock_fields=None) -> dict:
        """Push user metadata edits to a Plex item (by ratingKey), locking each
        pushed field server-side. ``unlock_fields`` releases Plex's field lock
        (used when the user releases a SoulSync lock). Fields Plex can't edit
        (e.g. a show's network) are reported back in ``skipped``."""
        try:
            item = self._server.fetchItem(int(server_id))
            edits, skipped = {}, []
            for field, value in (changes or {}).items():
                if field == "genres":
                    continue   # tags handled below
                plex_field = self._EDIT_FIELDS.get(field)
                if not plex_field:
                    skipped.append(field)
                    continue
                edits[f"{plex_field}.value"] = "" if value is None else value
                edits[f"{plex_field}.locked"] = 1
            for field in (unlock_fields or []):
                if field == "genres":
                    edits["genre.locked"] = 0
                elif field in self._EDIT_FIELDS:
                    edits[f"{self._EDIT_FIELDS[field]}.locked"] = 0
            if edits:
                item.edit(**edits)
            if "genres" in (changes or {}):
                want = [str(g) for g in (changes.get("genres") or [])]
                current = [t.tag for t in (getattr(item, "genres", None) or [])]
                stale = [g for g in current if g not in want]
                fresh = [g for g in want if g not in current]
                if stale:
                    item.removeGenre(stale, locked=True)
                if fresh:
                    item.addGenre(fresh, locked=True)
                if not stale and not fresh:
                    item.edit(**{"genre.locked": 1})   # value unchanged — still lock it
            return {"ok": True, "skipped": skipped}
        except Exception as e:   # noqa: BLE001 - surface any Plex/network error
            logger.exception("Plex: edit_item_metadata failed for %s", server_id)
            return {"ok": False, "error": str(e)}

    def set_watched(self, server_id, watched: bool, kind: str = "movie") -> dict:
        """Mark a Plex item played/unplayed (a show marks all its episodes)."""
        try:
            item = self._server.fetchItem(int(server_id))
            if watched:
                (getattr(item, "markPlayed", None) or item.markWatched)()
            else:
                (getattr(item, "markUnplayed", None) or item.markUnwatched)()
            return {"ok": True}
        except Exception as e:   # noqa: BLE001
            logger.exception("Plex: set_watched failed for %s", server_id)
            return {"ok": False, "error": str(e)}

    # ── Collections (SoulSync-managed) ────────────────────────────────────────
    def _collection_section(self, kind: str):
        """The library section collections of a kind live in — the mapped one,
        else the first section of that kind."""
        name = self._movies_lib if kind == "movie" else self._tv_lib
        secs = self._sections(kind, name) if name else self._sections(kind)
        return secs[0] if secs else None

    def _fetch_items(self, ids):
        items = []
        for i in ids:
            try:
                items.append(self._server.fetchItem(int(i)))
            except Exception:   # noqa: BLE001 - a stale ratingKey shouldn't fail the batch
                logger.debug("Plex: fetchItem %s failed for collection op", i)
        return items

    def find_collection(self, kind: str, name: str):
        sec = self._collection_section(kind)
        if not sec:
            return None
        try:
            for c in sec.collections():
                if (getattr(c, "title", "") or "") == name:
                    return str(c.ratingKey)
        except Exception:   # noqa: BLE001
            logger.exception("Plex: find_collection failed")
        return None

    def create_collection(self, kind: str, name: str, member_ids) -> dict:
        sec = self._collection_section(kind)
        if not sec:
            return {"ok": False, "error": f"no {kind} library configured on Plex"}
        items = self._fetch_items(member_ids)
        if not items:
            return {"ok": False, "error": "no resolvable items for the collection"}
        try:
            col = sec.createCollection(title=name, items=items)
            return {"ok": True, "server_id": str(col.ratingKey)}
        except Exception as e:   # noqa: BLE001
            logger.exception("Plex: createCollection failed")
            return {"ok": False, "error": str(e)}

    def collection_member_ids(self, collection_id):
        """Member ratingKeys, or None if the collection no longer exists."""
        try:
            col = self._server.fetchItem(int(collection_id))
        except Exception:   # noqa: BLE001 - gone
            return None
        try:
            return [str(i.ratingKey) for i in col.items()]
        except Exception:   # noqa: BLE001
            logger.exception("Plex: collection items() failed for %s", collection_id)
            return None

    def collection_add(self, collection_id, ids) -> dict:
        try:
            col = self._server.fetchItem(int(collection_id))
            items = self._fetch_items(ids)
            if items:
                col.addItems(items)
            return {"ok": True}
        except Exception as e:   # noqa: BLE001
            logger.exception("Plex: collection_add failed")
            return {"ok": False, "error": str(e)}

    def collection_remove(self, collection_id, ids) -> dict:
        try:
            col = self._server.fetchItem(int(collection_id))
            items = self._fetch_items(ids)
            if items:
                col.removeItems(items)
            return {"ok": True}
        except Exception as e:   # noqa: BLE001
            logger.exception("Plex: collection_remove failed")
            return {"ok": False, "error": str(e)}

    def set_collection_meta(self, collection_id, *, poster_url=None, poster_bytes=None,
                            summary=None, sort=None, pinned=None, mode=None) -> dict:
        try:
            col = self._server.fetchItem(int(collection_id))
        except Exception as e:   # noqa: BLE001
            return {"ok": False, "error": str(e)}
        try:
            if summary:
                col.editSummary(summary)
            if sort:
                plex_sort = {"alpha": "alpha", "release": "release", "custom": "custom"}.get(sort)
                if plex_sort:
                    try:
                        col.sortUpdate(sort=plex_sort)
                    except Exception:   # noqa: BLE001 - older plexapi may lack it
                        logger.debug("Plex: sortUpdate unsupported", exc_info=True)
            if mode in ("default", "hide", "hideItems", "showItems"):
                # Library behavior — 'hideItems' is the Kometa classic: the
                # library shows one collection tile instead of every member.
                try:
                    col.modeUpdate(mode=mode)
                except Exception:   # noqa: BLE001 - older plexapi may lack it
                    logger.debug("Plex: modeUpdate unsupported", exc_info=True)
            if poster_url:
                col.uploadPoster(url=poster_url)
            elif poster_bytes:
                import os as _os
                import tempfile as _tf
                fd, tmp = _tf.mkstemp(suffix=".jpg")
                try:
                    with _os.fdopen(fd, "wb") as f:
                        f.write(poster_bytes)
                    col.uploadPoster(filepath=tmp)
                finally:
                    try:
                        _os.unlink(tmp)
                    except OSError:
                        pass
            if pinned is not None:
                try:
                    hub = col.visibility()
                    hub.promoteHome() if pinned else hub.demoteHome()
                except Exception:   # noqa: BLE001 - hub promotion varies by Plex version
                    logger.debug("Plex: hub promote/demote unsupported", exc_info=True)
            return {"ok": True}
        except Exception as e:   # noqa: BLE001
            logger.exception("Plex: set_collection_meta failed")
            return {"ok": False, "error": str(e)}

    def delete_collection(self, collection_id) -> dict:
        try:
            self._server.fetchItem(int(collection_id)).delete()
            return {"ok": True}
        except Exception as e:   # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def collection_reorder(self, collection_id, ordered_server_ids) -> dict:
        """Arrange the collection's members in the given order (custom sort).
        plexapi moveItem: no ``after`` moves to the front, then each next item
        is placed after the previous one."""
        try:
            col = self._server.fetchItem(int(collection_id))
            by_key = {str(i.ratingKey): i for i in col.items()}
            prev = None
            for sid in ordered_server_ids or []:
                item = by_key.get(str(sid))
                if item is None:
                    continue
                col.moveItem(item, after=prev)
                prev = item
            return {"ok": True}
        except Exception as e:   # noqa: BLE001
            logger.exception("Plex: collection_reorder failed")
            return {"ok": False, "error": str(e)}

    def list_collections(self) -> list:
        """EVERY collection across all movie/show sections (not just the mapped
        one — foreign collections, e.g. Kometa's, can live anywhere). For the
        server-cleanup view. [{server_id, name, count, media_type, section}]."""
        out = []
        for kind in ("movie", "show"):
            for sec in self._sections(kind):
                try:
                    cols = sec.collections()
                except Exception:   # noqa: BLE001 - one section failing shouldn't hide the rest
                    logger.exception("Plex: collections() failed for section %s",
                                     getattr(sec, "title", "?"))
                    continue
                for c in cols:
                    try:
                        labels = [str(l.tag) for l in (getattr(c, "labels", None) or []) if getattr(l, "tag", None)]
                    except Exception:   # noqa: BLE001 - labels are a detection nicety
                        labels = []
                    out.append({
                        "server_id": str(c.ratingKey),
                        "name": getattr(c, "title", "") or "",
                        "count": int(getattr(c, "childCount", 0) or 0),
                        "media_type": kind,
                        "section": getattr(sec, "title", None),
                        # Provenance fingerprints: Kometa labels everything it
                        # manages ('Kometa'/'PMM'); smart = filter-based, which
                        # SoulSync never creates.
                        "labels": labels,
                        "smart": bool(getattr(c, "smart", False)),
                    })
        return out

    def is_scanning(self, media_type="all") -> bool:
        """True if any SELECTED video section (scoped by media_type) is currently
        being scanned by Plex. Checks the per-section refreshing flag, then the
        server activity feed (real-time) — mirrors the music PlexClient check."""
        sections = []
        for kind, name in (("movie", self._movies_lib), ("show", self._tv_lib)):
            if media_type != "all" and media_type != kind:
                continue
            sections.extend(self._scan_sections(kind, name))
        for s in sections:
            if getattr(s, "refreshing", False):
                return True
        titles = {(getattr(s, "title", "") or "").lower() for s in sections}
        for act in self._server.activities():
            if getattr(act, "type", "") in ("library.scan", "library.refresh"):
                at = (getattr(act, "title", "") or "").lower()
                if any(t and t in at for t in titles):
                    return True
        return False

    def has_item(self, media_type, item) -> bool:
        """True if Plex ALREADY has this specific grab indexed (so the post-download
        scan can skip the crawl). Conservative — only True when we can positively match
        the exact movie (title + year) or episode (show + SxE)."""
        title = (item or {}).get("title")
        if not title:
            return False
        if media_type == "movie":
            year = (item or {}).get("year")
            for sec in self._scan_sections("movie", self._movies_lib):
                try:
                    hits = sec.search(title=title, maxresults=5)
                except Exception:
                    hits = []
                for h in hits:
                    hy = getattr(h, "year", None)
                    if year and hy and abs(int(hy) - int(year)) > 1:
                        continue
                    return True
            return False
        if media_type == "show":
            sn, en = (item or {}).get("season_number"), (item or {}).get("episode_number")
            for sec in self._scan_sections("show", self._tv_lib):
                try:
                    hits = sec.search(title=title, maxresults=5)
                except Exception:
                    hits = []
                for show in hits:
                    if sn is None or en is None:
                        return True            # show present, no episode to pin
                    try:
                        if show.episode(season=int(sn), episode=int(en)):
                            return True
                    except Exception:
                        continue
            return False
        return False

    @staticmethod
    def _part_files(obj):
        """EVERY version of an item (Plex ``media`` = one entry per copy/edition;
        ``parts`` = the pieces of one copy). Best-first isn't guaranteed here —
        the DB read orders by size. [] when the item has no playable media."""
        out = []
        runtime = int(obj.duration / 1000) if getattr(obj, "duration", None) else None
        for media in (getattr(obj, "media", None) or []):
            try:
                parts = media.parts or []
                if not parts:
                    continue
                out.append({
                    "relative_path": parts[0].file,
                    "size_bytes": sum(int(getattr(p, "size", 0) or 0) for p in parts) or None,
                    "resolution": getattr(media, "videoResolution", None),
                    "aspect": getattr(media, "aspectRatio", None),
                    "video_codec": getattr(media, "videoCodec", None),
                    "audio_codec": getattr(media, "audioCodec", None),
                    "runtime_seconds": runtime,
                })
            except Exception:   # noqa: BLE001 - one unreadable version never hides the rest
                continue
        return out

    @classmethod
    def _part_file(cls, obj):
        files = cls._part_files(obj)
        return files[0] if files else None

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
            "play_count": int(getattr(m, "viewCount", 0) or 0),
            "last_viewed_at": _iso_dt(getattr(m, "lastViewedAt", None)),
            "genres": self._tags(getattr(m, "genres", None)),
            "runtime_minutes": int(dur / 60000) if dur else None,
            "files": self._part_files(m),
        }
        d["file"] = (d["files"] or [None])[0]
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
            "added_at": _iso_dt(getattr(ep, "addedAt", None)),   # ranks the show in Recently Added
            "files": self._part_files(ep),
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
            "watched_episodes": int(getattr(sh, "viewedLeafCount", 0) or 0),
            "genres": self._tags(getattr(sh, "genres", None)),
            "seasons": seasons,
        }
        d.update(_parse_plex_guids(sh))
        return d


# ── Jellyfin ────────────────────────────────────────────────────────────────
_JF_MOVIE_FIELDS = ("Overview,Path,MediaSources,ProductionYear,OfficialRating,RunTimeTicks,Studios,"
                    "ProviderIds,Genres,Taglines,CommunityRating,CriticRating,UserData")
_JF_EP_FIELDS = ("Overview,Path,MediaSources,PremiereDate,RunTimeTicks,IndexNumber,ParentIndexNumber,"
                 "ProviderIds,CommunityRating,DateCreated")
_JF_SHOW_FIELDS = ("Overview,ProductionYear,OfficialRating,ProviderIds,Genres,Taglines,CommunityRating,"
                   "PremiereDate,EndDate,UserData,RecursiveItemCount")


def _union_jf_episode_delta_series(req, uid, view_id, since, series_items, show_fields):
    """Jellyfin twin of ``_union_episode_delta_shows``: union into ``series_items`` the parent
    series of any EPISODE saved since ``since``. Jellyfin doesn't bump a Series' DateLastSaved
    when it gains an episode, so the series-level MinDateLastSaved delta misses freshly-added
    episodes; an episode-level query catches both new adds and re-matches (both bump the
    episode's DateLastSaved). ``req(path, params)->dict|None``. Best-effort — any hiccup returns
    ``series_items`` unchanged so the series-level delta still stands."""
    try:
        series_items = list(series_items)
        seen = {str(s.get("Id")) for s in series_items if s.get("Id")}
        ep_resp = req(f"/Users/{uid}/Items", {
            "ParentId": view_id, "IncludeItemTypes": "Episode", "Recursive": "true",
            "MinDateLastSaved": since.isoformat(), "Fields": "SeriesId", "Limit": "1000"}) or {}
        new_ids = []
        for ep in ep_resp.get("Items", []):
            sid = str(ep.get("SeriesId") or "")
            if sid and sid not in seen:
                seen.add(sid)
                new_ids.append(sid)
        if new_ids:
            got = req(f"/Users/{uid}/Items", {
                "Ids": ",".join(new_ids), "IncludeItemTypes": "Series",
                "Recursive": "true", "Fields": show_fields}) or {}
            series_items += got.get("Items", [])
        return series_items
    except Exception:   # noqa: BLE001 - the episode-level delta is an assist over the series delta
        logger.debug("Jellyfin: episode-level delta failed", exc_info=True)
        return series_items


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

    def set_poster(self, server_id, *, image_url=None, image_bytes=None, kind="movie",
                   delete_key=None) -> dict:
        """Upload a Primary image to a Jellyfin item — the raw image, base64-encoded in
        the body with the image content-type (Jellyfin's image-upload contract). Fetches
        the URL's bytes when only a URL is given. Returns {ok[, error]}.

        ``delete_key`` is accepted for a uniform signature but ignored: Jellyfin's Primary
        image is a single slot that this upload REPLACES, so it never accumulates like Plex."""
        import base64 as _b64
        import requests as _rq
        try:
            if image_bytes is None and image_url:
                image_bytes = _rq.get(image_url, timeout=20).content
            if not image_bytes:
                return {"ok": False, "error": "No image provided"}
            base = (self._c.base_url or "").rstrip("/")
            if not base:
                return {"ok": False, "error": "Jellyfin not configured"}
            headers = {"X-Emby-Token": self._c.api_key or "", "Content-Type": "image/jpeg"}
            r = _rq.post(base + "/Items/" + str(server_id) + "/Images/Primary",
                         data=_b64.b64encode(image_bytes), headers=headers, timeout=30)
            r.raise_for_status()
            return {"ok": True}
        except Exception as e:   # noqa: BLE001 - surface any Jellyfin/network error
            logger.exception("Jellyfin: set_poster failed for %s", server_id)
            return {"ok": False, "error": str(e)}

    # ── User metadata edits (Manage sidebar) ──────────────────────────────────
    # Jellyfin's edit contract is the full item-DTO round-trip (GET → mutate →
    # POST whole, same as set_collection_meta). Jellyfin natively supports
    # LockedFields — each pushed field that has a lock name is added there so
    # Jellyfin's own metadata refreshes don't undo the user's edit.
    _JF_LOCK_NAMES = {"title": "Name", "overview": "Overview", "genres": "Genres",
                      "content_rating": "OfficialRating", "studio": "Studios",
                      "network": "Studios"}

    def edit_item_metadata(self, server_id, changes: dict, kind: str = "movie",
                           unlock_fields=None) -> dict:
        import requests
        base, headers = self._jf()
        if not base:
            return {"ok": False, "error": "Jellyfin not configured"}
        try:
            item = self._req(f"/Users/{self.uid}/Items/{server_id}")
            if not item or not item.get("Id"):
                return {"ok": False, "error": "Item not found"}
            skipped = []
            for field, value in (changes or {}).items():
                if field == "title":
                    item["Name"] = value
                elif field == "sort_title":
                    item["SortName"] = value
                    item["ForcedSortName"] = value
                elif field == "year":
                    item["ProductionYear"] = value
                elif field == "content_rating":
                    item["OfficialRating"] = value
                elif field == "overview":
                    item["Overview"] = value
                elif field == "tagline":
                    item["Taglines"] = [value] if value else []
                elif field in ("studio", "network"):
                    item["Studios"] = [{"Name": value}] if value else []
                elif field == "genres":
                    item["Genres"] = [str(g) for g in (value or [])]
                else:
                    skipped.append(field)
            locks = set(item.get("LockedFields") or [])
            locks |= {self._JF_LOCK_NAMES[f] for f in (changes or {}) if f in self._JF_LOCK_NAMES}
            locks -= {self._JF_LOCK_NAMES[f] for f in (unlock_fields or []) if f in self._JF_LOCK_NAMES}
            item["LockedFields"] = sorted(locks)
            headers = dict(headers, **{"Content-Type": "application/json"})
            requests.post(base + f"/Items/{server_id}", json=item,
                          headers=headers, timeout=20).raise_for_status()
            return {"ok": True, "skipped": skipped}
        except Exception as e:   # noqa: BLE001 - surface any Jellyfin/network error
            logger.exception("Jellyfin: edit_item_metadata failed for %s", server_id)
            return {"ok": False, "error": str(e)}

    def set_watched(self, server_id, watched: bool, kind: str = "movie") -> dict:
        """Mark a Jellyfin item played/unplayed via the PlayedItems endpoint
        (a series marks all its episodes)."""
        import requests
        base, headers = self._jf()
        if not base:
            return {"ok": False, "error": "Jellyfin not configured"}
        try:
            url = base + f"/Users/{self.uid}/PlayedItems/{server_id}"
            r = (requests.post if watched else requests.delete)(url, headers=headers, timeout=20)
            r.raise_for_status()
            return {"ok": True}
        except Exception as e:   # noqa: BLE001
            logger.exception("Jellyfin: set_watched failed for %s", server_id)
            return {"ok": False, "error": str(e)}

    # ── Collections (BoxSets; SoulSync-managed) ───────────────────────────────
    def _jf(self):
        base = (self._c.base_url or "").rstrip("/")
        return base, {"X-Emby-Token": self._c.api_key or ""}

    def find_collection(self, kind: str, name: str):
        resp = self._req(f"/Users/{self.uid}/Items", params={
            "IncludeItemTypes": "BoxSet", "Recursive": "true", "SearchTerm": name}) or {}
        for it in resp.get("Items", []):
            if it.get("Name") == name and it.get("Id"):
                return str(it.get("Id"))
        return None

    def create_collection(self, kind: str, name: str, member_ids) -> dict:
        import requests
        base, headers = self._jf()
        if not base:
            return {"ok": False, "error": "Jellyfin not configured"}
        try:
            r = requests.post(base + "/Collections", headers=headers,
                              params={"Name": name, "Ids": ",".join(str(i) for i in member_ids)},
                              timeout=30)
            r.raise_for_status()
            data = r.json() if r.content else {}
            cid = data.get("Id")
            if not cid:
                return {"ok": False, "error": "Jellyfin returned no collection id"}
            return {"ok": True, "server_id": str(cid)}
        except Exception as e:   # noqa: BLE001
            logger.exception("Jellyfin: create_collection failed")
            return {"ok": False, "error": str(e)}

    def collection_member_ids(self, collection_id):
        """Member ids, or None if the BoxSet no longer exists."""
        item = self._req(f"/Users/{self.uid}/Items/{collection_id}")
        if not item or not item.get("Id"):
            return None   # gone (GET-only _make_request returns None on 404)
        resp = self._req(f"/Users/{self.uid}/Items",
                         params={"ParentId": str(collection_id), "Recursive": "false"}) or {}
        return [str(it.get("Id")) for it in resp.get("Items", []) if it.get("Id")]

    def collection_add(self, collection_id, ids) -> dict:
        import requests
        base, headers = self._jf()
        try:
            r = requests.post(base + f"/Collections/{collection_id}/Items", headers=headers,
                              params={"Ids": ",".join(str(i) for i in ids)}, timeout=30)
            r.raise_for_status()
            return {"ok": True}
        except Exception as e:   # noqa: BLE001
            logger.exception("Jellyfin: collection_add failed")
            return {"ok": False, "error": str(e)}

    def collection_remove(self, collection_id, ids) -> dict:
        import requests
        base, headers = self._jf()
        try:
            r = requests.delete(base + f"/Collections/{collection_id}/Items", headers=headers,
                                params={"Ids": ",".join(str(i) for i in ids)}, timeout=30)
            r.raise_for_status()
            return {"ok": True}
        except Exception as e:   # noqa: BLE001
            logger.exception("Jellyfin: collection_remove failed")
            return {"ok": False, "error": str(e)}

    def set_collection_meta(self, collection_id, *, poster_url=None, poster_bytes=None,
                            summary=None, sort=None, pinned=None, mode=None) -> dict:
        # Poster via the Primary-image endpoint; summary + display order via a
        # full item-DTO update (Jellyfin's edit contract: GET the DTO, mutate,
        # POST it back whole). Pin + 'mode' are Plex concepts — no equivalent.
        if poster_url or poster_bytes:
            r = self.set_poster(collection_id, image_url=poster_url,
                                image_bytes=poster_bytes, kind="collection")
            if not r.get("ok"):
                return r
        if summary or sort:
            import requests
            base, headers = self._jf()
            try:
                item = self._req(f"/Users/{self.uid}/Items/{collection_id}")
                if item and item.get("Id"):
                    if summary:
                        item["Overview"] = summary
                    disp = {"release": "PremiereDate", "alpha": "SortName"}.get(sort)
                    if disp:
                        item["DisplayOrder"] = disp
                    headers = dict(headers, **{"Content-Type": "application/json"})
                    requests.post(base + f"/Items/{collection_id}", json=item,
                                  headers=headers, timeout=20).raise_for_status()
            except Exception:   # noqa: BLE001 - meta is best-effort, never fail the sync
                logger.debug("Jellyfin: collection meta update failed (%s)",
                             collection_id, exc_info=True)
        return {"ok": True}

    def delete_collection(self, collection_id) -> dict:
        import requests
        base, headers = self._jf()
        try:
            r = requests.delete(base + f"/Items/{collection_id}", headers=headers, timeout=20)
            r.raise_for_status()
            return {"ok": True}
        except Exception as e:   # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def list_collections(self) -> list:
        """Every BoxSet on the server, for the server-cleanup view. Jellyfin
        BoxSets aren't per-library, so media_type is unknown (None).
        [{server_id, name, count, media_type, section}]."""
        resp = self._req(f"/Users/{self.uid}/Items", params={
            "IncludeItemTypes": "BoxSet", "Recursive": "true",
            "Fields": "ChildCount"}) or {}
        out = []
        for it in resp.get("Items", []):
            if not it.get("Id"):
                continue
            out.append({
                "server_id": str(it.get("Id")),
                "name": it.get("Name") or "",
                "count": int(it.get("ChildCount") or 0),
                "media_type": None,
                "section": None,
            })
        return out

    def is_scanning(self, media_type="all") -> bool:
        """True if Jellyfin's library-scan scheduled task is running. Jellyfin's
        scan isn't per-library, so media_type is ignored (any scan counts)."""
        import requests
        base = (self._c.base_url or "").rstrip("/")
        if not base:
            return False
        try:
            r = requests.get(base + "/ScheduledTasks",
                             headers={"X-Emby-Token": self._c.api_key or ""}, timeout=10)
            tasks = r.json() if r.ok else []
        except Exception:
            return False
        for task in tasks or []:
            name = (task.get("Name") or "").lower()
            if (("scan" in name or "refresh" in name or "library" in name)
                    and task.get("State") in ("Running", "Cancelling")):
                return True
        return False

    def has_item(self, media_type, item) -> bool:
        """True if Jellyfin already has this grab indexed. Conservative — matches the
        exact movie (name + year) or episode (series + SxE); any uncertainty → False."""
        import requests
        title = (item or {}).get("title")
        base = (self._c.base_url or "").rstrip("/")
        if not title or not base:
            return False
        headers = {"X-Emby-Token": self._c.api_key or ""}

        def _find(item_type):
            try:
                r = requests.get(base + "/Items", headers=headers, timeout=10, params={
                    "searchTerm": title, "IncludeItemTypes": item_type, "Recursive": "true",
                    "Fields": "ProductionYear", "Limit": 5})
                return (r.json() or {}).get("Items", []) if r.ok else []
            except Exception:
                return []

        if media_type == "movie":
            year = (item or {}).get("year")
            for it in _find("Movie"):
                py = it.get("ProductionYear")
                if year and py and abs(int(py) - int(year)) > 1:
                    continue
                return True
            return False
        if media_type == "show":
            sn, en = (item or {}).get("season_number"), (item or {}).get("episode_number")
            for series in _find("Series"):
                sid = series.get("Id")
                if not sid:
                    continue
                if sn is None or en is None:
                    return True
                try:
                    r = requests.get(base + "/Shows/" + str(sid) + "/Episodes", headers=headers,
                                     timeout=10, params={"season": int(sn)})
                    eps = (r.json() or {}).get("Items", []) if r.ok else []
                    if any(int(e.get("IndexNumber") or -1) == int(en) for e in eps):
                        return True
                except Exception:
                    continue
            return False
        return False

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
    def _files(item):
        """EVERY version (Jellyfin MediaSources = one entry per copy). [] when
        the item carries no source; a bare Path yields a minimal single entry."""
        sources = item.get("MediaSources") or []
        if not sources:
            path = item.get("Path")
            return [{"relative_path": path}] if path else []
        out = []
        for src in sources:
            streams = src.get("MediaStreams") or []
            vid = next((s for s in streams if s.get("Type") == "Video"), {})
            aud = next((s for s in streams if s.get("Type") == "Audio"), {})
            out.append({
                "relative_path": src.get("Path") or item.get("Path") or "",
                "size_bytes": src.get("Size"),
                "resolution": (str(vid.get("Height")) + "p") if vid.get("Height") else None,
                "aspect": vid.get("AspectRatio") or (
                    (vid.get("Width") / vid.get("Height")) if vid.get("Width") and vid.get("Height") else None),
                "video_codec": vid.get("Codec"),
                "audio_codec": aud.get("Codec"),
                "runtime_seconds": JellyfinVideoSource._ticks_to_seconds(item.get("RunTimeTicks")),
            })
        return out

    @classmethod
    def _file(cls, item):
        files = cls._files(item)
        return files[0] if files else None

    def iter_movies(self, incremental=False, since=None):
        path = f"/Users/{self.uid}/Items"
        for view in self._scan_views("movies", self._movies_lib):
            params = {"ParentId": view["Id"], "IncludeItemTypes": "Movie",
                      "Recursive": "true", "Fields": _JF_MOVIE_FIELDS}
            if incremental and since is not None:
                # Delta: DateLastSaved bumps when metadata is (re-)saved — a re-match or
                # edit on an EXISTING movie — so MinDateLastSaved catches changes, not
                # just new adds (the Jellyfin twin of Plex's updatedAt delta).
                params.update({"MinDateLastSaved": since.isoformat()})
                items = (self._req(path, params) or {}).get("Items", [])
            elif incremental:
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
            "play_count": int((it.get("UserData") or {}).get("PlayCount")
                              or (1 if (it.get("UserData") or {}).get("Played") else 0)),
            "last_viewed_at": _iso_dt((it.get("UserData") or {}).get("LastPlayedDate")),
            "genres": it.get("Genres") or [],
            "runtime_minutes": int(ticks / 600_000_000) if ticks else None,
            "files": self._files(it),
            "file": self._file(it),
        }
        d.update(_parse_jf_providers(it))
        return d

    def show_tree(self, server_id):
        """The full tree for ONE show — the per-show Synchronize. Jellyfin's
        request helper collapses every failure into None, so 'gone' is only
        believed when the item is missing while the server still answers a
        health probe; an unreachable server raises instead (a hiccup must
        never read as 'show removed')."""
        it = self._req(f"/Users/{self.uid}/Items/{server_id}",
                       {"Fields": _JF_SHOW_FIELDS})
        if it and it.get("Id"):
            if (it.get("Type") or "") != "Series":
                return None   # re-keyed to something else entirely — treat as gone
            return self._show(it)
        if self._req(f"/Users/{self.uid}/Views") is None:
            raise RuntimeError("Jellyfin unreachable — cannot verify the show's state")
        return None

    def iter_shows(self, incremental=False, since=None):
        path = f"/Users/{self.uid}/Items"
        for view in self._scan_views("tvshows", self._tv_lib):
            params = {"ParentId": view["Id"], "IncludeItemTypes": "Series",
                      "Recursive": "true", "Fields": _JF_SHOW_FIELDS}
            if incremental and since is not None:
                # Delta: metadata (re-)saved since the last scan — catches re-matches + edits.
                params.update({"MinDateLastSaved": since.isoformat()})
                items = (self._req(path, params) or {}).get("Items", [])
                # A Series' DateLastSaved does NOT bump when it gains an episode, so union in
                # the parent series of episodes saved since the baseline (the reliable new-
                # episode signal) — otherwise freshly-imported episodes are invisible.
                items = _union_jf_episode_delta_series(
                    self._req, self.uid, view["Id"], since, items, _JF_SHOW_FIELDS)
            elif incremental:
                # DateLastContentAdded bumps when a series gains episodes (DateCreated
                # is the series' original add-date and never moves). Fall back to DateCreated.
                params.update({"SortBy": "DateLastContentAdded,DateCreated",
                               "SortOrder": "Descending", "Limit": "200"})
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
                    "added_at": ((ep.get("DateCreated") or "").replace("T", " ")[:19] or None),
                    "files": self._files(ep),
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
