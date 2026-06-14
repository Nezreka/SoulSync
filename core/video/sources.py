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

from utils.logging_config import get_logger

logger = get_logger("video_sources")


def get_active_video_source():
    """Return a media source for the active server, or None when the active
    server has no video support (Navidrome/standalone) or isn't connected."""
    try:
        from config.settings import config_manager
        from core.media_server.engine import get_media_server_engine
    except Exception:
        logger.exception("video sources: shared infra unavailable")
        return None

    server = config_manager.get_active_media_server()
    engine = get_media_server_engine()
    if not engine:
        return None
    client = engine.client(server) if server in ("plex", "jellyfin") else None
    if not client:
        return None
    try:
        if not client.ensure_connection():
            return None
    except Exception:
        logger.exception("video sources: %s ensure_connection failed", server)
        return None

    if server == "plex" and getattr(client, "server", None) is not None:
        return PlexVideoSource(client)
    if server == "jellyfin" and getattr(client, "user_id", None):
        return JellyfinVideoSource(client)
    return None


# ── Plex ──────────────────────────────────────────────────────────────────────
class PlexVideoSource:
    server_name = "plex"

    def __init__(self, client):
        self._server = client.server

    def _sections(self, kind: str):
        return [s for s in self._server.library.sections() if s.type == kind]

    def iter_movies(self):
        for section in self._sections("movie"):
            for m in section.all():
                yield self._movie(m)

    def iter_shows(self):
        for section in self._sections("show"):
            for sh in section.all():
                yield self._show(sh)

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

    def _movie(self, m) -> dict:
        dur = getattr(m, "duration", None)
        return {
            "server_id": str(m.ratingKey),
            "title": m.title,
            "year": getattr(m, "year", None),
            "overview": getattr(m, "summary", None),
            "poster_url": getattr(m, "thumb", None),
            "content_rating": getattr(m, "contentRating", None),
            "studio": getattr(m, "studio", None),
            "runtime_minutes": int(dur / 60000) if dur else None,
            "file": self._part_file(m),
        }

    def _show(self, sh) -> dict:
        seasons = []
        try:
            for se in sh.seasons():
                episodes = []
                for ep in se.episodes():
                    dur = getattr(ep, "duration", None)
                    aired = getattr(ep, "originallyAvailableAt", None)
                    episodes.append({
                        "server_id": str(ep.ratingKey),
                        "season_number": ep.parentIndex if getattr(ep, "parentIndex", None) is not None
                        else getattr(se, "seasonNumber", 0),
                        "episode_number": getattr(ep, "index", 0),
                        "title": ep.title,
                        "overview": getattr(ep, "summary", None),
                        "air_date": aired.date().isoformat() if aired else None,
                        "runtime_minutes": int(dur / 60000) if dur else None,
                        "file": self._part_file(ep),
                    })
                seasons.append({
                    "server_id": str(se.ratingKey),
                    "season_number": getattr(se, "seasonNumber", 0),
                    "title": se.title,
                    "overview": getattr(se, "summary", None),
                    "poster_url": getattr(se, "thumb", None),
                    "episodes": episodes,
                })
        except Exception:
            logger.exception("Plex: failed reading seasons/episodes for %s",
                             getattr(sh, "title", "?"))
        return {
            "server_id": str(sh.ratingKey),
            "title": sh.title,
            "year": getattr(sh, "year", None),
            "overview": getattr(sh, "summary", None),
            "poster_url": getattr(sh, "thumb", None),
            "status": None,
            "network": getattr(sh, "network", None),
            "content_rating": getattr(sh, "contentRating", None),
            "seasons": seasons,
        }


# ── Jellyfin ────────────────────────────────────────────────────────────────
_JF_MOVIE_FIELDS = "Overview,Path,MediaSources,ProductionYear,OfficialRating,RunTimeTicks,Studios"
_JF_EP_FIELDS = "Overview,Path,MediaSources,PremiereDate,RunTimeTicks,IndexNumber,ParentIndexNumber"


class JellyfinVideoSource:
    server_name = "jellyfin"

    def __init__(self, client):
        self._c = client
        self.uid = client.user_id

    def _req(self, path, params=None):
        return self._c._make_request(path, params=params)

    def _views(self, collection_type: str):
        resp = self._req(f"/Users/{self.uid}/Views") or {}
        return [v for v in resp.get("Items", [])
                if (v.get("CollectionType") or "").lower() == collection_type]

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

    def iter_movies(self):
        for view in self._views("movies"):
            resp = self._req(f"/Users/{self.uid}/Items", {
                "ParentId": view["Id"], "IncludeItemTypes": "Movie",
                "Recursive": "true", "Fields": _JF_MOVIE_FIELDS}) or {}
            for it in resp.get("Items", []):
                yield self._movie(it)

    def _movie(self, it) -> dict:
        studios = it.get("Studios") or []
        ticks = it.get("RunTimeTicks")
        return {
            "server_id": str(it["Id"]),
            "title": it.get("Name"),
            "year": it.get("ProductionYear"),
            "overview": it.get("Overview"),
            "poster_url": (it.get("ImageTags") or {}).get("Primary"),
            "content_rating": it.get("OfficialRating"),
            "studio": studios[0].get("Name") if studios else None,
            "runtime_minutes": int(ticks / 600_000_000) if ticks else None,
            "file": self._file(it),
        }

    def iter_shows(self):
        for view in self._views("tvshows"):
            resp = self._req(f"/Users/{self.uid}/Items", {
                "ParentId": view["Id"], "IncludeItemTypes": "Series",
                "Recursive": "true", "Fields": "Overview,ProductionYear,OfficialRating"}) or {}
            for it in resp.get("Items", []):
                yield self._show(it)

    def _show(self, it) -> dict:
        series_id = str(it["Id"])
        seasons = []
        try:
            eps_resp = self._req(f"/Shows/{series_id}/Episodes", {
                "UserId": self.uid, "Fields": _JF_EP_FIELDS}) or {}
            by_season: dict[int, list] = {}
            for ep in eps_resp.get("Items", []):
                snum = ep.get("ParentIndexNumber") or 0
                aired = ep.get("PremiereDate")
                ticks = ep.get("RunTimeTicks")
                by_season.setdefault(snum, []).append({
                    "server_id": str(ep["Id"]),
                    "season_number": snum,
                    "episode_number": ep.get("IndexNumber") or 0,
                    "title": ep.get("Name"),
                    "overview": ep.get("Overview"),
                    "air_date": aired[:10] if aired else None,
                    "runtime_minutes": int(ticks / 600_000_000) if ticks else None,
                    "file": self._file(ep),
                })
            for snum, eps in sorted(by_season.items()):
                seasons.append({"server_id": None, "season_number": snum,
                                "title": None, "overview": None,
                                "poster_url": None, "episodes": eps})
        except Exception:
            logger.exception("Jellyfin: failed reading episodes for %s", it.get("Name", "?"))
        return {
            "server_id": series_id,
            "title": it.get("Name"),
            "year": it.get("ProductionYear"),
            "overview": it.get("Overview"),
            "poster_url": (it.get("ImageTags") or {}).get("Primary"),
            "status": None,
            "network": None,
            "content_rating": it.get("OfficialRating"),
            "seasons": seasons,
        }
