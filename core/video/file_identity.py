"""what a file is, from the file itself, when its name says nothing.

a movie in a folder called 'new folder (2)' can't be matched by name. its
fingerprint (the opensubtitles hash: the file's size and the sums of its
first and last 64 KB) can: opensubtitles knows which film a release with
that exact fingerprint is. used by soulsync's folder scan
(core/video/disk_source.py) as the last step before a title is left for the
person, and only when the answer is a single film.

needs the opensubtitles api key the subtitles side already uses; without one
there is no lookup and the title simply waits for review.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("video_file_identity")


def identity_from_search(found: Any) -> Optional[Dict[str, Any]]:
    """{tmdb_id, imdb_id} from an opensubtitles /subtitles?moviehash= answer:
    the one film every fingerprint-matched row names, or None when no row
    matched by fingerprint or they disagree. pure."""
    films = {}
    for row in (found or {}).get("data") or []:
        attrs = (row or {}).get("attributes") or {}
        if attrs.get("moviehash_match") is not True:
            continue
        feat = attrs.get("feature_details") or {}
        if str(feat.get("feature_type") or "").lower() != "movie":
            continue
        tmdb, imdb = feat.get("tmdb_id"), feat.get("imdb_id")
        if not (tmdb or imdb):
            continue
        key = ("tmdb", tmdb) if tmdb else ("imdb", imdb)
        films[key] = {"tmdb_id": int(tmdb) if tmdb else None,
                      "imdb_id": ("tt%07d" % int(imdb)) if imdb else None}
    if len(films) != 1:
        return None
    return next(iter(films.values()))


def hash_lookup(db, *, get_json: Optional[Callable] = None,
                hasher: Optional[Callable] = None) -> Optional[Callable]:
    """``lookup(path) -> {tmdb_id, imdb_id} | None`` backed by opensubtitles,
    or None when there is no api key"""
    try:
        key = str(db.get_setting("opensubtitles_api_key") or "").strip()
    except Exception:   # noqa: BLE001
        key = ""
    if not key:
        return None
    from core.video.subtitles.providers import opensubtitles as osub
    from core.video.subtitles.scoring import opensubtitles_hash
    get_json = get_json or osub._get_json
    hasher = hasher or opensubtitles_hash

    def lookup(path: str):
        h = hasher(path)
        if not h:
            return None
        try:
            found = get_json(osub.BASE + "/subtitles", {"moviehash": h}, osub._headers(key))
        except Exception:   # noqa: BLE001 - an assist: the title waits for review instead
            logger.debug("fingerprint lookup failed for %s", path, exc_info=True)
            return None
        return identity_from_search(found)

    return lookup
