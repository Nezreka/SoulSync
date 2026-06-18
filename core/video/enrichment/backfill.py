"""Video BACKFILL workers — enrich an already-identified item BY id.

The matcher workers (worker.py) find an external id for a title. These workers
take items we can already address (a tmdb/imdb/tvdb id, or a YouTube video id)
and fetch SUPPLEMENTARY data for them: artwork (fanart.tv), subtitle
availability (OpenSubtitles), and the no-key YouTube extras — Return YouTube
Dislike (like/dislike estimates) and SponsorBlock (crowd segments).

Each worker presents the EXACT same lifecycle + get_stats() shape as
VideoEnrichmentWorker, so the engine registry, the /api/video/enrichment routes,
and the Manage-Workers modal (cards / animations / pause-resume) drive them
identically — a new worker is just another entry in engine.workers.

Isolated: imports only video.db helpers + requests; no music code.
"""

from __future__ import annotations

import json
import threading
import time

from utils.logging_config import get_logger

logger = get_logger("video_enrichment.backfill")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ── tiny HTTP helper with the status semantics the workers rely on ────────────
class _RateLimited(Exception):
    def __init__(self, retry_after=60):
        self.retry_after = retry_after


class _Unauthorized(Exception):
    pass


def _http_get_json(url, params=None, headers=None, timeout=12):
    """GET → parsed JSON (list or dict), or None for a 404 / unparseable body.
    Raises _Unauthorized (401/403), _RateLimited (429), or the underlying error
    so the worker can record 'error' vs back off vs mark 'not_found'."""
    import requests
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    r = requests.get(url, params=params, headers=h, timeout=timeout)
    if r.status_code == 404:
        return None
    if r.status_code in (401, 403):
        raise _Unauthorized()
    if r.status_code == 429:
        try:
            ra = int(r.headers.get("Retry-After") or 60)
        except (TypeError, ValueError):
            ra = 60
        raise _RateLimited(ra)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return None


# ── base worker (lifecycle + loop + status; mirrors VideoEnrichmentWorker) ────
class VideoBackfillWorker:
    is_ratings = False

    def __init__(self, db, service, display_name, interval=1.0):
        self.db = db
        self.service = service
        self.display_name = display_name
        self.interval = interval
        self.running = False
        self.paused = False
        self.should_stop = False
        self._thread = None
        self._stop = threading.Event()
        self.current_item = None
        self.stats = {"matched": 0, "not_found": 0, "errors": 0}
        self.note = None
        self._cooldown_until = 0.0

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(self):
        if self.running:
            return
        self.should_stop = False
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.should_stop = True
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self.running = False

    def pause(self, persist=True):
        self.paused = True
        if persist:
            self._persist_paused()

    def resume(self, persist=True):
        self.paused = False
        if persist:
            self._persist_paused()

    def _persist_paused(self):
        try:
            self.db.set_setting(self.service + "_paused", "1" if self.paused else "0")
        except Exception:
            logger.exception("video backfill: could not persist pause for %s", self.service)

    def restore_paused(self):
        try:
            self.paused = str(self.db.get_setting(self.service + "_paused") or "") == "1"
        except Exception:
            logger.exception("video backfill: could not restore pause for %s", self.service)

    @property
    def enabled(self):
        try:
            return self._enabled()
        except Exception:
            return False

    # ── subclass hooks ────────────────────────────────────────────────────────
    def _enabled(self) -> bool:
        return True

    def test(self):
        return (True, self.display_name + " OK")

    def next_item(self):
        raise NotImplementedError

    def fetch(self, item):
        """Return data (truthy) on a hit, falsy for a genuine 'no data', or raise
        on a call failure (network/rate-limit/auth)."""
        raise NotImplementedError

    def record_ok(self, item, data):
        raise NotImplementedError

    def record_empty(self, item):
        raise NotImplementedError

    def record_error(self, item):
        raise NotImplementedError

    def breakdown(self) -> dict:
        raise NotImplementedError

    # ── loop ──────────────────────────────────────────────────────────────────
    def _run(self):
        while not self.should_stop:
            if self.paused or not self.enabled:
                self._stop.wait(1.0)
                continue
            if self._cooldown_until > time.monotonic():
                self.current_item = None
                self._stop.wait(15.0)
                continue
            try:
                did = self.process_one()
            except Exception:
                logger.exception("video backfill %s loop error", self.service)
                self.stats["errors"] += 1
                self._stop.wait(5.0)
                continue
            if did:
                self._stop.wait(self.interval)
            else:
                self.current_item = None
                self._stop.wait(10.0)

    def process_one(self) -> bool:
        item = self.next_item()
        if not item:
            return False
        self.current_item = {"type": item.get("kind"), "name": item.get("name")}
        try:
            data = self.fetch(item)
        except _RateLimited as e:
            self._cooldown_until = time.monotonic() + max(15, e.retry_after)
            self.note = self.display_name + " rate-limited — backing off"
            logger.warning("%s rate-limited; idling %ss", self.display_name, e.retry_after)
            return False
        except _Unauthorized:
            self.note = self.display_name + " rejected the API key"
            self.pause(persist=False)        # transient: fixing the key rebuilds the engine
            logger.warning("%s rejected the API key — pausing until fixed", self.display_name)
            return False
        except Exception:
            logger.exception("video backfill %s fetch failed for %s", self.service, item.get("name"))
            self.stats["errors"] += 1
            self.record_error(item)
            return True
        self.note = None
        if data:
            self.record_ok(item, data)
            self.stats["matched"] += 1
            logger.info("Enriched %s '%s' via %s", item.get("kind"), item.get("name"), self.display_name)
        else:
            self.record_empty(item)
            self.stats["not_found"] += 1
        return True

    # ── status (same shape the music/video enrichment API returns) ────────────
    def get_stats(self) -> dict:
        breakdown = self.breakdown()
        pending = sum(b["pending"] + b.get("errors", 0)
                      for b in breakdown.values() if not b.get("coverage_only"))
        cooling = self._cooldown_until > time.monotonic()
        running = self.running and not self.paused and self.enabled and not cooling
        idle = running and pending == 0 and self.current_item is None
        progress = {}
        for kind, b in breakdown.items():
            total = b["matched"] + b["not_found"] + b.get("errors", 0) + b["pending"]
            done = b["matched"] + b["not_found"]
            progress[kind] = {"matched": b["matched"], "total": total,
                              "percent": round(done / total * 100) if total else 0}
        return {
            "enabled": self.enabled,
            "running": running,
            "paused": self.paused or cooling,
            "idle": idle,
            "current_item": self.current_item,
            "note": self.note,
            "cooldown": cooling,
            "stats": {**self.stats, "pending": pending},
            "progress": progress,
            "breakdown": breakdown,
        }


# ── Return YouTube Dislike (no key) ───────────────────────────────────────────
class RydWorker(VideoBackfillWorker):
    URL = "https://returnyoutubedislikeapi.com/votes"

    def __init__(self, db):
        super().__init__(db, "ryd", "Return YouTube Dislike", interval=0.6)

    def _enabled(self):
        return str(self.db.get_setting("ryd_enabled") or "1") != "0"

    def test(self):
        try:
            j = _http_get_json(self.URL, {"videoId": "dQw4w9WgXcQ"})
            return (j is not None, "Return YouTube Dislike reachable" if j else "No response")
        except Exception as e:
            return (False, str(e))

    def next_item(self):
        return self.db.youtube_enrich_next("ryd_status")

    def fetch(self, item):
        j = _http_get_json(self.URL, {"videoId": item["youtube_id"]})
        if not isinstance(j, dict) or j.get("dislikes") is None:
            return None
        return {"likes": j.get("likes"), "dislikes": j.get("dislikes")}

    def record_ok(self, item, data):
        self.db.apply_youtube_votes(item["youtube_id"], data.get("likes"), data.get("dislikes"), "ok")

    def record_empty(self, item):
        self.db.apply_youtube_votes(item["youtube_id"], None, None, "not_found")

    def record_error(self, item):
        self.db.apply_youtube_votes(item["youtube_id"], None, None, "error")

    def breakdown(self):
        return self.db.youtube_enrich_breakdown("ryd_status")


# ── SponsorBlock (no key) ─────────────────────────────────────────────────────
_SB_CATS = ["sponsor", "selfpromo", "interaction", "intro", "outro",
            "preview", "music_offtopic", "filler", "poi_highlight"]


class SponsorBlockWorker(VideoBackfillWorker):
    URL = "https://sponsor.ajay.app/api/skipSegments"

    def __init__(self, db):
        super().__init__(db, "sponsorblock", "SponsorBlock", interval=0.6)

    def _enabled(self):
        return str(self.db.get_setting("sponsorblock_enabled") or "1") != "0"

    def test(self):
        try:
            _http_get_json(self.URL, {"videoID": "dQw4w9WgXcQ", "categories": json.dumps(_SB_CATS)})
            return (True, "SponsorBlock reachable")
        except Exception as e:
            return (False, str(e))

    def next_item(self):
        return self.db.youtube_enrich_next("sb_status")

    def fetch(self, item):
        data = _http_get_json(self.URL, {"videoID": item["youtube_id"],
                                         "categories": json.dumps(_SB_CATS)})
        if not isinstance(data, list) or not data:
            return None
        segs = []
        for s in data:
            seg = s.get("segment") or []
            if len(seg) != 2 or not s.get("UUID") or not s.get("category"):
                continue
            segs.append({"category": s.get("category"), "start_sec": seg[0], "end_sec": seg[1],
                         "votes": s.get("votes"), "uuid": s.get("UUID")})
        return segs or None

    def record_ok(self, item, data):
        self.db.apply_youtube_segments(item["youtube_id"], data, "ok")

    def record_empty(self, item):
        self.db.apply_youtube_segments(item["youtube_id"], None, "not_found")

    def record_error(self, item):
        self.db.apply_youtube_segments(item["youtube_id"], None, "error")

    def breakdown(self):
        return self.db.youtube_enrich_breakdown("sb_status")


# ── fanart.tv (free key) ──────────────────────────────────────────────────────
def _fa_first(j, *keys):
    """First artwork URL from the first present key, preferring English / textless
    (lang '') and most-liked."""
    for k in keys:
        arr = j.get(k) or []
        if not isinstance(arr, list) or not arr:
            continue
        best = sorted(arr, key=lambda a: (a.get("lang") not in ("en", ""),
                                          -int(a.get("likes") or 0)))
        url = best[0].get("url") if best else None
        if url:
            return url
    return None


class FanartWorker(VideoBackfillWorker):
    BASE = "https://webservice.fanart.tv/v3"

    def __init__(self, db):
        super().__init__(db, "fanart", "fanart.tv", interval=1.0)

    def _key(self):
        return (self.db.get_setting("fanart_api_key") or "").strip()

    def _enabled(self):
        return bool(self._key())

    def test(self):
        key = self._key()
        if not key:
            return (False, "No fanart.tv API key")
        try:
            j = _http_get_json(self.BASE + "/movies/550", {"api_key": key})
            return (j is not None, "fanart.tv key OK" if j else "No artwork returned")
        except _Unauthorized:
            return (False, "fanart.tv rejected the API key")
        except Exception as e:
            return (False, str(e))

    def next_item(self):
        return self.db.backfill_next("fanart")

    def fetch(self, item):
        key = self._key()
        if not key:
            return None
        if item["kind"] == "movie":
            ident = item.get("tmdb_id") or item.get("imdb_id")
            if not ident:
                return None
            j = _http_get_json(self.BASE + "/movies/" + str(ident), {"api_key": key})
            if not isinstance(j, dict):
                return None
            out = {"logo_url": _fa_first(j, "hdmovielogo", "clearlogo"),
                   "clearart_url": _fa_first(j, "hdmovieclearart", "movieart"),
                   "banner_url": _fa_first(j, "moviebanner"),
                   "backdrop_url": _fa_first(j, "moviebackground"),
                   "poster_url": _fa_first(j, "movieposter")}
        else:
            ident = item.get("tvdb_id")
            if not ident:
                return None
            j = _http_get_json(self.BASE + "/tv/" + str(ident), {"api_key": key})
            if not isinstance(j, dict):
                return None
            out = {"logo_url": _fa_first(j, "hdtvlogo", "clearlogo"),
                   "clearart_url": _fa_first(j, "hdclearart", "clearart"),
                   "banner_url": _fa_first(j, "tvbanner"),
                   "backdrop_url": _fa_first(j, "showbackground"),
                   "poster_url": _fa_first(j, "tvposter")}
        out = {k: v for k, v in out.items() if v}
        return out or None

    def record_ok(self, item, data):
        self.db.backfill_mark("fanart", item["kind"], item["id"], "ok", columns=data)

    def record_empty(self, item):
        self.db.backfill_mark("fanart", item["kind"], item["id"], "not_found")

    def record_error(self, item):
        self.db.backfill_mark("fanart", item["kind"], item["id"], "error")

    def breakdown(self):
        return self.db.backfill_breakdown("fanart")


# ── OpenSubtitles (free key) — subtitle-language availability ─────────────────
class OpenSubtitlesWorker(VideoBackfillWorker):
    BASE = "https://api.opensubtitles.com/api/v1"

    def __init__(self, db):
        super().__init__(db, "opensubtitles", "OpenSubtitles", interval=1.5)

    def _key(self):
        return (self.db.get_setting("opensubtitles_api_key") or "").strip()

    def _enabled(self):
        return bool(self._key())

    def _headers(self):
        return {"Api-Key": self._key(), "Accept": "application/json",
                "User-Agent": "SoulSync v1.0"}

    def test(self):
        if not self._key():
            return (False, "No OpenSubtitles API key")
        try:
            j = _http_get_json(self.BASE + "/subtitles", {"tmdb_id": 550, "languages": "en"},
                               headers=self._headers())
            return (j is not None, "OpenSubtitles key OK" if j else "No response")
        except _Unauthorized:
            return (False, "OpenSubtitles rejected the API key")
        except Exception as e:
            return (False, str(e))

    def next_item(self):
        return self.db.backfill_next("opensubtitles")

    def fetch(self, item):
        if not self._key():
            return None
        params = {}
        imdb = str(item.get("imdb_id") or "").lower()
        if imdb.startswith("tt"):
            imdb = imdb[2:]
        if imdb:
            params["imdb_id"] = imdb
        elif item.get("tmdb_id"):
            params["tmdb_id"] = item["tmdb_id"]
        else:
            return None
        j = _http_get_json(self.BASE + "/subtitles", params, headers=self._headers())
        if not isinstance(j, dict):
            return None
        langs = set()
        for row in (j.get("data") or []):
            lng = ((row.get("attributes") or {}).get("language") or "").lower()
            if lng:
                langs.add(lng)
        if not langs:
            return None
        return {"subtitle_langs": json.dumps(sorted(langs))}

    def record_ok(self, item, data):
        self.db.backfill_mark("opensubtitles", item["kind"], item["id"], "ok", columns=data)

    def record_empty(self, item):
        self.db.backfill_mark("opensubtitles", item["kind"], item["id"], "not_found")

    def record_error(self, item):
        self.db.backfill_mark("opensubtitles", item["kind"], item["id"], "error")

    def breakdown(self):
        return self.db.backfill_breakdown("opensubtitles")


def build_backfill_workers(db) -> dict:
    """All backfill workers, keyed by service id, for the engine registry."""
    return {w.service: w for w in (
        RydWorker(db), SponsorBlockWorker(db), FanartWorker(db), OpenSubtitlesWorker(db),
    )}
