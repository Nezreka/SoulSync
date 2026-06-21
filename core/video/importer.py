"""Post-process a finished video download into the library — the Radarr/Sonarr step.

On completion the monitor hands us the located file. We:
  1. parse the release + SANITY-GATE it (reject non-video / samples / wrong episode /
     multi-file packs we can't safely place),
  2. build the canonical library path (``library_paths``),
  3. decide IMPORT vs UPGRADE-replace vs not-an-upgrade by looking at what is already
     in the destination folder — the filesystem is the source of truth (like Radarr),
     which avoids leaning on DB columns the schema doesn't have,
  4. COPY it in (renamed), carry sibling subtitles, on an upgrade delete the worse
     existing file, and remove the source unless it's a torrent (preserve seeding).

``plan_import`` is pure (directory reads injected via ``list_dir``); ``run_import``
executes the plan through an injected ``fs`` facade so orchestration is unit-tested
without touching disk. Isolated — sibling video modules + stdlib only; no music imports.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any, Callable

from core.video import organization
from core.video.download_pipeline import basename_of
from core.video.library_paths import quality_full
from core.video.quality_eval import resolution_rank
from core.video.release_parse import parse_release

VIDEO_EXTS = frozenset({
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv",
    ".mpg", ".mpeg", ".webm", ".flv", ".m2ts",
})
SUB_EXTS = frozenset({".srt", ".sub", ".ass", ".ssa", ".idx", ".vtt", ".smi"})

_SAMPLE_MAX_BYTES = 150 * 1024 * 1024   # a "sample"-named file under this is a sample
_SAMPLE = re.compile(r"(^|[.\-_ ])sample([.\-_ ]|$)", re.I)
_SXXEXX = re.compile(r"\bS(\d{1,2})[ .]?E(\d{1,3})\b", re.I)

# A probed runtime under this (seconds) is a sample/clip, not the real thing. Movies
# get a generous floor; episodes vary wildly (shorts/cartoons) so only the absurdly
# short get caught.
_RUNTIME_FLOOR = {"movie": 15 * 60, "episode": 90}

# Source ranking for the upgrade comparison (mirrors the quality ladder order).
_SRC_RANK = {"remux": 6, "bluray": 5, "web-dl": 4, "webrip": 3, "hdtv": 2, "dvd": 1}


def ext_of(path: Any) -> str:
    """Lower-cased extension (with dot) of a path's basename, '' if none."""
    return os.path.splitext(basename_of(path))[1].lower()


def is_video(path: Any) -> bool:
    return ext_of(path) in VIDEO_EXTS


def is_sample(name: Any, size_bytes: Any) -> bool:
    """A 'sample'-tagged file that's also small (or of unknown size) is a sample."""
    if not _SAMPLE.search(basename_of(name)):
        return False
    try:
        sz = int(size_bytes or 0)
    except (TypeError, ValueError):
        sz = 0
    return sz == 0 or sz < _SAMPLE_MAX_BYTES


def quality_score(parsed: Any) -> int:
    """A self-contained quality score (resolution dominates, source breaks ties) used
    only for the local upgrade comparison. Higher = better."""
    parsed = parsed if isinstance(parsed, dict) else {}
    return resolution_rank(parsed.get("resolution")) * 10 + _SRC_RANK.get(parsed.get("source"), 0)


def _scope_of(dl: dict) -> str:
    """The import scope from the search context, falling back to the download kind.
    Only 'movie' and 'episode' are placeable; packs/youtube are gated out upstream."""
    ctx = _search_ctx(dl)
    sc = str(ctx.get("scope") or "").lower()
    if sc in ("movie", "episode", "season", "series"):
        return sc
    k = str(dl.get("kind") or "").lower()
    if k == "movie":
        return "movie"
    if k in ("show", "tv", "episode"):
        return "episode"
    return k or "movie"


def _search_ctx(dl: dict) -> dict:
    try:
        ctx = json.loads((dl or {}).get("search_ctx") or "{}")
        return ctx if isinstance(ctx, dict) else {}
    except (ValueError, TypeError):
        return {}


def _ctx(dl: dict) -> dict:
    sc = _search_ctx(dl)
    return {
        "title": sc.get("title") or (dl or {}).get("title") or "",
        "year": sc.get("year") if sc.get("year") is not None else (dl or {}).get("year"),
        "season": sc.get("season"),
        "episode": sc.get("episode"),
        "episode_title": sc.get("episode_title"),
    }


def _reject(reason: str) -> dict:
    return {"action": "reject", "reason": reason, "manual": True}


def _existing_match(scope: str, dest_dir: str, ctx: dict, list_dir: Callable) -> str | None:
    """The basename of a file ALREADY in the destination folder that represents this
    same item (any video for a movie; a matching SxxExx for an episode), or None.
    ``list_dir(dir)`` yields basenames; a missing dir yields nothing."""
    try:
        names = [str(n) for n in (list_dir(dest_dir) or [])]
    except Exception:   # noqa: BLE001 - a missing/denied dir simply means "nothing there"
        return None
    vids = [n for n in names if ext_of(n) in VIDEO_EXTS and not is_sample(n, None)]
    if scope == "movie":
        return max(vids, key=len) if vids else None
    if scope == "episode":
        try:
            ws, we = int(ctx.get("season")), int(ctx.get("episode"))
        except (TypeError, ValueError):
            return None
        for n in vids:
            m = _SXXEXX.search(n)
            if m and int(m.group(1)) == ws and int(m.group(2)) == we:
                return n
        return None
    return None


def plan_import(dl: dict, src_path: str, *, list_dir: Callable, probe: dict | None = None,
                settings: dict | None = None, force: bool = False,
                override: dict | None = None) -> dict:
    """Decide what to do with a finished download. Returns one of:

      {"action": "import",  "dest": {...}, "quality_label": str}
      {"action": "upgrade", "dest": {...}, "replace_path": str, "quality_label": str}
      {"action": "reject",  "reason": str, "manual": True}

    Pure: all directory reads go through ``list_dir`` (injected). ``probe`` is the
    ffprobe ``mediainfo`` result (or None when ffprobe is unavailable) — when present
    we trust the FILE's real resolution over the scene name and reject corrupt /
    too-short junk. ``settings`` are the user's organisation settings (naming templates
    + replace policy); None = defaults.

    MANUAL placement: ``force=True`` with an ``override`` ({scope, title, year, season,
    episode, episode_title, target_dir, media_id}) trusts the user's chosen identity —
    it skips the auto sanity-gates (sample / wrong-episode / pack / not-an-upgrade) and
    files the file exactly where they said, replacing any worse copy. ffprobe is still
    used for the true resolution, but never to reject."""
    dl = dl or {}
    settings = organization.normalize(settings)
    override = override or {}
    scope = str(override.get("scope") or _scope_of(dl)).lower() if force else _scope_of(dl)
    name = basename_of(src_path)
    ext = ext_of(src_path)
    parsed = parse_release(dl.get("release_title") or name)
    ctx = _ctx(dl)
    if force:   # the user told us what it is — let their identity win
        for k in ("title", "year", "season", "episode", "episode_title"):
            if override.get(k) is not None:
                ctx[k] = override.get(k)

    if not is_video(src_path):   # can't place a non-video, even on a forced import
        return _reject("Not a video file (%s)" % (ext or "no extension"))
    if not force:
        if is_sample(name, dl.get("size_bytes")):
            return _reject("Looks like a sample, not the feature")
        if scope not in ("movie", "episode"):
            return _reject("Season/complete packs need manual import")
        if scope == "episode":
            if ctx.get("season") is None or ctx.get("episode") is None:
                return _reject("Missing season/episode info")
            # If the release name itself names a DIFFERENT episode, don't mis-file it.
            if parsed.get("episode") is not None and (
                    parsed.get("season") != ctx.get("season")
                    or parsed.get("episode") != ctx.get("episode")):
                return _reject("Release is S%02dE%02d, not the episode requested"
                               % (parsed.get("season") or 0, parsed.get("episode") or 0))
    else:
        if scope not in ("movie", "episode"):
            return _reject("Pick a movie or an episode to place this file")
        if scope == "episode" and (ctx.get("season") is None or ctx.get("episode") is None):
            return _reject("Pick a season and episode to place this file")

    # ffprobe verification — best-effort; on a forced placement we use the real
    # resolution but never reject on it (the user has decided).
    if probe is not None:
        if not force:
            if not probe.get("ok"):
                return _reject("No readable video stream — corrupt or fake file")
            dur = probe.get("duration_sec") or 0
            floor = _RUNTIME_FLOOR.get(scope)
            if floor and 0 < dur < floor:
                return _reject("Runtime is only %d min — looks like a sample/clip, not the %s"
                               % (int(dur // 60), scope))
        # Trust the FILE over the (often lying) scene name: real resolution always,
        # real codec only when the name didn't carry one.
        parsed = dict(parsed)
        if probe.get("resolution"):
            parsed["resolution"] = probe["resolution"]
        if probe.get("video_codec") and not parsed.get("codec"):
            parsed["codec"] = probe["video_codec"]

    root = (override.get("target_dir") if force else None) or dl.get("target_dir") or ""
    if not root:
        return _reject("No library folder configured for this type")

    media_id = override.get("media_id") if force else dl.get("media_id")
    quality = quality_full(parsed)
    fields = {
        "title": ctx.get("title"), "year": ctx.get("year"),
        "series": ctx.get("title"), "season": ctx.get("season"),
        "episode": ctx.get("episode"), "episode_title": ctx.get("episode_title"),
        "quality": quality, "resolution": parsed.get("resolution"),
        "source": parsed.get("source"), "codec": parsed.get("codec"),
        "tmdbid": media_id if scope == "movie" else None,
        "tvdbid": media_id if scope == "episode" else None,
    }
    dest = organization.render_path(scope, root, fields, settings, ext)
    # Where poster.jpg goes: the movie folder, or the SHOW root for an episode
    # (parent of the Season folder) — so it isn't dropped per-season.
    artwork_dir = dest["dir"] if scope == "movie" else os.path.dirname(dest["dir"])

    existing = _existing_match(scope, dest["dir"], ctx, list_dir)
    if existing:
        # A forced placement replaces whatever's there (the user chose to put it here).
        if force:
            return {"action": "upgrade", "dest": dest, "quality_label": quality,
                    "replace_path": os.path.join(dest["dir"], existing), "artwork_dir": artwork_dir}
        if not settings.get("replace_existing", True):
            return _reject("Already in the library (%s) — replace is turned off" % existing)
        new_score = quality_score(parsed)
        old_score = quality_score(parse_release(existing))
        if new_score > old_score:
            return {"action": "upgrade", "dest": dest, "quality_label": quality,
                    "replace_path": os.path.join(dest["dir"], existing), "artwork_dir": artwork_dir}
        return _reject("Not an upgrade over the copy already in the library (%s)" % existing)

    return {"action": "import", "dest": dest, "quality_label": quality, "artwork_dir": artwork_dir}


def plan_subs(src_path: str, dest_path: str, list_dir: Callable) -> list:
    """Sibling subtitle files to carry alongside the video, renamed to match the
    destination stem (preserving any language suffix, e.g. '.en.srt'). Returns a list
    of (src_abs, dest_abs) pairs. ``list_dir`` lists the SOURCE directory."""
    src_dir = os.path.dirname(src_path) or "."
    v_stem = os.path.splitext(basename_of(src_path))[0]
    d_stem = os.path.splitext(basename_of(dest_path))[0]
    d_dir = os.path.dirname(dest_path)
    out = []
    try:
        names = [str(n) for n in (list_dir(src_dir) or [])]
    except Exception:   # noqa: BLE001
        return out
    for n in names:
        if ext_of(n) not in SUB_EXTS:
            continue
        stem, ext = os.path.splitext(n)
        if stem == v_stem:
            extra = ""                                  # movie.srt → <dest>.srt
        elif stem.startswith(v_stem + "."):
            extra = stem[len(v_stem):]                  # movie.en.srt → <dest>.en.srt
        else:
            continue
        out.append((os.path.join(src_dir, n), os.path.join(d_dir, d_stem + extra + ext)))
    return out


def run_import(dl: dict, src_path: str, *, fs: Any, prober: Callable | None = None,
               settings: dict | None = None, force: bool = False,
               override: dict | None = None) -> dict:
    """Execute the import and return a DB patch dict for the download row.

    ``fs`` is an injected facade with: ``list_dir(dir)->iterable[name]``,
    ``makedirs(dir)``, ``copy(src, dst)``, ``move(src, dst)``, ``remove(path)``.
    ``prober(path)->mediainfo`` is an optional ffprobe hook (None = skip verification).
    ``settings`` are the user's organisation settings (transfer mode, subtitle carry);
    None = defaults. ``force``/``override`` drive a MANUAL placement (see ``plan_import``).
    A reject becomes an ``import_failed`` row with ``dest_path`` pointing at the file's
    current (unplaced) location so the Import page can resolve it; a success becomes a
    ``completed`` row with ``dest_path`` set to its final home."""
    settings = organization.normalize(settings)
    probe_info = None
    if prober is not None:
        try:
            probe_info = prober(src_path)
        except Exception:   # noqa: BLE001 - a probe crash must not block the import
            probe_info = None
    plan = plan_import(dl, src_path, list_dir=fs.list_dir, probe=probe_info,
                       settings=settings, force=force, override=override)
    if plan["action"] == "reject":
        # Leave the file where it is; remember WHERE so manual import can find it.
        return {"status": "import_failed", "progress": 100.0, "error": plan["reason"],
                "dest_path": src_path}

    dest = plan["dest"]
    move_mode = settings.get("transfer_mode") == "move"
    try:
        fs.makedirs(dest["dir"])
        if move_mode:
            fs.move(src_path, dest["path"])
        else:
            fs.copy(src_path, dest["path"])
        if settings.get("carry_subtitles", True):
            for sub_src, sub_dst in plan_subs(src_path, dest["path"], fs.list_dir):
                try:
                    fs.copy(sub_src, sub_dst)
                except Exception:   # noqa: BLE001 - a subtitle that won't copy isn't fatal
                    pass
        if plan["action"] == "upgrade" and plan.get("replace_path"):
            try:
                fs.remove(plan["replace_path"])
            except Exception:   # noqa: BLE001 - failing to delete the old file isn't fatal
                pass
        # Copy mode reclaims the download copy UNLESS it's a torrent (keep seeding);
        # move mode already relocated it.
        if not move_mode and str(dl.get("source") or "").lower() != "torrent":
            try:
                fs.remove(src_path)
            except Exception:   # noqa: BLE001
                pass
    except Exception as e:   # noqa: BLE001 - any copy/mkdir failure → manual import
        return {"status": "import_failed", "progress": 100.0, "error": "Import failed: " + str(e),
                "dest_path": src_path}

    return {"status": "completed", "progress": 100.0, "dest_path": dest["path"],
            "quality_label": plan.get("quality_label") or dl.get("quality_label")}


class _RealFS:
    """The production filesystem facade for ``run_import`` (os/shutil)."""

    @staticmethod
    def list_dir(path):
        try:
            return os.listdir(str(path or ""))
        except OSError:
            return []

    @staticmethod
    def makedirs(path):
        os.makedirs(str(path or "."), exist_ok=True)

    @staticmethod
    def copy(src, dst):
        shutil.copy2(src, dst)

    @staticmethod
    def move(src, dst):
        shutil.move(src, dst)

    @staticmethod
    def save_url(url, dst):
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "SoulSync"})
        with urllib.request.urlopen(req, timeout=20) as resp, open(dst, "wb") as f:
            shutil.copyfileobj(resp, f)

    @staticmethod
    def write_text(path, content):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def remove(path):
        os.remove(path)


def real_fs() -> _RealFS:
    return _RealFS()


__all__ = [
    "VIDEO_EXTS", "SUB_EXTS", "ext_of", "is_video", "is_sample", "quality_score",
    "plan_import", "plan_subs", "run_import", "real_fs",
]
