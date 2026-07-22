"""Import lists (arr-parity P6) — recurring auto-add from external lists.

Radarr/Sonarr's Import Lists: point at a list, and everything on it enters
acquisition automatically — new list members included, forever. Sources v1:

  · tmdb_list       — a public TMDB list id
  · tmdb_chart      — a living chart (trending_movies, top_shows, …) reusing
                      the collections engine's chart fetchers
  · imdb_list       — an IMDb ls-id, via the collections engine's IMDb
                      GraphQL fetcher
  · plex_watchlist  — the Plex ACCOUNT watchlist (best-effort: needs the
                      configured Plex token to be an account token, which it
                      normally is)

Each list: {id, name, source, ref, media movie|show|both, monitor (P2 policy
for shows), quality_profile_id (P2, stamped on added movies), limit, enabled}.
Stored in video_settings['import_lists'].

Sync semantics (the part Radarr gets wrong-by-default): a per-list SEEN set
means only members NEW to the list are added — removing something you didn't
want stays removed instead of boomeranging back every sync. Adds are the same
idempotent wishlist/watchlist writes everything else uses; show policy
expansion is capped per sync so a 500-show list can't stampede TMDB.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.import_lists")

_KEY = "import_lists"
SOURCES = ("tmdb_list", "tmdb_chart", "imdb_list", "plex_watchlist")
MEDIA = ("movie", "show", "both")
MAX_LISTS = 32
_EXPANSION_CAP = 10          # shows whose monitor policy expands per sync tick

_running = False
_lock = threading.Lock()


def is_running() -> bool:
    return _running


# ── config store ──────────────────────────────────────────────────────────────

def normalize_list(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "").strip().lower()
    ref = str(raw.get("ref") or "").strip()
    if source not in SOURCES or (source != "plex_watchlist" and not ref):
        return None
    from core.video.monitor_policy import POLICIES
    monitor = str(raw.get("monitor") or "future").lower()
    media = str(raw.get("media") or "both").lower()
    try:
        limit = max(1, min(500, int(raw.get("limit", 100))))
    except (TypeError, ValueError):
        limit = 100
    try:
        qpid = int(raw.get("quality_profile_id") or 0) or None
    except (TypeError, ValueError):
        qpid = None
    lid = raw.get("id")
    return {"id": int(lid) if isinstance(lid, (int, float)) and int(lid) >= 1 else None,
            "name": (str(raw.get("name") or "").strip() or source)[:80],
            "source": source, "ref": ref[:200],
            "media": media if media in MEDIA else "both",
            "monitor": monitor if monitor in POLICIES else "future",
            "quality_profile_id": qpid,
            "limit": limit,
            "enabled": raw.get("enabled", True) is not False}


def load_lists(db) -> List[Dict[str, Any]]:
    try:
        rows = json.loads(db.get_setting(_KEY) or "[]")
    except (ValueError, TypeError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        n = normalize_list(r)
        if n and n["id"]:
            out.append(n)
    return out


def save_list(db, raw: Any) -> Optional[Dict[str, Any]]:
    n = normalize_list(raw)
    if not n:
        return None
    rows = load_lists(db)
    if n["id"] is None:
        n["id"] = max([0] + [r["id"] for r in rows]) + 1
        rows.append(n)
    else:
        rows = [r for r in rows if r["id"] != n["id"]] + [n]
    db.set_setting(_KEY, json.dumps(rows[:MAX_LISTS]))
    return n


def delete_list(db, list_id: Any) -> bool:
    try:
        lid = int(list_id)
    except (TypeError, ValueError):
        return False
    rows = load_lists(db)
    kept = [r for r in rows if r["id"] != lid]
    if len(kept) == len(rows):
        return False
    db.set_setting(_KEY, json.dumps(kept))
    db.set_setting("import_list_seen_%d" % lid, "[]")
    return True


def _seen(db, lid: int) -> set:
    try:
        return set(json.loads(db.get_setting("import_list_seen_%d" % lid) or "[]"))
    except (ValueError, TypeError):
        return set()


def _mark_seen(db, lid: int, keys: set) -> None:
    db.set_setting("import_list_seen_%d" % lid, json.dumps(sorted(keys)[-2000:]))


# ── member fetching (engine-injected; collections fetchers reused) ───────────

def _fetch_members(entry: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Normalized members [{kind, tmdb_id, title, year, poster_url}] or None on
    a fetch failure (None = don't touch the seen-set; [] = truly empty)."""
    from core.video.collections import list_sources as ls
    from core.video.enrichment.engine import get_video_enrichment_engine
    eng = get_video_enrichment_engine()
    src, ref, limit = entry["source"], entry["ref"], entry["limit"]
    try:
        if src == "tmdb_chart":
            items = ls._fetch_chart(eng, {"chart": ref, "limit": limit})
        elif src == "tmdb_list":
            items = ls._fetch_list(eng, ref)
        elif src == "imdb_list":
            items = ls._fetch_imdb(eng, {"url": ref, "limit": limit})
        elif src == "plex_watchlist":
            items = _fetch_plex_watchlist(limit)
        else:
            return []
        return [m for m in (items or []) if m.get("tmdb_id")]
    except Exception:   # noqa: BLE001 - a broken list skips this tick, never crashes the sync
        logger.warning("import list %r (%s) fetch failed", entry.get("name"), src, exc_info=True)
        return None


def _fetch_plex_watchlist(limit: int) -> List[Dict[str, Any]]:
    """The Plex ACCOUNT watchlist via plexapi. Needs an account token."""
    from config.settings import config_manager
    from plexapi.myplex import MyPlexAccount
    token = config_manager.get("plex.token", "") or ""
    if not token:
        return []
    account = MyPlexAccount(token=token)
    out: List[Dict[str, Any]] = []
    for item in account.watchlist(maxresults=limit):
        tmdb = None
        for guid in getattr(item, "guids", []) or []:
            gid = str(getattr(guid, "id", "") or "")
            if gid.startswith("tmdb://"):
                tmdb = int(gid.split("//", 1)[1])
                break
        if not tmdb:
            continue
        out.append({"kind": "show" if getattr(item, "TYPE", "") == "show" else "movie",
                    "tmdb_id": tmdb, "title": getattr(item, "title", "?"),
                    "year": getattr(item, "year", None), "poster_url": None})
    return out


# ── the sync ──────────────────────────────────────────────────────────────────

def sync(*, fetch: Optional[Callable[[Dict[str, Any]], Optional[List[Dict[str, Any]]]]] = None,
         log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    global _running
    with _lock:
        if _running:
            return {"status": "skipped", "reason": "already_running"}
        _running = True
    try:
        return _sync_inner(fetch or _fetch_members, log or (lambda m: None))
    finally:
        with _lock:
            _running = False


def _sync_inner(fetch, log) -> Dict[str, Any]:
    from api.video import get_video_db
    db = get_video_db()
    lists = [e for e in load_lists(db) if e["enabled"]]
    if not lists:
        return {"status": "skipped", "reason": "no_lists", "added": 0}

    added_movies = added_shows = 0
    expansions = 0
    for entry in lists:
        members = fetch(entry)
        if members is None:
            continue                      # fetch failed — seen-set untouched, retried next tick
        seen = _seen(db, entry["id"])
        fresh_keys = set(seen)
        for m in members:
            kind = m.get("kind") if m.get("kind") in ("movie", "show") else "movie"
            if entry["media"] != "both" and kind != entry["media"]:
                continue
            key = "%s:%s" % (kind, m["tmdb_id"])
            fresh_keys.add(key)
            if key in seen:
                continue                  # user removals never boomerang back
            if kind == "movie":
                if db.add_movie_to_wishlist(m["tmdb_id"], m.get("title") or "?",
                                            year=m.get("year"), poster_url=m.get("poster_url")):
                    added_movies += 1
                    if entry.get("quality_profile_id"):
                        _stamp_movie_profile(db, m["tmdb_id"], entry["quality_profile_id"])
                    log("List '%s': wishlisted %s" % (entry["name"], m.get("title")))
            else:
                if db.add_to_watchlist("show", m["tmdb_id"], m.get("title") or "?",
                                       poster_url=m.get("poster_url")):
                    added_shows += 1
                    log("List '%s': following %s" % (entry["name"], m.get("title")))
                    if entry["monitor"] != "future" and expansions < _EXPANSION_CAP:
                        expansions += 1
                        _expand_policy(db, m, entry["monitor"])
        _mark_seen(db, entry["id"], fresh_keys)
    return {"status": "completed", "lists": len(lists),
            "added_movies": added_movies, "added_shows": added_shows}


def _stamp_movie_profile(db, tmdb_id, qpid) -> None:
    try:
        conn = db._get_connection()
        try:
            conn.execute("UPDATE video_wishlist SET quality_profile_id=? "
                         "WHERE kind='movie' AND tmdb_id=? AND quality_profile_id IS NULL",
                         (int(qpid), int(tmdb_id)))
            conn.commit()
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        logger.debug("profile stamp failed for %s", tmdb_id, exc_info=True)


def _expand_policy(db, member, monitor) -> None:
    try:
        from datetime import date

        from core.video.enrichment.engine import get_video_enrichment_engine
        from core.video.monitor_policy import episodes_for_policy
        eps = episodes_for_policy(get_video_enrichment_engine(), int(member["tmdb_id"]),
                                  monitor, date.today().isoformat())
        if eps:
            db.add_episodes_to_wishlist(int(member["tmdb_id"]), member.get("title") or "?",
                                        eps, poster_url=member.get("poster_url"))
    except Exception:   # noqa: BLE001 - expansion is best-effort
        logger.debug("policy expansion failed for %s", member.get("tmdb_id"), exc_info=True)
