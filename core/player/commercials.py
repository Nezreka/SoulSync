"""Zero-download Retro Commercial Breaks engine for Live Guide channels.

Sources short (12s-95s) retro TV commercials from:
1. Optional local folder (`SOULSYNC_COMMERCIALS_DIR` or `database/commercials/`).
2. Internet Archive (`archive.org`) public collections (`classic_tv_commercials`,
   `television_commercials`, `prelinger`) via their public JSON search & metadata
   APIs — cached lazily in a bounded local rolling cache
   (`database/player-cache/commercials/`, max 500 MB) so breaks play instantly
   and direct-play on Chromecast without CORS or SSL issues.

SoulSync never bundles or redistributes third-party commercial files in the repo;
the feature is 100% opt-in per profile (`commercials_enabled: False` by default).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("player.commercials")

ERAS = ("auto", "70s", "80s", "90s", "2000s", "kids", "mix")
MODES = ("short", "cable")

MAX_CACHE_BYTES = 500 * 1024 * 1024
MAX_CLIP_BYTES = 16 * 1024 * 1024
MIN_CLIP_SEC = 10
MAX_CLIP_SEC = 95
# a fetch fills the cache to here, split across the eras it fetches: most of
# the 500 MB, leaving room so the oldest spots aren't pruned as new ones land
FILL_BYTES = int(MAX_CACHE_BYTES * 0.9)
# archive.org search pages read per era, deepest first only when needed
SEARCH_PAGES = 6
# once a week a tenth of the space is fetched in spots never had before, and
# the next day the oldest go to make room, so the breaks don't stay the same
# set forever. a tenth keeps the cache under 500 MB in between (450 + 45)
ROTATE_EVERY_SEC = 7 * 86400
ROTATE_SHARE = 0.1
ROTATE_PAGES = 20
HTTP_TIMEOUT = 15

_ARCHIVE_QUERIES: Dict[str, str] = {
    "70s": 'mediatype:movies AND (collection:classic_tv_commercials OR collection:television_commercials OR collection:prelinger) AND ("1970s" OR "70s" OR 1975 OR 1978) AND commercial',
    "80s": 'mediatype:movies AND (collection:classic_tv_commercials OR collection:television_commercials OR collection:prelinger) AND ("1980s" OR "80s" OR 1984 OR 1987) AND commercial',
    "90s": 'mediatype:movies AND (collection:classic_tv_commercials OR collection:television_commercials) AND ("1990s" OR "90s" OR 1993 OR 1996) AND commercial',
    "2000s": 'mediatype:movies AND (collection:classic_tv_commercials OR collection:television_commercials) AND ("2000s" OR 2001 OR 2003) AND commercial',
    "kids": 'mediatype:movies AND (collection:classic_tv_commercials OR collection:television_commercials) AND (toy OR cereal OR cartoon OR nickelodeon) AND commercial',
    "horror": 'mediatype:movies AND (collection:classic_tv_commercials OR collection:television_commercials) AND (halloween OR horror OR spooky OR monster) AND commercial',
    "mix": 'mediatype:movies AND (collection:classic_tv_commercials OR collection:television_commercials) AND commercial',
}


_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")


def extract_year(*texts: Any) -> Optional[int]:
    """Extract a plausible broadcast year (1950-2029) from Archive.org metadata or titles."""
    for raw in texts:
        if raw is None:
            continue
        m = _YEAR_RE.search(str(raw))
        if m:
            y = int(m.group(1))
            if 1950 <= y <= 2029:
                return y
    return None


def era_for_year(year: Optional[int]) -> Optional[str]:
    if year is None:
        return None
    if 1970 <= year <= 1979:
        return "70s"
    if 1980 <= year <= 1989:
        return "80s"
    if 1990 <= year <= 1999:
        return "90s"
    if 2000 <= year <= 2011:
        return "2000s"
    return None


@dataclass
class CommercialClip:
    id: str
    title: str
    era: str
    duration_seconds: int
    path: str
    source: str = "archive"  # "archive" | "local"
    archive_id: str = ""
    year: Optional[int] = None
    size_bytes: int = 0
    added_at: float = 0.0

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "era": self.era,
            "year": self.year,
            "duration_seconds": self.duration_seconds,
            "source": self.source,
            "stream_path": f"/api/player/stream/commercial/{self.id}",
        }


_lock = threading.Lock()
_warming = False
_last_error: Optional[str] = None
_clips_cache: Dict[str, CommercialClip] = {}
_loaded = False


def cache_dir() -> Path:
    base = os.environ.get("SOULSYNC_DATA_DIR") or "database"
    d = Path(base) / "player-cache" / "commercials"
    d.mkdir(parents=True, exist_ok=True)
    return d


def local_dir() -> Optional[Path]:
    env = os.environ.get("SOULSYNC_COMMERCIALS_DIR", "").strip()
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    default_p = Path(os.environ.get("SOULSYNC_DATA_DIR") or "database") / "commercials"
    return default_p if default_p.is_dir() else None


def infer_era(channel_id: str = "", channel_name: str = "") -> str:
    text = f"{channel_id} {channel_name}".lower()
    if any(k in text for k in ("1970", "70s")):
        return "70s"
    if any(k in text for k in ("1980", "80s")):
        return "80s"
    if any(k in text for k in ("1990", "90s")):
        return "90s"
    if any(k in text for k in ("2000", "00s")):
        return "2000s"
    if any(k in text for k in ("kid", "cartoon", "animat", "family", "disney", "nick", "saturday")):
        return "kids"
    if any(k in text for k in ("horror", "late", "thriller", "halloween", "scifi", "sci-fi")):
        return "horror"
    return "mix"


def _manifest_path() -> Path:
    return cache_dir() / "manifest.json"


def _load_locked() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    mp = _manifest_path()
    if mp.exists():
        try:
            raw = json.loads(mp.read_text(encoding="utf-8"))
            for row in raw if isinstance(raw, list) else []:
                p = Path(row.get("path", ""))
                if p.is_file() and p.stat().st_size > 1024:
                    title_s = str(row.get("title") or "Retro Commercial")
                    clip_year = row.get("year")
                    if clip_year is None:
                        clip_year = extract_year(title_s, row.get("archive_id"))
                    clip = CommercialClip(
                        id=str(row["id"]),
                        title=title_s,
                        era=str(row.get("era") or "mix"),
                        duration_seconds=int(row.get("duration_seconds") or 30),
                        path=str(p),
                        source=str(row.get("source") or "archive"),
                        archive_id=str(row.get("archive_id") or ""),
                        year=int(clip_year) if clip_year else None,
                        size_bytes=int(p.stat().st_size),
                        added_at=float(row.get("added_at") or 0.0),
                    )
                    _clips_cache[clip.id] = clip
        except Exception:  # noqa: BLE001
            logger.debug("could not read commercials manifest", exc_info=True)
    _scan_local_locked()


def _scan_local_locked() -> None:
    ld = local_dir()
    if ld is None:
        return
    exts = {".mp4", ".m4v", ".webm", ".mkv"}
    for entry in sorted(ld.rglob("*")):
        if not entry.is_file() or entry.suffix.lower() not in exts:
            continue
        cid = "loc-" + hashlib.sha1(str(entry).encode("utf-8")).hexdigest()[:12]
        if cid in _clips_cache:
            continue
        era = infer_era(entry.parent.name, entry.stem)
        _clips_cache[cid] = CommercialClip(
            id=cid,
            title=entry.stem.replace("_", " ").replace("-", " ").strip() or "Commercial",
            era=era,
            duration_seconds=30,
            path=str(entry),
            source="local",
            year=extract_year(entry.stem, entry.parent.name),
            size_bytes=entry.stat().st_size,
            added_at=entry.stat().st_mtime,
        )


def _save_manifest_locked() -> None:
    try:
        rows = [asdict(c) for c in _clips_cache.values() if c.source == "archive"]
        tmp = _manifest_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        tmp.replace(_manifest_path())
    except OSError:
        pass


def _prune_cache_locked() -> None:
    arch = sorted(
        [c for c in _clips_cache.values() if c.source == "archive"],
        key=lambda c: c.added_at,
    )
    total = sum(c.size_bytes for c in arch)
    while total > MAX_CACHE_BYTES and len(arch) > 4:
        victim = arch.pop(0)
        _clips_cache.pop(victim.id, None)
        total -= victim.size_bytes
        try:
            Path(victim.path).unlink(missing_ok=True)
        except OSError:
            pass
    _save_manifest_locked()


def _http_json(url: str) -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(url, headers={"User-Agent": "SoulSync-Player/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("archive.org request failed for %s: %s", url, exc)
        return None


def _parse_duration_sec(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if ":" in s:
        parts = s.split(":")
        try:
            nums = [float(p) for p in parts]
            if len(nums) == 3:
                return int(nums[0] * 3600 + nums[1] * 60 + nums[2])
            if len(nums) == 2:
                return int(nums[0] * 60 + nums[1])
        except ValueError:
            return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _era_bytes(era: str) -> int:
    with _lock:
        return sum(c.size_bytes for c in _clips_cache.values() if c.era == era and c.source == "archive")


def _search_docs(era: str, page: int) -> list:
    query = _ARCHIVE_QUERIES.get(era, _ARCHIVE_QUERIES["mix"])
    params = urllib.parse.urlencode({
        "q": query,
        "fl[]": ["identifier", "title"],
        "rows": "100",
        "page": str(page),
        "output": "json",
    }, doseq=True)
    data = _http_json(f"https://archive.org/advancedsearch.php?{params}")
    return (data or {}).get("response", {}).get("docs", []) or []


def _download_archive_era(era: str, needed: int = 0, *, fill_bytes: int = 0,
                          pages: int = SEARCH_PAGES, skip: Optional[set] = None) -> int:
    """fetch new spots for one era: ``needed`` more of them, or with
    ``fill_bytes`` until the era's spots reach that size. pages through the
    search, skipping archive items already cached (and ``skip``: ones had
    before). returns how many it added"""
    added = 0
    for page in range(1, pages + 1):
        if (needed and added >= needed) or (fill_bytes and _era_bytes(era) >= fill_bytes):
            break
        docs = _search_docs(era, page)
        if not docs:
            break
        with _lock:
            have = {c.archive_id for c in _clips_cache.values()} | (skip or set())
        docs = [d for d in docs if str(d.get("identifier") or "").strip() not in have]
        random.shuffle(docs)
        want = (needed - added) if needed else 10_000
        added += _download_docs(era, docs, want, fill_bytes)
    return added


def _download_docs(era: str, docs: list, needed: int, fill_bytes: int = 0) -> int:
    if not docs:
        return 0
    added = 0
    dest_dir = cache_dir()
    for doc in docs:
        if added >= needed or (fill_bytes and _era_bytes(era) >= fill_bytes):
            break
        ident = str(doc.get("identifier") or "").strip()
        if not ident:
            continue
        meta = _http_json(f"https://archive.org/metadata/{urllib.parse.quote(ident)}")
        if not meta:
            continue
        files = meta.get("files") or []
        item_title = str((meta.get("metadata") or {}).get("title") or doc.get("title") or ident)
        for f in files:
            if added >= needed:
                break
            name = str(f.get("name") or "")
            if not name.lower().endswith(".mp4"):
                continue
            try:
                size = int(f.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            if size <= 10_000 or size > MAX_CLIP_BYTES:
                continue
            dur = _parse_duration_sec(f.get("length"))
            if dur is None or not (MIN_CLIP_SEC <= dur <= MAX_CLIP_SEC):
                continue
            cid = "arc-" + hashlib.sha1(f"{ident}/{name}".encode("utf-8")).hexdigest()[:12]
            with _lock:
                if cid in _clips_cache:
                    continue
            out_path = dest_dir / f"{cid}.mp4"
            dl_url = f"https://archive.org/download/{urllib.parse.quote(ident)}/{urllib.parse.quote(name)}"
            try:
                req = urllib.request.Request(dl_url, headers={"User-Agent": "SoulSync-Player/1.0"})
                tmp_path = out_path.with_suffix(".tmp")
                with urllib.request.urlopen(req, timeout=30) as resp, tmp_path.open("wb") as fh:
                    written = 0
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_CLIP_BYTES:
                            raise ValueError("clip exceeded size cap")
                        fh.write(chunk)
                tmp_path.replace(out_path)
                md = meta.get("metadata") or {}
                clip_year = extract_year(md.get("year"), md.get("date"), item_title, name, ident)
                clip_title = item_title[:64].strip() or "Retro Commercial"
                clip = CommercialClip(
                    id=cid,
                    title=clip_title,
                    era=era,
                    duration_seconds=dur,
                    path=str(out_path),
                    source="archive",
                    archive_id=ident,
                    year=clip_year,
                    size_bytes=out_path.stat().st_size,
                    added_at=time.time(),
                )
                with _lock:
                    _clips_cache[cid] = clip
                    _prune_cache_locked()
                added += 1
                break  # 1 clip per Archive item for maximum variety
            except Exception as exc:  # noqa: BLE001
                logger.debug("commercial download skipped %s: %s", dl_url, exc)
                try:
                    out_path.with_suffix(".tmp").unlink(missing_ok=True)
                except OSError:
                    pass
    return added


def _rotation_path() -> Path:
    return cache_dir() / "rotation.json"


def _rotation() -> Dict[str, Any]:
    """{last: when the set last rotated, seen: every archive item ever fetched}"""
    try:
        raw = json.loads(_rotation_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {"last": float(raw.get("last") or 0), "seen": set(raw.get("seen") or []),
                    "trim_after": raw.get("trim_after") or None}
    except (OSError, ValueError, TypeError):
        pass
    return {"last": 0.0, "seen": set(), "trim_after": None}


def _save_rotation(state: Dict[str, Any]) -> None:
    try:
        tmp = _rotation_path().with_suffix(".tmp")
        tmp.write_text(json.dumps({"last": state["last"], "seen": sorted(state["seen"])[-5000:],
                                   "trim_after": state.get("trim_after")}), encoding="utf-8")
        tmp.replace(_rotation_path())
    except OSError:
        logger.debug("could not save the commercials rotation", exc_info=True)


def _targets(era: str) -> List[str]:
    return [era] if era in _ARCHIVE_QUERIES and era != "mix" else ["80s", "90s", "mix"]


def _trim_era(era: str, keep_bytes: int) -> int:
    """drop an era's oldest fetched spots until it fits ``keep_bytes``"""
    removed = 0
    with _lock:
        arch = sorted([c for c in _clips_cache.values() if c.era == era and c.source == "archive"],
                      key=lambda c: c.added_at)
        total = sum(c.size_bytes for c in arch)
        while total > keep_bytes and len(arch) > 4:
            victim = arch.pop(0)
            _clips_cache.pop(victim.id, None)
            total -= victim.size_bytes
            removed += 1
            try:
                Path(victim.path).unlink(missing_ok=True)
            except OSError:
                pass
        if removed:
            _save_manifest_locked()
    return removed


def maybe_rotate(era: str = "mix", *, now: Optional[float] = None) -> bool:
    """once a week: fetch a tenth of the space in spots never had before; the
    next day, drop the oldest to make room. the guide reads the config at the
    start of each day, before it freezes the day's breaks, and that calls
    this, so a spot a frozen day plays is never deleted under it. True when a
    rotation started"""
    global _warming
    now = time.time() if now is None else now
    if not _server_enabled():
        return False
    state = _rotation()
    today = _dt.date.fromtimestamp(now).isoformat()
    if state.get("trim_after") and today > state["trim_after"]:
        for t in _targets(era):
            _trim_era(t, FILL_BYTES // len(_targets(era)))
        state["trim_after"] = None
        _save_rotation(state)
    if not state["last"]:
        # the clock starts with the first fill, not on its first day
        state["last"] = now
        _save_rotation(state)
        return False
    if now - state["last"] < ROTATE_EVERY_SEC:
        return False
    targets = _targets(era)
    share = FILL_BYTES // len(targets)
    with _lock:
        _load_locked()
        if _warming:
            return False
        _warming = True
    state["last"] = now
    _save_rotation(state)

    def _run():
        global _warming, _last_error
        try:
            with _lock:
                seen = state["seen"] | {c.archive_id for c in _clips_cache.values()}
            for t in targets:
                fresh = int(share * ROTATE_SHARE)
                _download_archive_era(t, fill_bytes=_era_bytes(t) + fresh, pages=ROTATE_PAGES, skip=seen)
            with _lock:
                state["seen"] = seen | {c.archive_id for c in _clips_cache.values()}
            # the old ones go tomorrow, before tomorrow's breaks are made
            state["trim_after"] = today
            _save_rotation(state)
            logger.info("commercials: rotated in a fresh set of spots")
        except Exception as exc:  # noqa: BLE001
            _last_error = str(exc)
            logger.warning("commercials rotation failed: %s", exc)
        finally:
            with _lock:
                _warming = False

    threading.Thread(target=_run, name="commercials-rotate", daemon=True).start()
    return True


def _server_enabled() -> bool:
    """the admin's switch. off (or unreadable): nothing downloads"""
    try:
        from database.player_database import get_player_database
        return bool(get_server_config(get_player_database())["enabled"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("commercials: couldn't read the server switch: %s", exc)
        return False


def warm_async(era: str = "mix", *, enabled: Optional[bool] = None) -> bool:
    """Kick off a non-blocking background fill of retro commercials from Archive.org.

    only while the admin has commercials on: any profile (or paired phone)
    asking for a break must not make the server download from archive.org
    when they're off. ``enabled`` skips the lookup when the caller knows."""
    global _warming, _last_error
    if not (enabled if enabled is not None else _server_enabled()):
        return False
    targets = _targets(era)
    with _lock:
        _load_locked()
        if _warming:
            return False
        # the cache is already as full as a fetch fills it: nothing to do,
        # so say so instead of flashing "fetching" for a job that ends at once
        share = FILL_BYTES // len(targets)
        if all(sum(c.size_bytes for c in _clips_cache.values()
                   if c.era == t and c.source == "archive") >= share for t in targets):
            return False
        _warming = True
        _last_error = None

    def _run():
        global _warming, _last_error
        try:
            share = FILL_BYTES // len(targets)
            for target_era in targets:
                if _era_bytes(target_era) < share:
                    _download_archive_era(target_era, fill_bytes=share)
        except Exception as exc:  # noqa: BLE001
            _last_error = str(exc)
            logger.warning("commercials warm failed: %s", exc)
        finally:
            with _lock:
                _warming = False

    threading.Thread(target=_run, name="commercials-warm", daemon=True).start()
    return True


def status() -> Dict[str, Any]:
    with _lock:
        _load_locked()
        _scan_local_locked()
        by_era: Dict[str, int] = {}
        local_count = 0
        cached_count = 0
        total_bytes = 0
        for c in _clips_cache.values():
            by_era[c.era] = by_era.get(c.era, 0) + 1
            total_bytes += c.size_bytes
            if c.source == "local":
                local_count += 1
            else:
                cached_count += 1
        return {
            "ready": len(_clips_cache) > 0,
            "total_count": len(_clips_cache),
            "cached_count": cached_count,
            "local_count": local_count,
            "cache_mb": round(total_bytes / (1024 * 1024), 1),
            "max_cache_mb": MAX_CACHE_BYTES // (1024 * 1024),
            "warming": _warming,
            "eras": by_era,
            "error": _last_error,
        }


def clear_cache() -> Dict[str, Any]:
    """Delete all auto-cached Archive.org commercial clips (keeps user local folder clips)."""
    with _lock:
        _load_locked()
        removed = 0
        for cid in list(_clips_cache.keys()):
            clip = _clips_cache[cid]
            if clip.source == "archive":
                _clips_cache.pop(cid, None)
                try:
                    Path(clip.path).unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
        try:
            _manifest_path().unlink(missing_ok=True)
        except OSError:
            pass
    out = status()
    out["removed"] = removed
    return out


def list_clips_sorted() -> List[Dict[str, Any]]:
    """Deterministic sorted list of all ready commercial clips (shared across all clients)."""
    with _lock:
        _load_locked()
        _scan_local_locked()
        valid = [c for c in _clips_cache.values() if Path(c.path).is_file()]
    valid.sort(key=lambda c: c.id)
    return [c.public() for c in valid]


def get_server_config(db: Any) -> Dict[str, Any]:
    """Read server-wide Live Guide commercial settings (stored under profile_id=0)."""
    from core.player import settings as S
    raw = S.get(db, 0)
    return {
        "enabled": bool(raw.get("commercials_enabled", False)),
        "era": str(raw.get("commercials_era") or "auto"),
        "mode": str(raw.get("commercials_mode") or "cable"),
    }


def put_server_config(db: Any, body: Any) -> Dict[str, Any]:
    """Update server-wide Live Guide commercial settings (admin-only)."""
    from core.player import settings as S
    if not isinstance(body, dict):
        raise S.SettingsError("expected object")
    # a null is "leave it": the mobile app sends every field, the unset ones
    # as null, and null used to read as era "none" (refused) or enabled off
    changes: Dict[str, Any] = {}
    if body.get("enabled") is not None:
        changes["commercials_enabled"] = bool(body["enabled"])
    if body.get("era") is not None:
        changes["commercials_era"] = str(body["era"]).strip().lower()
    if body.get("mode") is not None:
        changes["commercials_mode"] = str(body["mode"]).strip().lower()
    if not changes:
        raise S.SettingsError("nothing to change")
    out = S.put(db, 0, changes)
    if out.get("commercials_enabled"):
        warm_async(out.get("commercials_era") or "mix", enabled=True)
    return {
        "enabled": bool(out.get("commercials_enabled", False)),
        "era": str(out.get("commercials_era") or "auto"),
        "mode": str(out.get("commercials_mode") or "cable"),
    }


def get_clip(clip_id: str) -> Optional[CommercialClip]:
    with _lock:
        _load_locked()
        clip = _clips_cache.get(clip_id)
        if clip and Path(clip.path).is_file():
            return clip
    return None


def pick_break(
    era: str = "auto",
    channel_id: str = "",
    channel_name: str = "",
    mode: str = "short",
    max_seconds: Optional[int] = None,
    item_year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return 1-3 retro commercials for a break between Live Guide programs.

    In ``era="auto"`` mode, if ``item_year`` is known (e.g. a 1985 movie or 1994
    episode), prioritizes commercials within ±4 years of ``item_year``, then
    same decade, then channel vibe / random mix.
    """
    year_era = era_for_year(item_year) if era == "auto" else None
    resolved_era = year_era or (infer_era(channel_id, channel_name) if era == "auto" else era)
    with _lock:
        _load_locked()
        _scan_local_locked()
        all_valid = [c for c in _clips_cache.values() if Path(c.path).is_file()]

    if not all_valid:
        warm_async(resolved_era)
        return []

    # Tier 1: within ±4 years of the playing title when era is auto & year is vintage
    close_year: List[CommercialClip] = []
    if era == "auto" and item_year and year_era is not None:
        close_year = [c for c in all_valid if c.year is not None and abs(c.year - item_year) <= 4]

    # Tier 2: matching decade / era
    exact_era = [c for c in all_valid if c.era == resolved_era]
    if len(exact_era) < 2:
        warm_async(resolved_era)

    target_count = 3 if mode == "cable" else 2
    budget = max_seconds if max_seconds and max_seconds > 0 else (120 if mode == "cable" else 65)

    # Build tiered candidate order (shuffled within each tier for freshness)
    tiers = [close_year, exact_era, all_valid]
    ordered_pool: List[CommercialClip] = []
    seen_ids: set[str] = set()
    for tier in tiers:
        shuffled = list(tier)
        random.shuffle(shuffled)
        for clip in shuffled:
            if clip.id not in seen_ids:
                seen_ids.add(clip.id)
                ordered_pool.append(clip)

    picked: List[CommercialClip] = []
    used_sec = 0
    for clip in ordered_pool:
        if len(picked) >= target_count:
            break
        if picked and used_sec + clip.duration_seconds > budget:
            continue
        picked.append(clip)
        used_sec += clip.duration_seconds
    return [c.public() for c in picked]


def pick_breaks_for_queue(
    items: List[Dict[str, Any]],
    era: str = "auto",
    channel_id: str = "",
    channel_name: str = "",
    mode: str = "cable",
    min_gap_seconds: int = 300,
) -> Dict[str, List[Dict[str, Any]]]:
    """Plan continuous, non-repeating commercial breaks across an entire Live Guide queue.

    Inserts a year-matched break after each show once at least ``min_gap_seconds``
    (default 5 minutes) of content has aired since the previous break, so short
    back-to-back clips never trigger consecutive ad breaks.
    """
    if not items:
        return {}
    breaks: Dict[str, List[Dict[str, Any]]] = {}
    recently_used: set[str] = set()
    content_since_break = 0
    for idx, item in enumerate(items[:-1]):  # no break needed after the last item
        dur = int(item.get("duration_seconds") or 0)
        content_since_break += dur if dur > 0 else 1800
        if content_since_break < min_gap_seconds:
            continue
        item_year = item.get("year")
        try:
            year_int = int(item_year) if item_year else None
        except (TypeError, ValueError):
            year_int = None
        clips = pick_break(
            era=era,
            channel_id=channel_id,
            channel_name=channel_name,
            mode=mode,
            item_year=year_int,
        )
        if not clips:
            continue
        # Prefer clips not just played in the previous break when enough pool exists
        fresh = [c for c in clips if c["id"] not in recently_used]
        chosen = fresh if fresh else clips
        breaks[str(idx)] = chosen
        recently_used = {c["id"] for c in chosen}
        content_since_break = 0
    return breaks
