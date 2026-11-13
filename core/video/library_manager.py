"""The video library manager's reads: every title and every file, as rows.

The library page was a wall of posters with five filters. A manager needs the
facts a person sorts and filters a collection by: what each title is made of
on disk (resolution, codec, HDR, languages, size, where it sits), what it is
missing, and which ones need a look. This module answers those questions in
SQL, one page at a time, for three views:

- ``titles``  one row per movie or show, with its files summed up
- ``files``   one row per video file, with the title it belongs to
- ``facets``  what there is to filter by, with counts

Nothing here writes. The bulk operations live in ``library_manager_ops``.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("video.library_manager")

KINDS = ("movies", "shows")

#: resolution as a rank: 4 = 4K, 3 = 1080p, 2 = 720p, 1 = SD, 0 = unknown.
#: the same ladder quality_eval.resolution_rank climbs, in SQL
RES_LABELS = {4: "4K", 3: "1080p", 2: "720p", 1: "SD", 0: ""}
_RES_BY_NAME = {"4k": 4, "2160": 4, "2160p": 4, "uhd": 4, "1080": 3, "1080p": 3,
                "720": 2, "720p": 2, "sd": 1, "480": 1, "480p": 1, "576": 1, "576p": 1}


def res_rank_sql(col: str) -> str:
    c = f"LOWER(COALESCE({col}, ''))"
    return (f"(CASE WHEN {c} IN ('4k','2160','2160p','2k','1440','1440p','uhd') THEN 4 "
            f"WHEN {c} LIKE '1080%' THEN 3 WHEN {c} LIKE '720%' THEN 2 "
            f"WHEN {c} = '' THEN 0 ELSE 1 END)")


_FILE_SORTS = {
    "title": ("title_sort COLLATE NOCASE", "asc"),
    "size": ("mf.size_bytes", "desc"),
    "resolution": ("res_rank", "desc"),
    "codec": ("LOWER(COALESCE(mf.video_codec, ''))", "asc"),
    "added": ("mf.added_at", "desc"),
    "path": ("mf.relative_path COLLATE NOCASE", "asc"),
    "runtime": ("mf.runtime_seconds", "desc"),
}

#: the "needs a look" filters, in the order the page lists them
NEEDS = ("missing", "below_cutoff", "copies", "unmatched", "no_subtitles",
         "unprobed", "unmonitored", "unwatched", "not_owned")


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _langs(value: Any) -> List[str]:
    """'ja,en|en' (several files' lists joined) as a tidy unique list."""
    seen: List[str] = []
    for part in re.split(r"[,|]", str(value or "")):
        code = part.strip().lower()
        if code and code not in seen:
            seen.append(code)
    return seen


def _uniq(value: Any) -> List[str]:
    seen: List[str] = []
    for part in str(value or "").split("|"):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


def _locked(value: Any) -> List[str]:
    try:
        data = json.loads(value) if value else []
    except (TypeError, ValueError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


# ── quality cutoffs ──────────────────────────────────────────────────────────

def _cutoff_ranks(db, kind: str) -> Tuple[int, Dict[int, int]]:
    """(the rank a title with no profile of its own is held to, {profile id: rank}).
    0 means "no cutoff": nothing is below it."""
    from core.video import quality_profile as qp
    from core.video.quality_eval import resolution_rank

    def rank(profile) -> int:
        return resolution_rank((profile or {}).get("cutoff_resolution") or "")

    title_kind = "movie" if kind == "movies" else "show"
    try:
        default = rank(qp.profile_by_id(db, None, kind=title_kind))
    except Exception:   # noqa: BLE001 - no profiles readable: nothing is below a cutoff
        default = 0
    named: Dict[int, int] = {}
    try:
        for p in qp.list_profiles(db):
            pid = _int(p.get("id"))
            if pid:
                named[pid] = rank(qp.profile_by_id(db, pid, kind=title_kind))
    except Exception:   # noqa: BLE001
        logger.debug("named quality profiles not read", exc_info=True)
    return default, named


def _cutoff_sql(default: int, named: Dict[int, int]) -> str:
    if not named:
        return str(int(default))
    whens = " ".join(f"WHEN {int(pid)} THEN {int(r)}" for pid, r in sorted(named.items()))
    return f"(CASE t.quality_profile_id {whens} ELSE {int(default)} END)"


# ── the aggregates a title row is built from ─────────────────────────────────

def _movie_files_cte(scope: str = "") -> str:
    rr = res_rank_sql("mf.resolution")
    return f"""
        f AS (
          SELECT mf.movie_id AS tid, COUNT(*) AS files,
                 COALESCE(SUM(mf.size_bytes), 0) AS size_bytes,
                 MAX({rr}) AS best, MIN({rr}) AS worst,
                 GROUP_CONCAT(DISTINCT LOWER(mf.video_codec)) AS vcodecs,
                 GROUP_CONCAT(DISTINCT LOWER(mf.audio_codec)) AS acodecs,
                 GROUP_CONCAT(DISTINCT mf.dynamic_range) AS hdr,
                 GROUP_CONCAT(DISTINCT mf.release_source) AS sources,
                 MAX(mf.audio_channels) AS channels, MAX(COALESCE(mf.atmos, 0)) AS atmos,
                 REPLACE(GROUP_CONCAT(DISTINCT mf.audio_languages), ',', '|') AS alangs,
                 REPLACE(GROUP_CONCAT(DISTINCT mf.subtitle_languages), ',', '|') AS slangs,
                 SUM(CASE WHEN mf.audio_languages IS NULL THEN 1 ELSE 0 END) AS unprobed,
                 SUM(CASE WHEN COALESCE(mf.subtitle_languages, '') = '' THEN 1 ELSE 0 END) AS nosubs,
                 MIN(mf.relative_path) AS path, COUNT(*) - 1 AS extra,
                 SUM({rr} = 4) AS r4, SUM({rr} = 3) AS r3, SUM({rr} = 2) AS r2, SUM({rr} = 1) AS r1
            FROM media_files mf WHERE mf.movie_id IS NOT NULL{scope} GROUP BY mf.movie_id)"""


def _show_ctes(e_scope: str = "", f_scope: str = "") -> str:
    rr = res_rank_sql("mf.resolution")
    return f"""
        e AS (
          SELECT ep.show_id AS tid,
                 COUNT(DISTINCT CASE WHEN ep.season_number > 0 THEN ep.season_number END) AS seasons,
                 SUM(ep.season_number > 0) AS total,
                 SUM(ep.season_number > 0 AND ep.has_file = 1) AS owned,
                 SUM(ep.season_number > 0 AND ep.has_file = 0 AND ep.monitored = 1
                     AND ep.air_date IS NOT NULL
                     AND substr(ep.air_date, 1, 10) <= date('now')) AS missing
            FROM episodes ep{e_scope} GROUP BY ep.show_id),
        f AS (
          SELECT ep.show_id AS tid, COUNT(*) AS files,
                 COALESCE(SUM(mf.size_bytes), 0) AS size_bytes,
                 MAX({rr}) AS best, MIN({rr}) AS worst,
                 GROUP_CONCAT(DISTINCT LOWER(mf.video_codec)) AS vcodecs,
                 GROUP_CONCAT(DISTINCT LOWER(mf.audio_codec)) AS acodecs,
                 GROUP_CONCAT(DISTINCT mf.dynamic_range) AS hdr,
                 GROUP_CONCAT(DISTINCT mf.release_source) AS sources,
                 MAX(mf.audio_channels) AS channels, MAX(COALESCE(mf.atmos, 0)) AS atmos,
                 REPLACE(GROUP_CONCAT(DISTINCT mf.audio_languages), ',', '|') AS alangs,
                 REPLACE(GROUP_CONCAT(DISTINCT mf.subtitle_languages), ',', '|') AS slangs,
                 SUM(CASE WHEN mf.audio_languages IS NULL THEN 1 ELSE 0 END) AS unprobed,
                 SUM(CASE WHEN COALESCE(mf.subtitle_languages, '') = '' THEN 1 ELSE 0 END) AS nosubs,
                 MIN(mf.relative_path) AS path,
                 COUNT(*) - COUNT(DISTINCT mf.episode_id) AS extra,
                 SUM({rr} = 4) AS r4, SUM({rr} = 3) AS r3, SUM({rr} = 2) AS r2, SUM({rr} = 1) AS r1
            FROM media_files mf JOIN episodes ep ON ep.id = mf.episode_id{f_scope}
           GROUP BY ep.show_id)"""


# ── the snapshot: every title of one side, summed up once ────────────────────
#
# summing a show's files means reading its episodes and their files: 224k and
# 112k rows on a big library, 14 seconds a page when done per request. so each
# side is summed once into a list of plain rows, kept in memory, and every
# filter, sort and page is worked out over that list (a few thousand dicts).
# a write through the manager refreshes just the titles it touched; a scan
# forgets the lot; anything older than the TTL is rebuilt in the background
# while the old rows keep answering.

_SNAP_TTL = 300.0
_snapshots: Dict[tuple, Dict[str, Any]] = {}
_snap_lock = threading.Lock()


def forget(db=None) -> None:
    """drop every summed-up row (a scan ran, files moved underneath us)"""
    with _snap_lock:
        if db is None:
            _snapshots.clear()
        else:
            path = str(getattr(db, "database_path", ""))
            for key in [k for k in _snapshots if k[0] == path]:
                _snapshots.pop(key, None)
    forget_facets()


def _snap_key(db, kind: str, server_source) -> tuple:
    return (str(getattr(db, "database_path", "")), kind, server_source or "")


def _build_rows(db, kind: str, server_source, ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """the summed-up rows of one side (or of just ``ids``), straight from SQL"""
    from core.video.drive_map import drive_root

    is_shows = kind == "shows"
    tbl = "shows" if is_shows else "movies"
    default_cut, named_cut = _cutoff_ranks(db, kind)
    cutoff = _cutoff_sql(default_cut, named_cut)
    where: List[str] = []
    params: List[Any] = []
    scope = ""
    if ids is not None:
        marks = "SELECT value FROM json_each(?)"
        scope = json.dumps([int(i) for i in ids])
    if is_shows:
        e_scope = f" WHERE ep.show_id IN ({marks})" if ids is not None else ""
        f_scope = f" WHERE ep.show_id IN ({marks})" if ids is not None else ""
        ctes = _show_ctes(e_scope, f_scope)
        joins = "LEFT JOIN e ON e.tid = t.id LEFT JOIN f ON f.tid = t.id"
        cols = ("e.seasons, e.total, e.owned, e.missing, t.network, t.series_type, "
                "t.watched_episodes AS plays, ")
        gjoin = ("(SELECT GROUP_CONCAT(g.name, '|') FROM show_genres xg JOIN genres g "
                 "ON g.id = xg.genre_id WHERE xg.show_id = t.id)")
        if ids is not None:
            params += [scope, scope]
    else:
        f_scope = f" AND mf.movie_id IN ({marks})" if ids is not None else ""
        ctes = _movie_files_cte(f_scope)
        joins = "LEFT JOIN f ON f.tid = t.id"
        cols = "t.has_file, t.studio, t.play_count AS plays, "
        gjoin = ("(SELECT GROUP_CONCAT(g.name, '|') FROM movie_genres xg JOIN genres g "
                 "ON g.id = xg.genre_id WHERE xg.movie_id = t.id)")
        if ids is not None:
            params += [scope]
    if server_source:
        where.append("t.server_source = ?")
        params.append(server_source)
    if ids is not None:
        where.append(f"t.id IN ({marks})")
        params.append(scope)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    conn = db._get_connection()
    try:
        rows = conn.execute(
            f"WITH {ctes} SELECT t.id, t.title, t.sort_title, t.year, t.tmdb_id, t.monitored, "
            f"t.added_at, t.content_rating, t.runtime_minutes, t.rating, t.imdb_rating, "
            f"t.poster_url, t.quality_profile_id, t.video_library_id, t.locked_fields, t.hands_off, t.status, "
            f"{cols}{gjoin} AS genres, {cutoff} AS cutoff, f.* "
            f"FROM {tbl} t {joins}{where_sql}", params).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        facts = _file_facts(r, drive_root)
        cut = int(r["cutoff"] or 0)
        judged = int(r["worst"] or 0) if is_shows else facts["resolution_rank"]
        item = {
            "id": r["id"], "kind": "show" if is_shows else "movie", "title": r["title"],
            "sort_title": (r["sort_title"] or r["title"] or "").lower(),
            "year": r["year"], "tmdb_id": r["tmdb_id"], "matched": r["tmdb_id"] is not None,
            "monitored": bool(r["monitored"]), "added_at": r["added_at"],
            "content_rating": r["content_rating"], "runtime_minutes": r["runtime_minutes"],
            "rating": r["imdb_rating"] if r["imdb_rating"] is not None else r["rating"],
            "has_poster": bool(r["poster_url"]),
            "quality_profile_id": r["quality_profile_id"],
            "video_library_id": r["video_library_id"],
            "locked_fields": _locked(r["locked_fields"]),
            "locked": bool(r["hands_off"]),
            "status": r["status"], "watched": bool(r["plays"]),
            "genres": _uniq(r["genres"]),
            "below_cutoff": bool(cut and facts["file_count"] and 0 < judged < cut),
            "cutoff": RES_LABELS.get(cut, ""),
            **facts,
        }
        if is_shows:
            item.update({
                "seasons": int(r["seasons"] or 0), "episodes_total": int(r["total"] or 0),
                "episodes_owned": int(r["owned"] or 0), "episodes_missing": int(r["missing"] or 0),
                "network": r["network"], "series_type": r["series_type"],
                "owned": int(r["owned"] or 0) > 0,
            })
        else:
            item.update({"owned": bool(r["has_file"]), "studio": r["studio"]})
        out.append(item)
    return out


def _file_facts(row: Any, drive_of) -> Dict[str, Any]:
    """what a title's files add up to, from one aggregate row"""
    best = int(row["best"] or 0)
    path = row["path"]

    def listed(value: Any) -> List[str]:
        return _uniq(str(value or "").replace(",", "|"))

    return {
        "file_count": int(row["files"] or 0),
        "size_bytes": int(row["size_bytes"] or 0),
        "resolution": RES_LABELS.get(best, ""),
        "resolution_rank": best,
        "resolution_mix": {RES_LABELS[k]: int(row[f"r{k}"] or 0) for k in (4, 3, 2, 1)
                           if int(row[f"r{k}"] or 0)},
        "video_codecs": listed(row["vcodecs"]),
        "audio_codecs": listed(row["acodecs"]),
        "dynamic_range": listed(row["hdr"]),
        "release_sources": listed(row["sources"]),
        "audio_channels": row["channels"],
        "atmos": bool(row["atmos"]),
        "audio_languages": _langs(row["alangs"]),
        "subtitle_languages": _langs(row["slangs"]),
        "extra_copies": int(row["extra"] or 0),
        "unprobed_files": int(row["unprobed"] or 0),
        "files_without_subtitles": int(row["nosubs"] or 0),
        "path": path,
        "drive": drive_of(path) if path else None,
    }


def _snapshot(db, kind: str, server_source) -> List[Dict[str, Any]]:
    key = _snap_key(db, kind, server_source)
    now = time.monotonic()
    with _snap_lock:
        snap = _snapshots.get(key)
        fresh = snap is not None and now - snap["at"] < _SNAP_TTL
        refreshing = bool(snap and snap.get("building"))
        if snap is not None and not fresh and not refreshing:
            snap["building"] = True
    if snap is None:
        rows = _build_rows(db, kind, server_source)
        with _snap_lock:
            _snapshots[key] = {"at": time.monotonic(), "rows": rows, "building": False}
        return rows
    if not fresh and not refreshing:
        def rebuild():
            try:
                rows = _build_rows(db, kind, server_source)
                with _snap_lock:
                    _snapshots[key] = {"at": time.monotonic(), "rows": rows, "building": False}
            except Exception:   # noqa: BLE001 - the old rows keep answering
                logger.exception("library snapshot rebuild failed (%s)", kind)
                with _snap_lock:
                    if key in _snapshots:
                        _snapshots[key]["building"] = False
        threading.Thread(target=rebuild, daemon=True, name="video-library-snapshot").start()
    return snap["rows"]


def refresh(db, kind: str, ids, *, server_source=None) -> int:
    """sum these titles up again and put them back in the snapshot (after a
    write). a title that is gone leaves it. returns rows refreshed."""
    kind = kind if kind in KINDS else ("shows" if kind == "show" else "movies")
    wanted = sorted({int(i) for i in ids or []})
    if not wanted:
        return 0
    path = str(getattr(db, "database_path", ""))
    done = 0
    with _snap_lock:
        keys = [k for k in _snapshots if k[0] == path and k[1] == kind]
    for key in keys:
        fresh = {r["id"]: r for r in _build_rows(db, kind, key[2] or None, wanted)}
        with _snap_lock:
            snap = _snapshots.get(key)
            if snap is None:
                continue
            kept = [r for r in snap["rows"] if r["id"] not in set(wanted)]
            snap["rows"] = kept + list(fresh.values())
        done += len(fresh)
    forget_facets()
    return done


def _watched_ids(db, kind: str, watcher) -> Optional[set]:
    """a profile's own watched titles; None means "read the shared flag"."""
    if watcher is None:
        return None
    conn = db._get_connection()
    try:
        if kind == "shows":
            sql = ("SELECT DISTINCT we.show_id FROM video_watch_state ws JOIN episodes we "
                   "ON we.id = ws.item_id WHERE ws.profile_id = ? AND ws.kind = 'episode' "
                   "AND ws.play_count > 0")
        else:
            sql = ("SELECT item_id FROM video_watch_state WHERE profile_id = ? "
                   "AND kind = 'movie' AND play_count > 0")
        return {int(r[0]) for r in conn.execute(sql, (int(watcher),))}
    except Exception:   # noqa: BLE001 - no per-profile state: nothing watched
        return set()
    finally:
        conn.close()


def _matches(row: Dict[str, Any], args: Dict[str, Any], watched: Optional[set]) -> bool:
    """does one summed-up row pass the page's filters"""
    vlib = _int(args.get("video_library"))
    if vlib and row.get("video_library_id") != vlib:
        return False
    search = str(args.get("search") or "").strip().lower()
    if search and search not in str(row.get("title") or "").lower():
        return False
    letter = str(args.get("letter") or "").strip().lower()
    if letter and letter != "all":
        first = (row.get("sort_title") or "")[:1]
        if letter == "#":
            if "a" <= first <= "z":
                return False
        elif first != letter[:1]:
            return False
    genre = str(args.get("genre") or "").strip().lower()
    if genre and genre not in [g.lower() for g in row.get("genres") or []]:
        return False
    year_from, year_to = _int(args.get("year_from")), _int(args.get("year_to"))
    year = row.get("year")
    if year_from and not (year and year >= year_from):
        return False
    if year_to and not (year and year <= year_to):
        return False
    profile = str(args.get("quality_profile") or "").strip()
    if profile == "default" and row.get("quality_profile_id"):
        return False
    if _int(profile) and row.get("quality_profile_id") != _int(profile):
        return False
    status = str(args.get("status") or "all")
    if status == "owned" and not row["owned"]:
        return False
    if status == "not_owned" and row["owned"]:
        return False
    monitored = str(args.get("monitored") or "")
    if monitored in ("1", "0") and bool(row["monitored"]) != (monitored == "1"):
        return False
    locked = str(args.get("locked") or "")
    if locked in ("1", "0") and bool(row.get("locked")) != (locked == "1"):
        return False
    res = str(args.get("resolution") or "").strip().lower()
    if res:
        label = RES_LABELS.get(_RES_BY_NAME.get(res, -1))
        if not label or not row["resolution_mix"].get(label):
            return False
    codec = str(args.get("codec") or "").strip().lower()
    if codec and codec not in row["video_codecs"]:
        return False
    hdr = str(args.get("hdr") or "")
    if hdr == "1" and not row["dynamic_range"]:
        return False
    if hdr == "0" and (row["dynamic_range"] or not row["owned"]):
        return False
    audio = str(args.get("audio_language") or "").strip().lower()
    if audio and audio not in row["audio_languages"]:
        return False
    sub = str(args.get("subtitle_language") or "").strip().lower()
    if sub and sub not in row["subtitle_languages"]:
        return False
    drive = str(args.get("drive") or "").strip()
    if drive and row.get("drive") != drive:
        return False
    is_watched = (row["id"] in watched) if watched is not None else row["watched"]
    for need in [n for n in str(args.get("needs") or "").split(",") if n in NEEDS]:
        if need == "missing":
            hit = (row.get("episodes_missing", 0) > 0 if row["kind"] == "show"
                   else (not row["owned"] and row["monitored"]))
        elif need == "not_owned":
            hit = not row["owned"]
        elif need == "below_cutoff":
            hit = row["below_cutoff"]
        elif need == "copies":
            hit = row["extra_copies"] > 0
        elif need == "unmatched":
            hit = not row["matched"]
        elif need == "no_subtitles":
            hit = bool(row["files_without_subtitles"]) and row["unprobed_files"] < row["file_count"]
        elif need == "unprobed":
            hit = row["unprobed_files"] > 0
        elif need == "unmonitored":
            hit = not row["monitored"]
        else:   # unwatched
            hit = row["owned"] and not is_watched
        if not hit:
            return False
    return True


_SORT_KEYS = {
    "title": (lambda r: r["sort_title"], False),
    "year": (lambda r: r["year"], True),
    "added": (lambda r: r["added_at"], True),
    "rating": (lambda r: r["rating"], True),
    "size": (lambda r: r["size_bytes"], True),
    "files": (lambda r: r["file_count"], True),
    "resolution": (lambda r: r["resolution_rank"], True),
    "runtime": (lambda r: r["runtime_minutes"], True),
    "missing": (lambda r: r.get("episodes_missing"), True),
    "episodes": (lambda r: r.get("episodes_owned"), True),
}


def _sorted(rows: List[Dict[str, Any]], args: Dict[str, Any]) -> List[Dict[str, Any]]:
    name = str(args.get("sort") or "title")
    key, desc = _SORT_KEYS.get(name) or _SORT_KEYS["title"]
    asked = str(args.get("dir") or "").lower()
    if asked in ("asc", "desc"):
        desc = asked == "desc"
    rows = sorted(rows, key=lambda r: (r["sort_title"], r["id"]))
    known = [r for r in rows if key(r) is not None]
    unknown = [r for r in rows if key(r) is None]
    known.sort(key=key, reverse=desc)        # stable: ties keep the title order
    return known + unknown                   # a title with no value sorts last either way


def _paging(args: Dict[str, Any], default_limit: int = 100) -> Tuple[int, int]:
    page = max(1, _int(args.get("page"), 1) or 1)
    limit = max(1, min(500, _int(args.get("limit"), default_limit) or default_limit))
    return page, limit


def _public(row: Dict[str, Any], watched: Optional[set]) -> Dict[str, Any]:
    out = {k: v for k, v in row.items() if k != "sort_title"}
    if watched is not None:
        out["watched"] = row["id"] in watched
    return out


def _filtered(db, kind: str, args: Dict[str, Any], server_source, watcher) -> Tuple[List[Dict[str, Any]], Optional[set]]:
    watched = _watched_ids(db, kind, watcher)
    rows = [r for r in _snapshot(db, kind, server_source) if _matches(r, args, watched)]
    return rows, watched


def titles(db, kind: str, args: Optional[Dict[str, Any]] = None, *, server_source=None,
           watcher: Any = None) -> Dict[str, Any]:
    """One page of movies or shows as manager rows, plus what the whole
    filtered set adds up to. ``args`` are the page's query parameters."""
    args = dict(args or {})
    kind = kind if kind in KINDS else "movies"
    rows, watched = _filtered(db, kind, args, server_source, watcher)
    rows = _sorted(rows, args)
    page, limit = _paging(args)
    total = len(rows)
    pages = max(1, (total + limit - 1) // limit)
    return {"items": [_public(r, watched) for r in rows[(page - 1) * limit: page * limit]],
            "kind": kind,
            "totals": {"titles": total, "size_bytes": sum(r["size_bytes"] for r in rows),
                       "files": sum(r["file_count"] for r in rows)},
            "pagination": {"page": page, "limit": limit, "total_pages": pages,
                           "total_count": total, "has_prev": page > 1, "has_next": page < pages}}


def title_ids(db, kind: str, args: Optional[Dict[str, Any]] = None, *, server_source=None,
              watcher: Any = None, cap: int = 5000) -> List[int]:
    """every id the filters match, for "select all N" (capped)."""
    kind = kind if kind in KINDS else "movies"
    rows, _watched = _filtered(db, kind, dict(args or {}), server_source, watcher)
    return [r["id"] for r in _sorted(rows, dict(args or {}))[: int(cap)]]


def _like_prefix(prefix: str) -> str:
    esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc.rstrip("/") + "/%"


def _order(sorts: Dict[str, Tuple[str, str]], args: Dict[str, Any], default: str,
           tiebreak: str) -> str:
    name = str(args.get("sort") or default)
    sql, direction = sorts.get(name) or sorts[default]
    asked = str(args.get("dir") or "").lower()
    direction = asked if asked in ("asc", "desc") else direction
    nulls = f"{sql} IS NULL, " if name not in ("title", "path") else ""
    return f"{nulls}{sql} {direction.upper()}, {tiebreak}"


# ── files ────────────────────────────────────────────────────────────────────

def files(db, args: Optional[Dict[str, Any]] = None, *, server_source=None) -> Dict[str, Any]:
    """One page of video files, each with the title it belongs to.
    ``kind`` = movies | shows narrows it to one side."""
    from core.video.drive_map import drive_root

    args = dict(args or {})
    kind = str(args.get("kind") or "movies")
    is_shows = kind == "shows"
    rr = res_rank_sql("mf.resolution")
    if is_shows:
        src = ("media_files mf JOIN episodes ep ON ep.id = mf.episode_id "
               "JOIN shows t ON t.id = ep.show_id")
        cols = ("t.id AS title_id, t.title AS title, COALESCE(t.sort_title, t.title) AS title_sort, "
                "t.year AS year, ep.id AS episode_id, ep.season_number AS season_number, "
                "ep.episode_number AS episode_number, ep.title AS episode_title, ")
        tiebreak = "ep.season_number, ep.episode_number, mf.id"
    else:
        src = "media_files mf JOIN movies t ON t.id = mf.movie_id"
        cols = ("t.id AS title_id, t.title AS title, COALESCE(t.sort_title, t.title) AS title_sort, "
                "t.year AS year, NULL AS episode_id, NULL AS season_number, "
                "NULL AS episode_number, NULL AS episode_title, ")
        tiebreak = "mf.id"
    where: List[str] = []
    params: List[Any] = []

    def add(sql: str, *values: Any) -> None:
        where.append(sql)
        params.extend(values)

    if server_source:
        add("t.server_source = ?", server_source)
    vlib = _int(args.get("video_library"))
    if vlib:
        add("t.video_library_id = ?", vlib)
    title_id = _int(args.get("title_id"))
    if title_id:
        add("t.id = ?", title_id)
    search = str(args.get("search") or "").strip()
    if search:
        add("(t.title LIKE ? COLLATE NOCASE OR mf.relative_path LIKE ? COLLATE NOCASE)",
            "%" + search + "%", "%" + search + "%")
    res = _RES_BY_NAME.get(str(args.get("resolution") or "").strip().lower())
    if res:
        add(f"{rr} = ?", res)
    codec = str(args.get("codec") or "").strip().lower()
    if codec:
        add("LOWER(COALESCE(mf.video_codec, '')) = ?", codec)
    audio_codec = str(args.get("audio_codec") or "").strip().lower()
    if audio_codec:
        add("LOWER(COALESCE(mf.audio_codec, '')) = ?", audio_codec)
    if str(args.get("hdr") or "") == "1":
        add("COALESCE(mf.dynamic_range, '') <> ''")
    elif str(args.get("hdr") or "") == "0":
        add("COALESCE(mf.dynamic_range, '') = ''")
    audio = str(args.get("audio_language") or "").strip().lower()
    if audio:
        add("(',' || COALESCE(mf.audio_languages, '') || ',') LIKE ?", f"%,{audio},%")
    sub = str(args.get("subtitle_language") or "").strip().lower()
    if sub:
        add("(',' || COALESCE(mf.subtitle_languages, '') || ',') LIKE ?", f"%,{sub},%")
    drive = str(args.get("drive") or "").strip()
    if drive:
        add("mf.relative_path LIKE ? ESCAPE '\\'", _like_prefix(drive))
    source = str(args.get("release_source") or "").strip().lower()
    if source:
        add("LOWER(COALESCE(mf.release_source, '')) = ?", source)
    for need in [n for n in str(args.get("needs") or "").split(",") if n]:
        if need == "no_subtitles":
            add("mf.audio_languages IS NOT NULL AND COALESCE(mf.subtitle_languages, '') = ''")
        elif need == "unprobed":
            add("mf.audio_languages IS NULL")
        elif need == "copies":
            key = "mf.episode_id" if is_shows else "mf.movie_id"
            add(f"(SELECT COUNT(*) FROM media_files o WHERE o.{key[3:]} = {key}) > 1")
        elif need == "below_cutoff":
            default_cut, named_cut = _cutoff_ranks(db, kind if kind in KINDS else "movies")
            cut = _cutoff_sql(default_cut, named_cut)
            add(f"{cut} > 0 AND {rr} > 0 AND {rr} < {cut}")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order = _order(_FILE_SORTS, args, "title", tiebreak)
    page, limit = _paging(args)
    conn = db._get_connection()
    try:
        totals = conn.execute(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(mf.size_bytes), 0) AS size FROM {src}{where_sql}",
            params).fetchone()
        rows = conn.execute(
            f"SELECT mf.id, mf.relative_path, mf.size_bytes, mf.resolution, mf.video_codec, "
            f"mf.audio_codec, mf.release_source, mf.quality, mf.runtime_seconds, mf.audio_channels, "
            f"mf.dynamic_range, mf.atmos, mf.audio_languages, mf.subtitle_languages, mf.added_at, "
            f"{cols}{rr} AS res_rank FROM {src}{where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit, (page - 1) * limit]).fetchall()
    finally:
        conn.close()
    items = []
    for r in rows:
        path = r["relative_path"] or ""
        items.append({
            "id": r["id"], "kind": "show" if is_shows else "movie",
            "title_id": r["title_id"], "title": r["title"], "year": r["year"],
            "episode_id": r["episode_id"], "season_number": r["season_number"],
            "episode_number": r["episode_number"], "episode_title": r["episode_title"],
            "path": path, "filename": path.replace("\\", "/").rsplit("/", 1)[-1],
            "drive": drive_root(path), "size_bytes": int(r["size_bytes"] or 0),
            "resolution": RES_LABELS.get(int(r["res_rank"] or 0), ""),
            "resolution_rank": int(r["res_rank"] or 0),
            "video_codec": (r["video_codec"] or "").lower() or None,
            "audio_codec": (r["audio_codec"] or "").lower() or None,
            "release_source": r["release_source"], "quality": r["quality"],
            "runtime_seconds": r["runtime_seconds"], "audio_channels": r["audio_channels"],
            "dynamic_range": r["dynamic_range"] or None, "atmos": bool(r["atmos"]),
            "audio_languages": _langs(r["audio_languages"]),
            "subtitle_languages": _langs(r["subtitle_languages"]),
            "probed": r["audio_languages"] is not None, "added_at": r["added_at"],
        })
    total = int(totals["n"] or 0)
    pages = max(1, (total + limit - 1) // limit)
    return {"items": items, "kind": kind,
            "totals": {"files": total, "size_bytes": int(totals["size"] or 0)},
            "pagination": {"page": page, "limit": limit, "total_pages": pages,
                           "total_count": total, "has_prev": page > 1, "has_next": page < pages}}


# ── facets ───────────────────────────────────────────────────────────────────

def forget_facets() -> None:
    """kept for callers: the counts are read off the snapshot, which they share"""


def facets(db, kind: str, *, server_source=None, watcher: Any = None) -> Dict[str, Any]:
    """What there is to filter this side of the library by, counted in titles,
    and how many titles each "needs a look" filter holds. Read off the same
    summed-up rows the lists use, so a count is always the number of rows the
    filter would show."""
    kind = kind if kind in KINDS else "movies"
    rows = _snapshot(db, kind, server_source)
    watched = _watched_ids(db, kind, watcher)

    def counted(values_of, limit: int = 40) -> List[Dict[str, Any]]:
        tally: Dict[str, int] = {}
        for row in rows:
            for value in values_of(row):
                if value:
                    tally[value] = tally.get(value, 0) + 1
        return [{"value": k, "count": v}
                for k, v in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]

    drives: Dict[str, Dict[str, int]] = {}
    for row in rows:
        if row.get("drive"):
            d = drives.setdefault(row["drive"], {"count": 0, "size_bytes": 0})
            d["count"] += 1
            d["size_bytes"] += row["size_bytes"]
    order = {label: rank for rank, label in RES_LABELS.items()}
    resolutions = sorted(counted(lambda r: list(r["resolution_mix"])),
                         key=lambda x: -order.get(x["value"], 0))
    return {
        "kind": kind,
        "titles": len(rows),
        "size_bytes": sum(r["size_bytes"] for r in rows),
        "files": sum(r["file_count"] for r in rows),
        "owned": sum(1 for r in rows if r["owned"]),
        "resolutions": resolutions,
        "codecs": counted(lambda r: r["video_codecs"], 12),
        "audio_codecs": counted(lambda r: r["audio_codecs"], 12),
        "release_sources": counted(lambda r: r["release_sources"], 8),
        "hdr": counted(lambda r: r["dynamic_range"]),
        "audio_languages": counted(lambda r: r["audio_languages"]),
        "subtitle_languages": counted(lambda r: r["subtitle_languages"]),
        "genres": counted(lambda r: r["genres"], 60),
        "drives": [{"value": k, **v} for k, v in sorted(drives.items())],
        "needs": {need: sum(1 for r in rows if _matches(r, {"needs": need}, watched))
                  for need in NEEDS},
    }


__all__ = ["KINDS", "NEEDS", "RES_LABELS", "facets", "files", "forget_facets",
           "res_rank_sql", "title_ids", "titles"]
