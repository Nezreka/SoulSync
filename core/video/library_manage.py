"""The "Your library" tab on a movie or show page.

What you own of a title, where each file sits, how good it is against your
quality profile, and whether its name matches your template. The music side
has had this beside an artist's discography for a long time; the video side
only ever showed a quality line and a subtitle button.

Reads come from the database. Anything that needs the disk (is the file
reachable from here, which subtitles sit beside it, what would the template
name it) goes through the path resolver and only runs for the files asked
about: a whole movie, or ONE season of a show. A show's overview stays a
database read, because some shows have a thousand episodes on a network share.

A file SoulSync can't reach is reported as exactly that. Its facts still show
(the server reported them); only the disk-backed ones are left unknown.
"""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.video.direct_play import direct_play_verdict
from core.video.duplicate_copies import describe_copies, mount_root
from core.video.library_paths import file_quality
from core.video.quality_eval import evaluate_owned, resolution_label, resolution_rank
from utils.logging_config import get_logger

logger = get_logger("video.library_manage")

_RES_ORDER = ("4K", "1080p", "720p", "SD")


def _res_label(res: Any) -> str:
    return resolution_label(res) or ("Unknown" if res else "Unknown")


def light_file(row: dict) -> Dict[str, Any]:
    """one file's facts from the database alone (no disk)."""
    path = row.get("relative_path") or ""
    return {
        "id": row.get("file_id"),
        "path": path,
        "filename": os.path.basename(path.replace("\\", "/")),
        "root": mount_root(path),
        "size_bytes": row.get("size_bytes") or 0,
        "resolution": row.get("resolution"),
        "resolution_label": _res_label(row.get("resolution")),
        "quality": file_quality(row.get("quality"), path) or None,
        "video_codec": row.get("video_codec"),
        "audio_codec": row.get("audio_codec"),
        "audio_channels": row.get("audio_channels"),
        "dynamic_range": row.get("dynamic_range"),
        "atmos": bool(row.get("atmos")),
        "release_source": row.get("release_source"),
        "runtime_seconds": row.get("runtime_seconds"),
        "added_at": row.get("added_at"),
        "aspect": row.get("aspect"),
        "playable": direct_play_verdict(path, row.get("video_codec"), row.get("audio_codec")),
    }


class _Disk:
    """the disk-backed half, injectable for tests. one folder listing per
    folder per request."""

    def __init__(self, db, resolve: Optional[Callable] = None,
                 listdir: Optional[Callable] = None, roots: Optional[Callable] = None,
                 settings: Optional[dict] = None):
        from core.video import organization
        from core.video.path_resolver import resolve_video_file_path, video_base_dirs
        self.db = db
        self._resolve = resolve or resolve_video_file_path
        self._listdir = listdir or os.listdir
        self._base = video_base_dirs(db)
        self._roots_fn = roots
        self._roots: Dict[str, list] = {}
        self._settings = settings if settings is not None else organization.load(db)
        self._names: Dict[str, Optional[list]] = {}

    def roots(self, scope: str) -> list:
        if scope not in self._roots:
            if self._roots_fn is not None:
                self._roots[scope] = list(self._roots_fn(scope) or [])
            else:
                from core.video.library_roots import library_roots
                self._roots[scope] = library_roots(self.db, scope)
        return self._roots[scope]

    def names(self, folder: str) -> Optional[list]:
        if folder not in self._names:
            try:
                self._names[folder] = list(self._listdir(folder))
            except OSError:
                self._names[folder] = None
        return self._names[folder]

    def locate(self, row: dict) -> Optional[str]:
        try:
            return self._resolve(row.get("relative_path"), self._base,
                                 size_bytes=row.get("size_bytes"))
        except Exception:   # noqa: BLE001 - an unreachable share is a fact, not an error
            return None

    def describe(self, row: dict) -> Dict[str, Any]:
        from core.video.file_ops import expected_path, same_path
        from core.video.subtitles.existing import external_tracks
        out = light_file(row)
        local = self.locate(row)
        out["reachable"] = bool(local)
        out["local_path"] = local
        out["subtitles"] = None
        out["naming"] = None
        if not local:
            return out
        names = self.names(os.path.dirname(local))
        if names is not None:
            # language comes from the sidecar's name; reading every .srt to
            # detect an unnamed one is the subtitle worker's job, not a page load
            tracks = external_tracks(local, names=names, read_text=lambda _p: None)
            out["subtitles"] = [{"language": t.language, "hi": t.hi, "forced": t.forced}
                                for t in tracks if t.language]
        try:
            want = expected_path(row, local, self.roots(row.get("scope")), self._settings)
        except Exception:   # noqa: BLE001 - a template that won't render is shown as unknown
            logger.debug("template render failed for file %s", row.get("file_id"), exc_info=True)
            want = None
        if want:
            out["naming"] = {"expected": want, "expected_name": os.path.basename(want),
                             "conforms": same_path(local, want)}
        return out


def _profile(db, profile_id) -> Dict[str, Any]:
    from core.video import quality_profile
    prof = quality_profile.profile_by_id(db, profile_id)
    name = "Default"
    try:
        for p in quality_profile.list_profiles(db) or []:
            if str(p.get("id")) == str(profile_id):
                name = p.get("name") or name
    except Exception:   # noqa: BLE001 - a name is a label, never a blocker
        pass
    return {"id": profile_id, "name": name, "profile": prof,
            "cutoff_label": resolution_label(prof.get("cutoff_resolution")) or "Best available"}


def _best(files: List[dict]) -> Optional[dict]:
    if not files:
        return None
    return max(files, key=lambda f: (resolution_rank(f.get("resolution")), f.get("size_bytes") or 0))


def _res_mix(files: Iterable[dict]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for f in files:
        lab = f.get("resolution_label") or "Unknown"
        counts[lab] = counts.get(lab, 0) + 1
    order = list(_RES_ORDER) + sorted(k for k in counts if k not in _RES_ORDER)
    return [{"label": k, "count": counts[k]} for k in order if counts.get(k)]


def _quality_block(db, profile_id, best: Optional[dict]) -> Dict[str, Any]:
    p = _profile(db, profile_id)
    out = {"profile_id": p["id"], "profile_name": p["name"], "cutoff_label": p["cutoff_label"]}
    if best:
        ev = evaluate_owned(best, p["profile"])
        out.update({"meets": ev["meets"], "reasons": ev["reasons"]})
    return out


def _identity(db, kind: str, item_id: int) -> Dict[str, Any]:
    ident = db.title_identity(kind, item_id) or {}
    locked = set(ident.get("locked_fields") or [])
    return {"tmdb_id": ident.get("tmdb_id"), "imdb_id": ident.get("imdb_id"),
            "tvdb_id": ident.get("tvdb_id"), "match_status": ident.get("tmdb_match_status"),
            "fixed": "tmdb_id" in locked, "manual_link": ident.get("manual_link"),
            "server": ident.get("server_source")}


def _recycle_on(db) -> bool:
    from core.video import organization
    try:
        return bool(organization.load(db).get("recycle_deletes", True))
    except Exception:   # noqa: BLE001
        return True


def movie_library(db, movie_id: int, *, disk: Optional[_Disk] = None) -> Optional[Dict[str, Any]]:
    """the whole tab for one movie (every file gets the disk checks)."""
    ident = db.title_identity("movie", movie_id)
    if not ident:
        return None
    detail = db.movie_detail(movie_id) or {}
    disk = disk or _Disk(db)
    files = [disk.describe(r) for r in db.library_title_files("movie", movie_id)]
    best = _best(files)
    for f in files:
        f["best"] = best is not None and f["id"] == best["id"]
    return {
        "kind": "movie", "id": movie_id, "title": ident.get("title"), "year": ident.get("year"),
        "identity": _identity(db, "movie", movie_id),
        "summary": {"files": len(files), "size_bytes": sum(f["size_bytes"] for f in files),
                    "watched": bool(detail.get("watched")), "play_count": detail.get("play_count"),
                    "last_viewed_at": detail.get("last_viewed_at"),
                    "monitored": bool(detail.get("monitored", True)),
                    "added_at": min((f["added_at"] for f in files if f.get("added_at")), default=None)},
        "quality": _quality_block(db, detail.get("quality_profile_id"), best),
        "copies": _copies(files),
        "files": files,
        "recycle": _recycle_on(db),
    }


def _copies(files: List[dict]) -> Dict[str, Any]:
    c = describe_copies([{"file_id": f["id"], "relative_path": f["path"],
                          "size_bytes": f["size_bytes"], "resolution": f["resolution"]} for f in files])
    return {"count": len(files), "roots": c["roots"], "spans_drives": c["spans_drives"],
            "same_resolution": c["same_resolution"] if len(files) > 1 else False}


def _aired(ep: dict, today: str) -> bool:
    return bool(ep.get("has_file")) or bool(ep.get("air_date") and str(ep["air_date"])[:10] <= today)


# "Season 01", "S01", "Specials", and the Plex-style "Breaking Bad Season 5"
_SEASON_DIR = re.compile(r"(?:^|[\s._-])(?:season|series|staffel|saison|temporada)[\s._-]*\d+$"
                         r"|^specials?$|^s\d{1,3}$", re.IGNORECASE)


def _show_folders(paths: List[str]) -> List[Dict[str, Any]]:
    """the folders a show's files live in (server view), with how many files
    each holds. a season folder counts as its show folder, so a tidy show is
    one row and a show split across drives says exactly where each part is."""
    counts: Dict[str, int] = {}
    for p in paths:
        if not p:
            continue
        d = os.path.dirname(p.replace("\\", "/"))
        if _SEASON_DIR.search(os.path.basename(d) or ""):
            d = os.path.dirname(d)
        counts[d] = counts.get(d, 0) + 1
    return [{"path": k, "files": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]


def show_library(db, show_id: int, *, season: Optional[int] = None,
                 disk: Optional[_Disk] = None, today: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """the tab for one show. every season comes back with its counts, size and
    quality mix; only ``season`` (when given) gets the disk checks per file."""
    ident = db.title_identity("show", show_id)
    if not ident:
        return None
    today = today or date.today().isoformat()
    detail_meta = db.show_library_meta(show_id) or {}
    rows = db.library_title_files("show", show_id)
    disk_ids = {r["file_id"] for r in rows if season is not None and r.get("season") == season}
    if disk_ids:
        disk = disk or _Disk(db)
    by_file = {}
    for r in rows:
        by_file[r["file_id"]] = disk.describe(r) if r["file_id"] in disk_ids else light_file(r)
    files_by_ep: Dict[int, list] = {}
    for r in rows:
        files_by_ep.setdefault(r["item_id"], []).append(by_file[r["file_id"]])
    seasons: Dict[int, Dict[str, Any]] = {}
    for ep in db.library_show_episodes(show_id):
        sn = ep.get("season_number")
        s = seasons.setdefault(sn, {"season_number": sn, "owned": 0, "total": 0, "missing": 0,
                                    "upcoming": 0, "size_bytes": 0, "monitored": True,
                                    "episodes": [], "_files": []})
        files = files_by_ep.get(ep["id"], [])
        aired = _aired(ep, today)
        s["total"] += 1
        if ep.get("has_file"):
            s["owned"] += 1
        elif aired:
            s["missing"] += 1
        else:
            s["upcoming"] += 1
        s["size_bytes"] += sum(f["size_bytes"] for f in files)
        s["_files"].extend(files)
        s["monitored"] = bool(ep.get("season_monitored", 1))
        s["episodes"].append({
            "id": ep["id"], "episode_number": ep.get("episode_number"), "title": ep.get("title"),
            "air_date": ep.get("air_date"), "has_file": bool(ep.get("has_file")),
            "monitored": bool(ep.get("monitored", 1)), "watched": (ep.get("play_count") or 0) > 0,
            "aired": aired, "files": files,
        })
    season_list = []
    for sn in sorted(seasons, key=lambda n: (n == 0, n)):
        s = seasons[sn]
        s["res_mix"] = _res_mix(s.pop("_files"))
        s["title"] = "Specials" if sn == 0 else "Season %s" % sn
        season_list.append(s)
    all_files = list(by_file.values())
    best = _best(all_files)
    prof = _profile(db, detail_meta.get("quality_profile_id"))
    cutoff = prof["profile"].get("cutoff_resolution")
    for f in all_files:
        f["below_cutoff"] = bool(cutoff) and resolution_rank(f.get("resolution")) < resolution_rank(cutoff)
    for s_ in season_list:
        s_["below_cutoff"] = sum(1 for e in s_["episodes"] for f in e["files"] if f.get("below_cutoff"))
    real_seasons = [s for s in season_list if s["season_number"] != 0]
    return {
        "kind": "show", "id": show_id, "title": ident.get("title"), "year": ident.get("year"),
        "identity": _identity(db, "show", show_id),
        "summary": {
            "owned": sum(s["owned"] for s in real_seasons),
            "total": sum(s["total"] for s in real_seasons),
            "missing": sum(s["missing"] for s in real_seasons),
            "upcoming": sum(s["upcoming"] for s in real_seasons),
            "specials_owned": sum(s["owned"] for s in season_list if s["season_number"] == 0),
            "files": len(all_files),
            "size_bytes": sum(f["size_bytes"] for f in all_files),
            "res_mix": _res_mix(all_files),
            "roots": _copies(all_files)["roots"],
            "folders": _show_folders([f["path"] for f in all_files]),
            "watched_episodes": detail_meta.get("watched_episodes") or 0,
            "series_type": detail_meta.get("series_type"),
            "monitored": bool(detail_meta.get("monitored", 1)),
        },
        "quality": dict(_quality_block(db, detail_meta.get("quality_profile_id"), best),
                        below_cutoff=sum(1 for f in all_files if f.get("below_cutoff"))),
        "seasons": season_list,
        "detail_season": season,
        "recycle": _recycle_on(db),
    }


__all__ = ["light_file", "movie_library", "show_library"]
