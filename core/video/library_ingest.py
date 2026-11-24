"""a download is in the library the moment it lands.

the importer knows exactly what it placed: the title's tmdb id, the season
and episode, the file. so the title and file rows are written right away,
the way the music side writes a track when its download finishes, instead of
waiting for plex or jellyfin to scan the file in (or never, with no server).

rows soulsync makes here carry an ``ss:`` id (core/video/owned_ids.py) under
the library's label (the video server, or 'soulsync' with none). when the
server's scan later reports the title it takes the row over, so nothing is
listed twice; until then no scan prunes it while its file is on disk.

called from every place a video download completes: the download monitor,
season packs, the import folder and manual import. best effort: a failure
here never touches the import itself.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("video_library_ingest")


def _int(v) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ctx(dl: dict) -> dict:
    ctx = (dl or {}).get("search_ctx")
    if isinstance(ctx, dict):
        return ctx
    try:
        v = json.loads(ctx or "{}")
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def library_label(db) -> str:
    """what the library's rows are filed under: the video server, or
    soulsync's own when there is none"""
    try:
        from core.video.sources import resolve_video_server
        return resolve_video_server(db) or "soulsync"
    except Exception:   # noqa: BLE001 - no server config readable: soulsync's own
        return "soulsync"


def file_facts(path: str, dl: dict) -> Dict[str, Any]:
    """what's known about the placed file without a server: its size, and
    resolution, source, codec, HDR and Atmos from its release name. audio and
    subtitle languages come from the probe the importer kept."""
    from core.video.release_parse import parse_release
    from core.video.sources import _format_from_name
    name = os.path.basename(path)
    rel = parse_release(dl.get("release_title") or name)
    own = parse_release(name)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = _int(dl.get("size_bytes"))
    out = {
        "relative_path": path,
        "size_bytes": size,
        "resolution": own.get("resolution") or rel.get("resolution"),
        "release_source": own.get("source") or rel.get("source"),
        "video_codec": own.get("codec") or rel.get("codec"),
        "quality": dl.get("quality_label") or None,
    }
    out.update(_format_from_name(dl.get("release_title") or name))
    out.update(_format_from_name(name))
    return out


def ingest_landed(db, dl: dict, dest_path: str, *, replaced: Optional[str] = None) -> Optional[dict]:
    """write a placed movie or episode into the library. returns
    ``{kind, id}`` (the movie or episode row), or None when it isn't a
    library title (youtube, an unknown identity). never raises."""
    try:
        return _ingest(db, dl or {}, dest_path, replaced)
    except Exception:   # noqa: BLE001 - the file is placed; the next scan still finds it
        logger.exception("video download %s: couldn't write it into the library", (dl or {}).get("id"))
        return None


def _ingest(db, dl: dict, dest_path: str, replaced: Optional[str]) -> Optional[dict]:
    if not dest_path or not os.path.isfile(dest_path):
        return None
    kind = str(dl.get("kind") or "").lower()
    if kind == "youtube":
        return None
    ctx = _ctx(dl)
    scope = str(ctx.get("scope") or "").lower()
    from_library = str(dl.get("media_source") or "").lower() == "library"
    media_id = _int(dl.get("media_id"))
    label = library_label(db)
    from core.video import owned_ids
    from core.video.library_roots import library_roots
    roots = library_roots(db)
    lib_kind = "movie" if kind == "movie" else "show"
    library_id = None
    try:
        from core.video.libraries import library_for_path
        lib = library_for_path(db, dest_path, lib_kind)
        library_id = lib["id"] if lib else None
    except Exception:   # noqa: BLE001 - no libraries set up: no library
        library_id = None
    facts = file_facts(dest_path, dl)
    title = ctx.get("title") or dl.get("title")
    year = _int(ctx.get("year") if ctx.get("year") is not None else dl.get("year"))
    poster = dl.get("poster_url") or None
    if poster and not str(poster).startswith(("http://", "https://")):
        poster = None

    if kind == "movie":
        tmdb_id = None if from_library else media_id
        if from_library:
            tmdb_id = db.movie_tmdb_id(media_id) if media_id else None
        if not tmdb_id and not (from_library and media_id):
            return None
        mid = db.own_landed_movie(
            label, owned_id=owned_ids.movie_id(dest_path, roots), file=facts, tmdb_id=tmdb_id,
            row_id=media_id if from_library else None, title=title, year=year, poster_url=poster,
            library_id=library_id, replaced=replaced)
        _changed()
        return {"kind": "movie", "id": mid}

    season, episode = _int(ctx.get("season")), _int(ctx.get("episode"))
    if season is None or episode is None or scope in ("season", "series"):
        return None
    tmdb_id = db.show_tmdb_id(media_id) if from_library and media_id else media_id
    if not tmdb_id and not (from_library and media_id):
        return None
    show_id, ep_id = db.own_landed_episode(
        label, show_owned_id=owned_ids.show_id(dest_path, roots), file=facts,
        season=season, episode=episode, tmdb_id=tmdb_id,
        show_row_id=media_id if from_library else None, show_title=title, year=year,
        poster_url=poster, episode_title=ctx.get("episode_title"), air_date=ctx.get("air_date"),
        library_id=library_id, replaced=replaced)
    _changed()
    return {"kind": "episode", "id": ep_id, "show_id": show_id}


def _changed() -> None:
    """the library pages sum the library up from memory: tell them"""
    try:
        from core.video import library_manager
        library_manager.expire()
    except Exception:   # noqa: BLE001 - a stale page is not a failed import
        logger.debug("library manager not told about a landed file", exc_info=True)
