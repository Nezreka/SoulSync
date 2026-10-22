"""Anime suggestion for shows that are not in any library yet.

Where an episode is filed (TV folder vs anime folder) follows the show's series
type. For a show in a library the library decides: set by hand, or the show
sits in a library the admin marked as anime. A show that is followed, wished or
approved BEFORE it is owned has no library row, so nothing knows its type, and
its first episodes would land with TV.

So when such a show enters acquisition, SoulSync asks TMDB once and stores the
answer as the show's hint (``show_type_hints``, source 'tmdb'). The admin can
change it (source 'user'), and a TMDB suggestion never replaces a type the admin
chose. Once the show is scanned into a library, the library row decides and the
hint is no longer read.

TMDB has no Anime genre for TV. The signals are its ``anime`` keyword (applied
by TMDB's editors), or Animation together with Japanese as the original
language. Both are suggestions: an admin who files, say, an anthology of anime
shorts with TV changes it once.

``tmdb_suggests_anime`` is pure; the rest is best-effort and never raises.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("video.anime_hint")

#: TMDB's genre id for Animation (TV and movie lists share it).
TMDB_ANIMATION_GENRE_ID = 16


def tmdb_suggests_anime(raw: Any) -> bool:
    """True when a raw TMDB TV payload (with ``append_to_response=keywords``)
    suggests anime: an ``anime`` keyword, or Animation + Japanese original
    language. Pure; anything unexpected reads as False."""
    if not isinstance(raw, dict):
        return False
    kw = raw.get("keywords") or {}
    kwlist = (kw.get("results") or kw.get("keywords") or []) if isinstance(kw, dict) else []
    for k in kwlist:
        if isinstance(k, dict) and str(k.get("name") or "").strip().lower() == "anime":
            return True
    animation = False
    for g in raw.get("genres") or []:
        if not isinstance(g, dict):
            continue
        if g.get("id") == TMDB_ANIMATION_GENRE_ID or str(g.get("name") or "").strip().lower() == "animation":
            animation = True
            break
    return animation and str(raw.get("original_language") or "").strip().lower() == "ja"


def default_suggest(tmdb_id: int) -> Optional[bool]:
    """Ask TMDB through the enrichment engine's client. None when TMDB isn't
    configured or the call fails -- the caller then stores nothing, so the next
    follow tries again."""
    try:
        from core.video.enrichment.engine import get_video_enrichment_engine
        worker = get_video_enrichment_engine().workers.get("tmdb")
        if not worker or not worker.enabled:
            return None
        return worker.client.show_anime_hint(tmdb_id)
    except Exception:   # noqa: BLE001 - a suggestion is a nicety, never a failure
        logger.debug("anime hint: TMDB lookup failed for %s", tmdb_id, exc_info=True)
        return None


def ensure_show_type_hint(db, tmdb_id: Any,
                          suggest: Optional[Callable[[int], Optional[bool]]] = None) -> Optional[str]:
    """Make sure a show NOT in any library has a stored type; returns it.

    Owned shows return None (their library row decides). An existing hint is
    returned as is. Otherwise TMDB is asked once and the answer stored as
    'anime' or 'standard' (source 'tmdb'); when TMDB can't say, nothing is
    stored and None is returned. Never raises."""
    try:
        tmdb = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    try:
        if db.owns_show(tmdb):
            return None
        hint = db.show_type_hint(tmdb)
        if hint:
            return hint.get("series_type")
        answer = (suggest or default_suggest)(tmdb)
        if answer is None:
            return None
        st = "anime" if answer else "standard"
        db.set_show_type_hint(tmdb, st, source="tmdb")
        logger.info("anime hint: TMDB suggests %s for show %s", st, tmdb)
        return st
    except Exception:   # noqa: BLE001
        logger.exception("anime hint: could not ensure a type for show %s", tmdb_id)
        return None


def ensure_show_type_hint_later(db_provider: Callable[[], Any], tmdb_id: Any) -> None:
    """``ensure_show_type_hint`` on a daemon thread, so a follow or approve never
    waits on TMDB. Best-effort."""
    def _run():
        try:
            ensure_show_type_hint(db_provider(), tmdb_id)
        except Exception:   # noqa: BLE001
            logger.debug("anime hint: background ensure failed", exc_info=True)
    threading.Thread(target=_run, daemon=True, name="anime-hint-%s" % tmdb_id).start()


def note_show_wanted(db_provider: Callable[[], Any], tmdb_id: Any, chosen: Any = None) -> None:
    """A show just entered acquisition (followed, wished, approved). For a show
    in no library: store the type an admin ``chosen`` here (source 'user'),
    else -- only when an anime folder is set, so installs without one never
    call TMDB for this -- make sure TMDB's suggestion is stored, in the
    background. Owned shows are left to their library. Never raises."""
    try:
        tmdb = int(tmdb_id)
        db = db_provider()
        st = str(chosen or "").strip().lower()
        if st in ("standard", "daily", "anime"):
            if not db.owns_show(tmdb):
                db.set_show_type_hint(tmdb, st, source="user")
            return
        if db.get_setting("anime_path"):
            ensure_show_type_hint_later(db_provider, tmdb)
    except Exception:   # noqa: BLE001
        logger.debug("anime hint: note_show_wanted failed for %s", tmdb_id, exc_info=True)


__all__ = ["note_show_wanted", "tmdb_suggests_anime", "default_suggest", "ensure_show_type_hint",
           "ensure_show_type_hint_later", "TMDB_ANIMATION_GENRE_ID"]
