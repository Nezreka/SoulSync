"""The video import folder (#2129).

Drop movie or episode files (or whole folders) into one folder. SoulSync reads
each entry, works out what it is (movie, or show + season/episode) from the
names with the same release parser the downloads use, finds the title on TMDB,
and shows what it found for review. On approval, or on its own when the match
is sure and automatic import is on, the entry goes through the SAME importer a
finished download uses: a ``video_downloads`` row (source ``import``) is handed
to the monitor's organizer (one file) or season-pack importer (a folder of
episodes), so naming, upgrade rules, sidecars, subtitles, wishlist and history
behave exactly as they do for a download. There is no second ingest pipeline.

What is here:
  * ``scan`` lists the folder's top level entries and the videos in each.
  * ``guess`` reads an entry's names (pure).
  * ``match`` picks the TMDB title for a guess and says how sure it is (pure,
    the search is injected).
  * ``import_entry`` runs one entry through the importer.
  * a small worker imports sure matches on its own when the setting is on, like
    the music side's auto-import.

Isolated: sibling video modules + stdlib; nothing from the music side.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.import_folder")

SETTING_PATH = "import_folder_path"
SETTING_AUTO = "import_folder_auto"
SETTING_TRIED = "import_folder_tried"

SOURCE = "import"

# a file still being copied in keeps getting newer. an entry is only imported once
# nothing in it has changed for this long.
SETTLE_SECONDS = 60
AUTO_INTERVAL = 60

# names a copy in progress leaves behind
_PARTIAL = re.compile(r"(\.part|\.partial|\.!qb|\.crdownload|\.tmp|\.soulsync-part|\.tmp\.[0-9a-f]{8})$", re.I)
# a folder named only for its season says nothing about the show
_SEASON_ONLY = re.compile(r"^(season|series|staffel|saison|s)\s*\d+$", re.I)


def _is_video(name: str) -> bool:
    from core.video.importer import VIDEO_EXTS
    return os.path.splitext(str(name))[1].lower() in VIDEO_EXTS


def _skip_name(name: str) -> bool:
    return not name or name.startswith(".") or bool(_PARTIAL.search(name))


# ── settings ─────────────────────────────────────────────────────────────────

def load_settings(db) -> Dict[str, Any]:
    path = str(db.get_setting(SETTING_PATH) or "").strip()
    auto = str(db.get_setting(SETTING_AUTO) or "").strip().lower() in ("1", "true", "yes", "on")
    return {"path": path, "auto": auto}


def save_settings(db, body: Dict[str, Any]) -> Dict[str, Any]:
    if "path" in body:
        path = str(body.get("path") or "").strip()
        db.set_setting(SETTING_PATH, path)
        if path and not os.path.exists(path):
            try:
                os.makedirs(path, exist_ok=True)     # the settings check says it's made on first use
            except OSError:
                logger.info("import folder: couldn't create %s", path)
    if "auto" in body:
        db.set_setting(SETTING_AUTO, "1" if body.get("auto") in (True, 1, "1", "true", "on") else "0")
    return load_settings(db)


# ── reading the folder ───────────────────────────────────────────────────────

def _walk_files(path: str):
    for dirpath, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            yield os.path.join(dirpath, f)


def _stat(path: str):
    try:
        st = os.stat(path)
        return int(st.st_size), float(st.st_mtime)
    except OSError:
        return None, None


def scan(root: str, *, lister: Callable = os.listdir, isdir: Callable = os.path.isdir,
         walker: Callable = _walk_files, stat: Callable = _stat,
         now: Optional[float] = None) -> List[Dict[str, Any]]:
    """One entry per top level file or folder in ``root`` that holds a video.

    Each is ``{key, name, path, is_dir, files: [{path, size_bytes}], size,
    newest, settled, partial}``. ``partial`` is set when a half-copied file sits
    in it, ``settled`` when nothing in it changed for SETTLE_SECONDS. Raises
    OSError when ``root`` can't be read, so the page can say so."""
    now = time.time() if now is None else now
    out = []
    for name in sorted(lister(root), key=lambda n: str(n).lower()):
        name = str(name)
        if name.startswith("."):
            continue
        path = os.path.join(root, name)
        is_dir = bool(isdir(path))
        paths = list(walker(path)) if is_dir else [path]
        files, newest, partial = [], 0.0, False
        for p in paths:
            base = os.path.basename(p)
            if _PARTIAL.search(base):
                partial = True
                continue
            if base.startswith(".") or not _is_video(base):
                continue
            size, mtime = stat(p)
            if size is None:
                continue
            files.append({"path": p, "size_bytes": size})
            newest = max(newest, mtime or 0.0)
        if not files:
            continue
        files.sort(key=lambda f: f["path"].lower())
        out.append({
            "key": name, "name": name, "path": path, "is_dir": is_dir,
            "files": files, "size": sum(int(f["size_bytes"] or 0) for f in files),
            "newest": newest, "partial": partial,
            "settled": (not partial) and (now - newest) >= SETTLE_SECONDS,
        })
    return out


def signature(entry: Dict[str, Any]) -> str:
    """Changes whenever a file in the entry is added, removed or rewritten."""
    h = hashlib.sha1()
    for f in entry.get("files") or []:
        h.update(("%s|%s\n" % (f.get("path"), f.get("size_bytes"))).encode("utf-8", "replace"))
    h.update(str(int(entry.get("newest") or 0)).encode())
    return h.hexdigest()[:16]


# ── working out what an entry is ─────────────────────────────────────────────

def _title_of(name: str) -> str:
    from core.video.release_parse import search_title
    t = (search_title(name) or "").strip()
    return "" if (not t or _SEASON_ONLY.match(t)) else t


def _year_of(name: str) -> Optional[int]:
    from core.video.release_parse import title_region_year
    return title_region_year(name)


def _main_file(files: List[Dict[str, Any]]) -> Optional[str]:
    """The feature: the largest video that isn't a sample or an extra."""
    from core.video.season_pack import _is_junk
    best, best_size = None, -1
    for f in files:
        p = f["path"]
        if _is_junk(os.path.basename(p)):
            continue
        size = int(f.get("size_bytes") or 0)
        if size > best_size:
            best, best_size = p, size
    return best or (files[0]["path"] if files else None)


def guess(entry: Dict[str, Any]) -> Dict[str, Any]:
    """What an entry looks like from its names. Pure.

    ``{kind: movie|show, scope: movie|episode|series, title, year, season,
    episode, episodes: [[s, e], ...], main: path}``. A folder whose files carry
    episode numbers is a show (``series``: the pack importer maps every file);
    a loose file with SxxExx is one episode; anything else is a movie."""
    from core.video.release_parse import parse_release
    from core.video.season_pack import episode_keys_for, map_pack

    name = entry.get("name") or ""
    files = entry.get("files") or []
    is_dir = bool(entry.get("is_dir"))
    stem = name if is_dir else os.path.splitext(name)[0]
    parsed = parse_release(stem)
    main = _main_file(files)
    main_stem = os.path.splitext(os.path.basename(main or ""))[0]

    if is_dir:
        claimed = map_pack(files, root=entry.get("path"))["claimed"]
        episodes = sorted(claimed.keys())
    else:
        episodes = episode_keys_for(name)
    is_show = bool(episodes) or parsed.get("season") is not None

    title = _title_of(stem) or _title_of(main_stem)
    year = _year_of(stem) if _title_of(stem) else _year_of(main_stem)
    out = {"kind": "show" if is_show else "movie", "title": title, "year": year,
           "season": None, "episode": None,
           "episodes": [list(k) for k in episodes], "main": main}
    if not is_show:
        out["scope"] = "movie"
    elif is_dir:
        out["scope"] = "series"
        seasons = sorted({k[0] for k in episodes})
        out["season"] = seasons[0] if len(seasons) == 1 else None
    else:
        out["scope"] = "episode"
        if episodes:
            out["season"], out["episode"] = episodes[0]
        else:
            out["season"] = parsed.get("season")
    return out


def match(g: Dict[str, Any], search: Callable[[str, str], list]) -> Dict[str, Any]:
    """The TMDB title for a guess. ``search(kind, query)`` returns TMDB results
    ``[{id, title, year, poster_url}]``.

    ``confidence`` is ``high`` when the result's title is the guessed title and
    the year agrees (one either way), or there was no year and only one result
    carries that exact title; ``low`` when there is a best guess the user should
    check; ``none`` when nothing came back. Only ``high`` imports on its own."""
    from core.video.release_parse import normalize_title
    title, kind, year = g.get("title") or "", g.get("kind") or "movie", g.get("year")
    empty = {"tmdb_id": None, "title": None, "year": None, "poster_url": None,
             "confidence": "none", "why": "", "alternatives": []}
    if not title:
        return {**empty, "why": "Couldn't read a title from the name"}
    try:
        results = [r for r in (search(kind, title) or []) if r.get("id") is not None]
    except Exception:   # noqa: BLE001 - a failed search is "no match", the user can pick
        logger.debug("import folder: search failed for %r", title, exc_info=True)
        return {**empty, "why": "TMDB search failed, pick the title by hand"}
    if not results:
        return {**empty, "why": "Nothing on TMDB called “%s”" % title}
    want = normalize_title(title)
    same = [r for r in results if normalize_title(r.get("title")) == want]

    def near(r):
        try:
            return year is not None and r.get("year") is not None and abs(int(r["year"]) - int(year)) <= 1
        except (TypeError, ValueError):
            return False

    pick, confidence, why = None, "low", ""
    if year is not None:
        pick = next((r for r in same if near(r)), None)
        if pick:
            confidence, why = "high", "Title and year match"
    elif len(same) == 1:
        pick, confidence, why = same[0], "high", "Only title with this name"
    if pick is None:
        pick = (same or results)[0]
        if not same:
            why = "No exact title match, best guess"
        elif year is not None:
            why = "Title matches but the year doesn't"
        else:
            why = "%d titles share this name, check it's the right one" % len(same)
    alts = [_slim(r) for r in results[:6] if r.get("id") != pick.get("id")][:5]
    return {**_slim(pick), "confidence": confidence, "why": why, "alternatives": alts}


def _slim(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"tmdb_id": r.get("id"), "title": r.get("title"), "year": r.get("year"),
            "poster_url": r.get("poster_url")}


def _default_search(kind: str, query: str) -> list:
    from core.video.enrichment.engine import get_video_enrichment_engine
    w = get_video_enrichment_engine().workers.get("tmdb")
    client = getattr(w, "client", None)
    if not client or not hasattr(client, "search_candidates"):
        return []
    return client.search_candidates("movie" if kind == "movie" else "show", query, limit=8)


# the TMDB answer is kept per (kind, title, year): the page polls every few
# seconds and TMDB is rate limited. the guess itself is cheap and always fresh,
# so a file that is still growing never pins a stale main file.
_match_cache: Dict[tuple, Dict[str, Any]] = {}
_ident_lock = threading.Lock()


def identify(entry: Dict[str, Any], search: Optional[Callable] = None) -> Dict[str, Any]:
    g = guess(entry)
    key = (g.get("kind"), g.get("title"), g.get("year"))
    with _ident_lock:
        m = _match_cache.get(key)
    if m is None:
        m = match(g, search or _default_search)
        if not m.get("why", "").startswith("TMDB search failed"):   # try a failure again
            with _ident_lock:
                if len(_match_cache) > 500:
                    _match_cache.clear()
                _match_cache[key] = m
    return {"guess": g, "match": m}


# ── what was already tried ───────────────────────────────────────────────────
# an entry that didn't fully import stays in the folder. it is remembered by its
# signature so the automatic pass doesn't retry it every minute; changing the
# files (or Import again on the page) clears it.

def _tried(db) -> Dict[str, Any]:
    try:
        d = json.loads(db.get_setting(SETTING_TRIED) or "{}")
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def _remember(db, entry: Dict[str, Any], error: str) -> None:
    d = _tried(db)
    d[entry["path"]] = {"sig": signature(entry), "error": error or "", "at": int(time.time())}
    db.set_setting(SETTING_TRIED, json.dumps(d))


def _forget(db, path: str) -> None:
    d = _tried(db)
    if d.pop(path, None) is not None:
        db.set_setting(SETTING_TRIED, json.dumps(d))


# ── importing ────────────────────────────────────────────────────────────────

_inflight: set = set()          # entry paths being imported right now
_inflight_rows: set = set()     # their download row ids
_inflight_lock = threading.Lock()


def busy(path: str) -> bool:
    with _inflight_lock:
        return path in _inflight


def _default_target_for(db, kind, body) -> str:
    from api.video.downloads import _target_for
    return _target_for(db, kind, body)


def _identity(g: Dict[str, Any], m: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The identity an entry is imported as: what matched, or what the user picked."""
    ident = {"kind": g.get("kind"), "scope": g.get("scope"), "tmdb_id": m.get("tmdb_id"),
             "title": m.get("title"), "year": m.get("year"), "poster_url": m.get("poster_url"),
             "season": g.get("season"), "episode": g.get("episode")}
    o = override or {}
    if o.get("tmdb_id") not in (None, ""):
        kind = "show" if str(o.get("kind") or ident["kind"]) in ("show", "episode", "tv") else "movie"
        if kind != ident["kind"]:
            # the user says it's the other kind: a folder of episodes is the
            # series, a loose file is one episode
            ident["scope"] = "movie" if kind == "movie" else ("series" if g.get("episodes") and g.get("scope") == "series" else "episode")
        ident.update({"kind": kind, "tmdb_id": o.get("tmdb_id"), "title": o.get("title") or ident["title"],
                      "year": o.get("year") if o.get("year") not in ("", None) else None,
                      "poster_url": o.get("poster_url")})
        if ident["scope"] == "episode":
            for k in ("season", "episode"):
                if o.get(k) not in (None, ""):
                    ident[k] = o.get(k)
    for k in ("season", "episode", "year"):
        try:
            ident[k] = int(ident[k]) if ident[k] not in (None, "") else None
        except (TypeError, ValueError):
            ident[k] = None
    return ident


def check_identity(entry: Dict[str, Any], ident: Dict[str, Any]) -> Optional[str]:
    """Why this identity can't be imported, or None."""
    if not ident.get("tmdb_id"):
        return "Pick the title first"
    if ident.get("scope") == "episode" and (ident.get("season") is None or ident.get("episode") is None):
        return "Pick the season and episode"
    if ident.get("scope") == "series" and not entry.get("is_dir"):
        return "A single file can't be a whole series"
    return None


def import_entry(db, entry: Dict[str, Any], ident: Dict[str, Any], *,
                 organize: Optional[Callable] = None, import_pack: Optional[Callable] = None,
                 target_for: Optional[Callable] = None) -> Dict[str, Any]:
    """Run one entry through the download importer. Returns
    ``{status, error, dest_path, imported, failed, id}``.

    A loose file or a movie folder goes through the organizer like a finished
    single download; a show folder goes through the season-pack importer, which
    files each episode on its own row. A refusal (not an upgrade, a sample) is
    an ordinary ``import_failed`` row with the file left where it is, so it
    shows under Needs a hand and can be placed by hand from there."""
    from core.video import download_monitor as mon

    why = check_identity(entry, ident)
    if why:
        return {"status": "refused", "error": why}
    path = entry["path"]
    with _inflight_lock:
        if path in _inflight:
            return {"status": "refused", "error": "Already importing"}
        _inflight.add(path)
    dl_id = None
    try:
        kind = "movie" if ident["kind"] == "movie" else "show"
        scope = ident["scope"]
        ctx = {"scope": scope, "title": ident.get("title"), "year": ident.get("year"),
               "via": "import_folder"}
        if scope == "episode":
            ctx.update({"season": ident["season"], "episode": ident["episode"]})
        src = (entry.get("main") or _main_file(entry.get("files") or [])) if scope != "series" else None
        target = (target_for or _default_target_for)(
            db, kind, {"media_id": ident["tmdb_id"], "media_source": "tmdb", "title": ident.get("title")})
        row = {
            "kind": kind, "title": ident.get("title"), "year": ident.get("year"),
            # a folder's name carries the release detail its files often drop
            "source": SOURCE, "release_title": entry["name"] if scope == "series"
            else ("%s/%s" % (entry["name"], os.path.basename(src or "")) if entry.get("is_dir")
                  else os.path.basename(src or entry["name"])),
            "filename": entry["name"] if scope == "series" else os.path.basename(src or ""),
            "size_bytes": int(entry.get("size") or 0) if scope == "series"
            else int(next((f["size_bytes"] for f in entry["files"] if f["path"] == src), 0) or 0),
            "media_id": str(ident["tmdb_id"]), "media_source": "tmdb",
            "poster_url": ident.get("poster_url"), "target_dir": target,
            "search_ctx": json.dumps(ctx), "status": "importing", "attempts": 0,
        }
        dl_id = db.add_video_download(row)
        with _inflight_lock:
            _inflight_rows.add(dl_id)
        dl = {**row, "id": dl_id}
        if organize is None:
            organize = mon._make_organizer(db)
        if scope == "series":
            if import_pack is None:
                import_pack = mon._make_pack_importer(db, organize)
            patch = import_pack(dl, os.path.dirname(path), entry["name"]) or {
                "status": "import_failed", "error": "Couldn't read the folder"}
            if patch.get("status") != "completed":
                # a pack that placed nothing has no single file to place by hand;
                # it stays in the import folder list with the reason instead
                patch["status"] = "failed"
        else:
            patch = organize(dl, src)
        patch.setdefault("progress", 100.0)
        mon.settle_outcome(db, dl, patch)
        if patch.get("status") == "completed":
            mon._reclaim_source(patch.get("_cleanup_source"))
            if entry.get("is_dir"):
                mon._reclaim_job_dir(path)       # only empty folders go
        return {"id": dl_id, "status": patch.get("status"), "error": patch.get("error"),
                "dest_path": patch.get("dest_path"),
                "imported": patch.get("_pack_imported", 1 if patch.get("status") == "completed" else 0),
                "failed": patch.get("_pack_failed", 0)}
    except Exception as e:   # noqa: BLE001 - one bad entry must not take the folder down
        logger.exception("import folder: %s failed", entry.get("name"))
        if dl_id is not None:
            try:
                db.update_video_download(dl_id, status="failed", progress=100.0,
                                         error="Import failed: %s" % e)
            except Exception:   # noqa: BLE001
                logger.debug("import folder: couldn't mark row %s failed", dl_id, exc_info=True)
        return {"id": dl_id, "status": "failed", "error": "Import failed: %s" % e}
    finally:
        with _inflight_lock:
            _inflight.discard(path)
            if dl_id is not None:
                _inflight_rows.discard(dl_id)


def after_import(db, entry: Dict[str, Any], result: Dict[str, Any], *,
                 rescan: Optional[Callable] = None) -> None:
    """Remember an entry that is still in the folder after its import, so the
    automatic pass leaves it alone until its files change. One that is gone is
    forgotten. ``rescan(entry) -> entry or None`` reads it again."""
    left = (rescan or _rescan)(entry)
    if left is None:
        _forget(db, entry["path"])
        return
    failed = int(result.get("failed") or 0)
    if result.get("error"):
        why = str(result["error"])
    elif failed:
        why = "%d episode%s didn't import" % (failed, "" if failed == 1 else "s")
    elif result.get("status") == "completed":
        why = "Imported. What's left wasn't needed (a sample or an extra)"
    else:
        why = "Still in the folder after the import"
    _remember(db, left, why)


def _rescan(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    root = os.path.dirname(entry["path"])
    if not os.path.exists(entry["path"]):
        return None
    try:
        for e in scan(root, lister=lambda _r: [entry["name"]]):
            return e
    except OSError:
        return None
    return None


def recover_interrupted(db) -> int:
    """Rows a restart cut off mid-import: their thread is gone. The file is
    still in the folder (the move happens last), so the row is closed and the
    entry is simply listed again."""
    n = 0
    try:
        rows = db.get_active_video_downloads() or []
    except Exception:   # noqa: BLE001
        return 0
    for d in rows:
        if d.get("source") != SOURCE:
            continue
        with _inflight_lock:
            if d.get("id") in _inflight_rows:
                continue
        try:
            db.update_video_download(d["id"], status="failed", progress=100.0,
                                     error="Interrupted by a restart, it's back in the import folder list")
            n += 1
        except Exception:   # noqa: BLE001
            logger.debug("import folder: couldn't close row %s", d.get("id"), exc_info=True)
    return n


# ── the list the page shows ──────────────────────────────────────────────────

def listing(db, *, search: Optional[Callable] = None, scanner: Optional[Callable] = None,
            keep_entries: bool = False) -> Dict[str, Any]:
    """Everything the Import page shows for the folder. Entries whose file is
    already under Needs a hand (an import_failed row) are left out there."""
    st = load_settings(db)
    out = {"path": st["path"], "auto": st["auto"], "items": [], "error": None}
    if not st["path"]:
        return out
    try:
        entries = (scanner or scan)(st["path"])
    except OSError as e:
        out["error"] = "Can't read the import folder (%s)" % (e.strerror or e)
        return out
    parked = set()
    try:
        parked = {os.path.normcase(str(r.get("dest_path"))) for r in db.get_import_failed_video_downloads()
                  if r.get("dest_path")}
    except Exception:   # noqa: BLE001
        logger.debug("import folder: couldn't read the needs-a-hand list", exc_info=True)
    tried = _tried(db)
    live = set()
    for e in entries:
        live.add(e["path"])
        ident = identify(e, search)
        main = ident["guess"].get("main")
        if ident["guess"].get("scope") != "series" and main and os.path.normcase(main) in parked:
            continue        # its file is under Needs a hand already
        t = tried.get(e["path"])
        was_tried = bool(t and t.get("sig") == signature(e))
        out["items"].append({
            "key": e["key"], "name": e["name"], "path": e["path"], "is_dir": e["is_dir"],
            "size": e["size"], "files": len(e["files"]),
            "settled": e["settled"], "partial": e["partial"],
            "importing": busy(e["path"]),
            "guess": ident["guess"], "match": ident["match"],
            "tried": ({"error": t.get("error"), "at": t.get("at")} if was_tried else None),
        })
    if keep_entries:
        out["entries"] = {e["path"]: e for e in entries}
    gone = [p for p in tried if p not in live]
    if gone:
        for p in gone:
            tried.pop(p, None)
        try:
            db.set_setting(SETTING_TRIED, json.dumps(tried))
        except Exception:   # noqa: BLE001
            logger.debug("import folder: couldn't prune the tried list", exc_info=True)
    return out


def find_entry(db, key: str, *, scanner: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    st = load_settings(db)
    if not st["path"] or not key or "/" in key or "\\" in key or key in (".", ".."):
        return None
    for e in (scanner or scan)(st["path"]):
        if e["key"] == key:
            return e
    return None


def run_one(db, entry: Dict[str, Any], override: Optional[Dict[str, Any]] = None, *,
            search: Optional[Callable] = None, **seams) -> Dict[str, Any]:
    """Identify (or take the user's pick), import, remember the outcome, and
    refresh the media server when something landed."""
    ident_info = identify(entry, search)
    g = dict(ident_info["guess"])
    ident = _identity(g, ident_info["match"], override)
    entry = {**entry, "main": g.get("main")}
    result = import_entry(db, entry, ident, **seams)
    if result.get("status") == "refused":
        return result
    try:
        after_import(db, entry, result)
    except Exception:   # noqa: BLE001
        logger.debug("import folder: couldn't record the outcome", exc_info=True)
    if result.get("status") == "completed":
        try:
            from core.video.download_events import notify_batch_complete
            notify_batch_complete({"completed": int(result.get("imported") or 1), "import_folder": True})
        except Exception:   # noqa: BLE001
            logger.debug("import folder: batch notify failed", exc_info=True)
    return result


def auto_pass(db, *, search: Optional[Callable] = None, scanner: Optional[Callable] = None,
              **seams) -> int:
    """Import every settled entry whose match is sure and that wasn't tried
    already. Returns how many entries were imported."""
    st = load_settings(db)
    if not st["auto"] or not st["path"]:
        return 0
    data = listing(db, search=search, scanner=scanner, keep_entries=True)
    if data.get("error"):
        return 0
    n = 0
    for it in data["items"]:
        if it["importing"] or it["tried"] or not it["settled"] or it["match"].get("confidence") != "high":
            continue
        e = data["entries"].get(it["path"])
        if e is None:
            continue
        r = run_one(db, e, None, search=search, **seams)
        logger.info("import folder: %s -> %s%s", it["name"], r.get("status"),
                    (" (%s)" % r["error"]) if r.get("error") else "")
        if r.get("status") == "completed":
            n += 1
    return n


_started = False
_start_lock = threading.Lock()


def ensure_started(db_provider: Callable) -> None:
    """Start the automatic pass once. It rides along with the download monitor.
    Each pass checks the setting, so turning it on needs no restart."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True

    def loop():
        try:
            recover_interrupted(db_provider())
        except Exception:   # noqa: BLE001
            logger.debug("import folder: recovery failed", exc_info=True)
        while True:
            time.sleep(AUTO_INTERVAL)
            try:
                db = db_provider()
                if db is not None:
                    auto_pass(db)
            except Exception:   # noqa: BLE001
                logger.exception("import folder: automatic pass failed")

    threading.Thread(target=loop, name="video-import-folder", daemon=True).start()


def start_import(db, entry: Dict[str, Any], override: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Import one entry on a background thread (big files take a while to
    copy). Returns why it can't start, or None."""
    if busy(entry["path"]):
        return "Already importing"
    if entry.get("partial"):
        return "Still copying in, try again when it's done"
    info = identify(entry)
    ident = _identity(info["guess"], info["match"], override)
    why = check_identity(entry, ident)
    if why:
        return why
    _forget(db, entry["path"])

    def go():
        try:
            run_one(db, entry, override)
        except Exception:   # noqa: BLE001
            logger.exception("import folder: %s failed", entry.get("name"))

    _spawn(go)
    return None


def _spawn(fn: Callable) -> None:
    threading.Thread(target=fn, name="video-import-entry", daemon=True).start()


__all__ = ["scan", "guess", "match", "identify", "import_entry", "run_one", "auto_pass",
           "listing", "find_entry", "start_import", "load_settings", "save_settings",
           "ensure_started", "recover_interrupted", "signature"]
