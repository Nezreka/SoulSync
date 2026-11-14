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

import itertools
import json
import re
import sqlite3
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
# every change to a snapshot's rows takes the next number: what is worked out
# from a snapshot (the filter counts) is kept until its number moves
_version = itertools.count(1)


def forget(db=None) -> None:
    """drop every summed-up row (files moved underneath us and nobody knows
    whose they were)"""
    with _snap_lock:
        if db is None:
            _snapshots.clear()
            _file_snaps.clear()
        else:
            path = str(getattr(db, "database_path", ""))
            for store in (_snapshots, _file_snaps):
                for key in [k for k in store if k[0] == path]:
                    store.pop(key, None)
    forget_facets()


def expire(db=None) -> None:
    """a scan finished: everything summed up is out of date. the old rows
    keep answering while each side is summed again in the background, so
    the page that is open never waits on a cold build."""
    path = str(getattr(db, "database_path", "")) if db is not None else None
    with _snap_lock:
        for store in (_snapshots, _file_snaps):
            for key, snap in store.items():
                if path is None or key[0] == path:
                    snap["at"] = 0.0
    forget_facets()


def warm(db, kind: str, server_source) -> None:
    """sum one side up in the background if it has not been yet. the page
    asks for the side it opens on; the other is ready by the time its tab
    is clicked."""
    kind = kind if kind in KINDS else "movies"
    key = _snap_key(db, kind, server_source)
    with _snap_lock:
        if key in _snapshots or key in _warming:
            return
        _warming.add(key)

    def build():
        try:
            _snapshot(db, kind, server_source)
        except Exception:   # noqa: BLE001 - the tab builds it itself when clicked
            logger.debug("library snapshot warm-up failed (%s)", kind, exc_info=True)
        finally:
            with _snap_lock:
                _warming.discard(key)
    threading.Thread(target=build, daemon=True, name="video-library-warm").start()


_warming: set = set()


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
            _snapshots[key] = {"at": time.monotonic(), "rows": rows, "building": False,
                               "v": next(_version)}
        return rows
    if not fresh and not refreshing:
        def rebuild():
            try:
                rows = _build_rows(db, kind, server_source)
                with _snap_lock:
                    _snapshots[key] = {"at": time.monotonic(), "rows": rows, "building": False,
                                       "v": next(_version)}
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
            gone = set(wanted)
            kept = [r for r in snap["rows"] if r["id"] not in gone]
            snap["rows"] = kept + list(fresh.values())
            snap["v"] = next(_version)
        done += len(fresh)
    _refresh_files(db, kind, wanted)
    forget_facets()
    return done


def _snapshot_version(db, kind: str, server_source) -> int:
    with _snap_lock:
        return int((_snapshots.get(_snap_key(db, kind, server_source)) or {}).get("v") or 0)


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


def _need_hit(row: Dict[str, Any], need: str, watched: Optional[set]) -> bool:
    """is this title one that a "needs a look" filter shows"""
    if need == "missing":
        return (row.get("episodes_missing", 0) > 0 if row["kind"] == "show"
                else (not row["owned"] and row["monitored"]))
    if need == "not_owned":
        return not row["owned"]
    if need == "below_cutoff":
        return bool(row["below_cutoff"])
    if need == "copies":
        return row["extra_copies"] > 0
    if need == "unmatched":
        return not row["matched"]
    if need == "no_subtitles":
        return bool(row["files_without_subtitles"]) and row["unprobed_files"] < row["file_count"]
    if need == "unprobed":
        return row["unprobed_files"] > 0
    if need == "unmonitored":
        return not row["monitored"]
    is_watched = (row["id"] in watched) if watched is not None else row["watched"]
    return bool(row["owned"] and not is_watched)      # unwatched


def _compile(args: Dict[str, Any], watched: Optional[set]):
    """the page's filters as one test of a row. the query is read here, once,
    and only the filters that are set are asked of each row: the same
    parsing per row, over a whole library, was most of a request."""
    tests: List[Any] = []
    vlib = _int(args.get("video_library"))
    if vlib:
        tests.append(lambda r: r.get("video_library_id") == vlib)
    search = str(args.get("search") or "").strip().lower()
    if search:
        tests.append(lambda r: search in str(r.get("title") or "").lower())
    letter = str(args.get("letter") or "").strip().lower()
    if letter and letter != "all":
        if letter == "#":
            tests.append(lambda r: not ("a" <= (r.get("sort_title") or "")[:1] <= "z"))
        else:
            first = letter[:1]
            tests.append(lambda r: (r.get("sort_title") or "")[:1] == first)
    genre = str(args.get("genre") or "").strip().lower()
    if genre:
        tests.append(lambda r: genre in [g.lower() for g in r.get("genres") or []])
    year_from, year_to = _int(args.get("year_from")), _int(args.get("year_to"))
    if year_from:
        tests.append(lambda r: bool(r.get("year") and r["year"] >= year_from))
    if year_to:
        tests.append(lambda r: bool(r.get("year") and r["year"] <= year_to))
    profile = str(args.get("quality_profile") or "").strip()
    if profile == "default":
        tests.append(lambda r: not r.get("quality_profile_id"))
    profile_id = _int(profile)
    if profile_id:
        tests.append(lambda r: r.get("quality_profile_id") == profile_id)
    status = str(args.get("status") or "all")
    if status == "owned":
        tests.append(lambda r: bool(r["owned"]))
    elif status == "not_owned":
        tests.append(lambda r: not r["owned"])
    monitored = str(args.get("monitored") or "")
    if monitored in ("1", "0"):
        want_monitored = monitored == "1"
        tests.append(lambda r: bool(r["monitored"]) == want_monitored)
    locked = str(args.get("locked") or "")
    if locked in ("1", "0"):
        want_locked = locked == "1"
        tests.append(lambda r: bool(r.get("locked")) == want_locked)
    res = str(args.get("resolution") or "").strip().lower()
    if res:
        label = RES_LABELS.get(_RES_BY_NAME.get(res, -1))
        if not label:
            return lambda r: False
        tests.append(lambda r: bool(r["resolution_mix"].get(label)))
    codec = str(args.get("codec") or "").strip().lower()
    if codec:
        tests.append(lambda r: codec in r["video_codecs"])
    hdr = str(args.get("hdr") or "")
    if hdr == "1":
        tests.append(lambda r: bool(r["dynamic_range"]))
    elif hdr == "0":
        tests.append(lambda r: not r["dynamic_range"] and bool(r["owned"]))
    audio = str(args.get("audio_language") or "").strip().lower()
    if audio:
        tests.append(lambda r: audio in r["audio_languages"])
    sub = str(args.get("subtitle_language") or "").strip().lower()
    if sub:
        tests.append(lambda r: sub in r["subtitle_languages"])
    drive = str(args.get("drive") or "").strip()
    if drive:
        tests.append(lambda r: r.get("drive") == drive)
    for need in [n for n in str(args.get("needs") or "").split(",") if n in NEEDS]:
        tests.append(lambda r, need=need: _need_hit(r, need, watched))
    if not tests:
        return lambda r: True
    if len(tests) == 1:
        return tests[0]
    return lambda r: all(test(r) for test in tests)


def _matches(row: Dict[str, Any], args: Dict[str, Any], watched: Optional[set]) -> bool:
    """does one summed-up row pass the page's filters"""
    return bool(_compile(args, watched)(row))


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
    passes = _compile(args, watched)
    rows = [r for r in _snapshot(db, kind, server_source) if passes(r)]
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


# ── files ────────────────────────────────────────────────────────────────────
# the Files view is every file of one side as a row. asking the database for a
# page of them joined three tables over a hundred thousand files and sorted
# the lot, seconds per request, for every page turn and every filter. the
# files are read once into memory instead (plain tuples: a dict per file was
# several times the room), kept the way the title rows are, and every filter,
# sort and page is worked out over that. a write refreshes just its title's
# files, and a side nobody has looked at for a while is let go.

_file_snaps: Dict[tuple, Dict[str, Any]] = {}
_FILE_IDLE = 900.0                # a side's files are dropped after this long unread

# what each place in a file's tuple holds
(F_ID, F_PATH, F_SIZE, F_RANK, F_VCODEC, F_ACODEC, F_SOURCE, F_QUALITY, F_RUNTIME, F_CHANNELS,
 F_HDR, F_ATMOS, F_ALANGS, F_SLANGS, F_ADDED, F_TITLE_ID, F_TITLE, F_SORT, F_YEAR, F_EP_ID,
 F_SEASON, F_EPISODE, F_EP_TITLE, F_VLIB, F_PROFILE) = range(25)


def _res_rank(value: Any) -> int:
    """res_rank_sql, for a value already read"""
    c = str(value or "").lower()
    if c in ("4k", "2160", "2160p", "2k", "1440", "1440p", "uhd"):
        return 4
    if c.startswith("1080"):
        return 3
    if c.startswith("720"):
        return 2
    return 0 if c == "" else 1


def _file_rows(db, kind: str, server_source, title_ids: Optional[List[int]] = None) -> List[tuple]:
    """one side's files (or just these titles'), in the order the Files view
    opens in: by title, then season and episode"""
    is_shows = kind == "shows"
    if is_shows:
        # the episode's show, season and number come off idx_episodes_summary
        # alone. the episode rows carry each overview, and reading one per
        # file to learn its number was most of this query; the title, which
        # the index does not hold, is read for the page that is shown
        src = ("episodes ep INDEXED BY idx_episodes_summary "
               "JOIN media_files mf ON mf.episode_id = ep.id "
               "JOIN shows t ON t.id = ep.show_id")
        extra = "ep.id, ep.season_number, ep.episode_number, NULL"
    else:
        src = "media_files mf JOIN movies t ON t.id = mf.movie_id"
        extra = "NULL, NULL, NULL, NULL"
    where, params = [], []
    if server_source:
        where.append("t.server_source = ?")
        params.append(server_source)
    if title_ids is not None:
        where.append("t.id IN (%s)" % ",".join("?" * len(title_ids)))
        params.extend(title_ids)
    sql = ("SELECT mf.id, mf.relative_path, mf.size_bytes, mf.resolution, mf.video_codec, "
           "mf.audio_codec, mf.release_source, mf.quality, mf.runtime_seconds, mf.audio_channels, "
           "mf.dynamic_range, mf.atmos, mf.audio_languages, mf.subtitle_languages, mf.added_at, "
           "t.id, t.title, COALESCE(t.sort_title, t.title), t.year, " + extra + ", "
           "t.video_library_id, t.quality_profile_id FROM " + src
           + ((" WHERE " + " AND ".join(where)) if where else ""))
    conn = db._get_connection()
    try:
        try:
            raw = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # a database opened before the index was made: the plain join
            raw = conn.execute(sql.replace(" INDEXED BY idx_episodes_summary", ""),
                               params).fetchall()
    finally:
        conn.close()
    sorts: Dict[int, str] = {}              # one string per title, shared by its files
    out = []
    for r in raw:
        title_id = r[15]
        sort = sorts.get(title_id)
        if sort is None:
            sort = sorts[title_id] = str(r[17] or "").lower()
        out.append((
            r[0], r[1] or "", int(r[2] or 0) if r[2] is not None else None, _res_rank(r[3]),
            (r[4] or "").lower() or None, (r[5] or "").lower() or None, r[6], r[7], r[8], r[9],
            r[10] or None, bool(r[11]), r[12], r[13], r[14], title_id, r[16], sort, r[18],
            r[19], r[20], r[21], r[22], r[23], r[24]))
    out.sort(key=_file_base_key)
    return out


def _file_base_key(f: tuple) -> tuple:
    return (f[F_SORT], f[F_TITLE_ID], f[F_SEASON] or 0, f[F_EPISODE] or 0, f[F_ID])


def _file_snapshot(db, kind: str, server_source) -> List[tuple]:
    key = _snap_key(db, kind, server_source)
    now = time.monotonic()
    with _snap_lock:
        # a side nobody has opened the Files view of in a while gives its room back
        for other in [k for k, v in _file_snaps.items()
                      if k != key and now - v.get("used", now) > _FILE_IDLE and not v.get("building")]:
            _file_snaps.pop(other, None)
        snap = _file_snaps.get(key)
        fresh = snap is not None and now - snap["at"] < _SNAP_TTL
        refreshing = bool(snap and snap.get("building"))
        if snap is not None:
            snap["used"] = now
            if not fresh and not refreshing:
                snap["building"] = True
    if snap is None:
        rows = _file_rows(db, kind, server_source)
        with _snap_lock:
            _file_snaps[key] = {"at": time.monotonic(), "rows": rows, "building": False,
                                "used": time.monotonic()}
        return rows
    if not fresh and not refreshing:
        def rebuild():
            try:
                rows = _file_rows(db, kind, server_source)
                with _snap_lock:
                    if key in _file_snaps:
                        _file_snaps[key].update(at=time.monotonic(), rows=rows, building=False)
            except Exception:   # noqa: BLE001 - the old rows keep answering
                logger.exception("library file snapshot rebuild failed (%s)", kind)
                with _snap_lock:
                    if key in _file_snaps:
                        _file_snaps[key]["building"] = False
        threading.Thread(target=rebuild, daemon=True, name="video-library-files").start()
    return snap["rows"]


def _refresh_files(db, kind: str, title_ids: List[int]) -> None:
    """read these titles' files again into the file snapshots that hold them"""
    path = str(getattr(db, "database_path", ""))
    with _snap_lock:
        keys = [k for k in _file_snaps if k[0] == path and k[1] == kind]
    gone = set(title_ids)
    for key in keys:
        try:
            fresh = _file_rows(db, kind, key[2] or None, title_ids)
        except Exception:   # noqa: BLE001 - the next rebuild catches up
            logger.debug("file snapshot refresh failed", exc_info=True)
            continue
        with _snap_lock:
            snap = _file_snaps.get(key)
            if snap is None:
                continue
            rows = [f for f in snap["rows"] if f[F_TITLE_ID] not in gone] + fresh
            rows.sort(key=_file_base_key)
            snap["rows"] = rows


#: sort name -> (the value sorted on, the direction it opens in, can be unknown)
_FILE_SORT_KEYS = {
    "title": (None, "asc", False),          # the order the rows are kept in
    "size": (lambda f: f[F_SIZE], "desc", True),
    "resolution": (lambda f: f[F_RANK], "desc", False),
    "codec": (lambda f: f[F_VCODEC] or "", "asc", False),
    "added": (lambda f: f[F_ADDED], "desc", True),
    "path": (lambda f: f[F_PATH].lower(), "asc", False),
    "runtime": (lambda f: f[F_RUNTIME], "desc", True),
}


def _file_tests(db, kind: str, args: Dict[str, Any], rows: List[tuple]) -> List[Any]:
    tests: List[Any] = []
    vlib = _int(args.get("video_library"))
    if vlib:
        tests.append(lambda f: f[F_VLIB] == vlib)
    title_id = _int(args.get("title_id"))
    if title_id:
        tests.append(lambda f: f[F_TITLE_ID] == title_id)
    search = str(args.get("search") or "").strip().lower()
    if search:
        tests.append(lambda f: search in str(f[F_TITLE] or "").lower() or search in f[F_PATH].lower())
    res = _RES_BY_NAME.get(str(args.get("resolution") or "").strip().lower())
    if res:
        tests.append(lambda f: f[F_RANK] == res)
    codec = str(args.get("codec") or "").strip().lower()
    if codec:
        tests.append(lambda f: (f[F_VCODEC] or "") == codec)
    audio_codec = str(args.get("audio_codec") or "").strip().lower()
    if audio_codec:
        tests.append(lambda f: (f[F_ACODEC] or "") == audio_codec)
    hdr = str(args.get("hdr") or "")
    if hdr == "1":
        tests.append(lambda f: bool(f[F_HDR]))
    elif hdr == "0":
        tests.append(lambda f: not f[F_HDR])
    audio = str(args.get("audio_language") or "").strip().lower()
    if audio:
        tests.append(lambda f: ("," + audio + ",") in ("," + str(f[F_ALANGS] or "").lower() + ","))
    sub = str(args.get("subtitle_language") or "").strip().lower()
    if sub:
        tests.append(lambda f: ("," + sub + ",") in ("," + str(f[F_SLANGS] or "").lower() + ","))
    drive = str(args.get("drive") or "").strip()
    if drive:
        prefix = drive.rstrip("/").lower() + "/"
        tests.append(lambda f: f[F_PATH].lower().startswith(prefix))
    source = str(args.get("release_source") or "").strip().lower()
    if source:
        tests.append(lambda f: str(f[F_SOURCE] or "").lower() == source)
    for need in [n for n in str(args.get("needs") or "").split(",") if n]:
        if need == "no_subtitles":
            tests.append(lambda f: f[F_ALANGS] is not None and not (f[F_SLANGS] or ""))
        elif need == "unprobed":
            tests.append(lambda f: f[F_ALANGS] is None)
        elif need == "copies":
            owner = F_EP_ID if kind == "shows" else F_TITLE_ID
            held: Dict[Any, int] = {}
            for f in rows:
                held[f[owner]] = held.get(f[owner], 0) + 1
            tests.append(lambda f, owner=owner, held=held: held.get(f[owner], 0) > 1)
        elif need == "below_cutoff":
            default_cut, named_cut = _cutoff_ranks(db, kind if kind in KINDS else "movies")

            def below(f, default_cut=default_cut, named_cut=named_cut):
                cut = named_cut.get(f[F_PROFILE], default_cut) if f[F_PROFILE] else default_cut
                return cut > 0 and 0 < f[F_RANK] < cut
            tests.append(below)
    return tests


def _episode_titles(db, episode_ids: List[int]) -> Dict[int, Any]:
    """the titles of the episodes on the page being shown"""
    ids = sorted({int(i) for i in episode_ids if i is not None})
    if not ids:
        return {}
    conn = db._get_connection()
    try:
        return {int(r[0]): r[1] for r in conn.execute(
            "SELECT id, title FROM episodes WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _file_public(f: tuple, kind: str, drive_of, episode_titles: Dict[int, Any]) -> Dict[str, Any]:
    path = f[F_PATH]
    return {
        "id": f[F_ID], "kind": "show" if kind == "shows" else "movie",
        "title_id": f[F_TITLE_ID], "title": f[F_TITLE], "year": f[F_YEAR],
        "episode_id": f[F_EP_ID], "season_number": f[F_SEASON],
        "episode_number": f[F_EPISODE],
        "episode_title": episode_titles.get(f[F_EP_ID], f[F_EP_TITLE]),
        "path": path, "filename": path.replace("\\", "/").rsplit("/", 1)[-1],
        "drive": drive_of(path), "size_bytes": int(f[F_SIZE] or 0),
        "resolution": RES_LABELS.get(f[F_RANK], ""), "resolution_rank": f[F_RANK],
        "video_codec": f[F_VCODEC], "audio_codec": f[F_ACODEC],
        "release_source": f[F_SOURCE], "quality": f[F_QUALITY],
        "runtime_seconds": f[F_RUNTIME], "audio_channels": f[F_CHANNELS],
        "dynamic_range": f[F_HDR], "atmos": f[F_ATMOS],
        "audio_languages": _langs(f[F_ALANGS]), "subtitle_languages": _langs(f[F_SLANGS]),
        "probed": f[F_ALANGS] is not None, "added_at": f[F_ADDED],
    }


def files(db, args: Optional[Dict[str, Any]] = None, *, server_source=None) -> Dict[str, Any]:
    """One page of video files, each with the title it belongs to.
    ``kind`` = movies | shows narrows it to one side."""
    from core.video.drive_map import drive_root

    args = dict(args or {})
    kind = str(args.get("kind") or "movies")
    kind = kind if kind in KINDS else "movies"
    rows = _file_snapshot(db, kind, server_source)
    tests = _file_tests(db, kind, args, rows)
    if tests:
        rows = [f for f in rows if all(test(f) for test in tests)]
    name = str(args.get("sort") or "title")
    key, direction, nullable = _FILE_SORT_KEYS.get(name) or _FILE_SORT_KEYS["title"]
    asked = str(args.get("dir") or "").lower()
    desc = (asked if asked in ("asc", "desc") else direction) == "desc"
    if key is None:
        rows = rows[::-1] if desc else rows
    else:
        # stable: files that tie keep the title order they are held in
        known = [f for f in rows if key(f) is not None] if nullable else list(rows)
        unknown = [f for f in rows if key(f) is None] if nullable else []
        known.sort(key=key, reverse=desc)
        rows = known + unknown                 # a file with no value sorts last either way
    page, limit = _paging(args)
    total = len(rows)
    pages = max(1, (total + limit - 1) // limit)
    shown = rows[(page - 1) * limit: page * limit]
    episode_titles = _episode_titles(db, [f[F_EP_ID] for f in shown]) if kind == "shows" else {}
    return {"items": [_file_public(f, kind, drive_root, episode_titles) for f in shown],
            "kind": kind,
            "totals": {"files": total, "size_bytes": sum(f[F_SIZE] or 0 for f in rows)},
            "pagination": {"page": page, "limit": limit, "total_pages": pages,
                           "total_count": total, "has_prev": page > 1, "has_next": page < pages}}


# ── facets ───────────────────────────────────────────────────────────────────

# the counts are worked out from a snapshot in one pass and kept until that
# snapshot's rows change: they were worked out again for every visit, walking
# the whole library once per "needs a look" filter
_facet_cache: Dict[tuple, tuple] = {}


def forget_facets() -> None:
    """the counts follow the snapshot's version, so there is nothing to drop;
    kept for callers"""


def facets(db, kind: str, *, server_source=None, watcher: Any = None) -> Dict[str, Any]:
    """What there is to filter this side of the library by, counted in titles,
    and how many titles each "needs a look" filter holds. Read off the same
    summed-up rows the lists use, so a count is always the number of rows the
    filter would show."""
    kind = kind if kind in KINDS else "movies"
    rows = _snapshot(db, kind, server_source)
    version = _snapshot_version(db, kind, server_source)
    watched = _watched_ids(db, kind, watcher)
    # a profile's own watched titles change the unwatched count, and nothing else
    cache_key = (_snap_key(db, kind, server_source), None if watched is None else len(watched),
                 None if watched is None else hash(frozenset(watched)))
    hit = _facet_cache.get(cache_key)
    if hit and hit[0] == version and version:
        return dict(hit[1])

    tallies: Dict[str, Dict[str, int]] = {name: {} for name in (
        "resolutions", "codecs", "audio_codecs", "release_sources", "hdr", "audio_languages",
        "subtitle_languages", "genres")}
    sources = (("resolutions", lambda r: r["resolution_mix"]), ("codecs", lambda r: r["video_codecs"]),
               ("audio_codecs", lambda r: r["audio_codecs"]),
               ("release_sources", lambda r: r["release_sources"]),
               ("hdr", lambda r: r["dynamic_range"]),
               ("audio_languages", lambda r: r["audio_languages"]),
               ("subtitle_languages", lambda r: r["subtitle_languages"]),
               ("genres", lambda r: r["genres"]))
    drives: Dict[str, Dict[str, int]] = {}
    needs = dict.fromkeys(NEEDS, 0)
    size = files_n = owned = 0
    for row in rows:                                   # the whole library, once
        size += row["size_bytes"]
        files_n += row["file_count"]
        owned += 1 if row["owned"] else 0
        for name, values_of in sources:
            tally = tallies[name]
            for value in values_of(row):
                if value:
                    tally[value] = tally.get(value, 0) + 1
        if row.get("drive"):
            d = drives.setdefault(row["drive"], {"count": 0, "size_bytes": 0})
            d["count"] += 1
            d["size_bytes"] += row["size_bytes"]
        for need in NEEDS:
            if _need_hit(row, need, watched):
                needs[need] += 1

    def counted(name: str, limit: int = 40) -> List[Dict[str, Any]]:
        return [{"value": k, "count": v}
                for k, v in sorted(tallies[name].items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]

    order = {label: rank for rank, label in RES_LABELS.items()}
    out = {
        "kind": kind,
        "titles": len(rows),
        "size_bytes": size,
        "files": files_n,
        "owned": owned,
        "resolutions": sorted(counted("resolutions"), key=lambda x: -order.get(x["value"], 0)),
        "codecs": counted("codecs", 12),
        "audio_codecs": counted("audio_codecs", 12),
        "release_sources": counted("release_sources", 8),
        "hdr": counted("hdr"),
        "audio_languages": counted("audio_languages"),
        "subtitle_languages": counted("subtitle_languages"),
        "genres": counted("genres", 60),
        "drives": [{"value": k, **v} for k, v in sorted(drives.items())],
        "needs": needs,
    }
    if version:
        if len(_facet_cache) > 32:
            _facet_cache.clear()
        _facet_cache[cache_key] = (version, out)
    return dict(out)


__all__ = ["KINDS", "NEEDS", "RES_LABELS", "expire", "facets", "files", "forget",
           "forget_facets", "refresh", "res_rank_sql", "title_ids", "titles", "warm"]
