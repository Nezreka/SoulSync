"""Background YouTube date-enricher (video side, isolated).

Followed channels get their full upload-date catalog fetched in the background so
the channel page's year-seasons populate fully (the fast flat listing has no
dates). Cheap no-key bulk source first (Piped/Invidious proxy via
``proxy_channel_dates``); per-video yt-dlp only as a throttled fallback for the
channel's wished videos when every proxy is down. Everything is cached in
``youtube_video_dates`` so it's a one-time cost per channel and instant after.

A single daemon thread drains an enqueue() queue. Enqueue a channel when it's
followed (or its page opened while followed). Reads/writes only video_library.db.
"""

from __future__ import annotations

import os
import queue
import threading
import time

from utils.logging_config import get_logger

logger = get_logger("video_enrichment.youtube")   # same namespace as the other video workers

# Per-video fallback (used when the bulk proxy is down): dated in a small thread
# pool so a channel finishes in ~30s instead of minutes, without bursting too hard.
_FALLBACK_CAP = 60
_FALLBACK_WORKERS = 3


class YoutubeDateEnricher:
    def __init__(self, db_factory=None):
        self._db_factory = db_factory or self._default_db
        self._q: "queue.Queue[str]" = queue.Queue()
        self._inflight = set()
        self._titles = {}
        self._thread = None
        self._lock = threading.Lock()
        self._current = None          # channel being enriched right now (for the orb)
        self._paused = False
        self._channels_done = 0
        self._dates_total = 0

    @staticmethod
    def _default_db():
        from database.video_database import VideoDatabase
        return VideoDatabase()

    @staticmethod
    def _proxy_instances(db):
        """Parse the optional youtube_proxy_instances setting into [(kind, url), …].
        Empty (the default) → proxy skipped entirely. Format is comma/newline
        separated 'kind|url' (kind inferred from the url if omitted)."""
        try:
            raw = db.get_setting("youtube_proxy_instances") or ""
        except Exception:
            return []
        out = []
        for part in raw.replace("\n", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if "|" in part:
                kind, url = part.split("|", 1)
                kind, url = kind.strip().lower(), url.strip()
            else:
                url = part
                kind = "invidious" if "invidious" in url or "/api/v1" in url else "piped"
            if url.startswith("http"):
                out.append((kind, url.rstrip("/")))
        return out

    def enqueue(self, channel_id, title=None):
        """Queue a followed channel for full date enrichment (deduped; starts the
        worker thread on first use)."""
        cid = str(channel_id or "").strip()
        if not cid:
            return
        # Never spawn the background daemon (network + the default DB) under tests;
        # the enricher's logic is exercised directly via _enrich() with a tmp DB.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        with self._lock:
            if title:
                self._titles[cid] = title
            if cid in self._inflight:
                return
            self._inflight.add(cid)
            self._q.put(cid)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="yt-date-enricher", daemon=True)
                self._thread.start()

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stats(self):
        """Dashboard-orb telemetry — same shape the enrichment workers report."""
        queued = self._q.qsize()
        running = bool(self._current) and not self._paused
        cur = self._current
        return {
            "enabled": True,
            "idle": False,                       # this worker idles, it never "completes"
            "running": running,
            "paused": self._paused,
            "current_item": {"type": "channel", "name": cur} if cur else None,
            "progress": {"channels": {"matched": self._channels_done,
                                      "total": self._channels_done + queued + (1 if cur else 0)}},
            "queued": queued,
            "dates_cached": self._dates_total,
        }

    def _run(self):
        while True:
            if self._paused:
                time.sleep(0.5)
                continue
            try:
                cid = self._q.get(timeout=45)
            except queue.Empty:
                return   # idle → let the thread die; re-spawned on next enqueue
            try:
                self._enrich(cid)
            except Exception:
                logger.exception("YouTube date enrichment failed for %s", cid)
            finally:
                self._current = None
                with self._lock:
                    self._inflight.discard(cid)
                self._q.task_done()

    def _enrich(self, channel_id):
        """Fetch + cache a channel's upload dates. Proxy in bulk; per-video fallback."""
        from core.video import youtube as yt
        db = self._db_factory()
        cid = str(channel_id or "").strip()
        if not cid or db.channel_dates_enriched_recently(cid):
            return
        self._current = self._titles.get(cid) or cid
        logger.debug("Enriching dates for %s (%s)", self._current, cid)

        # PRIMARY: YouTube's own InnerTube browse API (no key/Java/proxy) — bulk
        # dates for the whole videos tab in a handful of requests. The path we
        # prefer; approximate dates, great for year-seasons.
        dates = {}
        try:
            dates = yt.innertube_channel_dates(cid) or {}
        except Exception:
            logger.debug("innertube date fetch failed for %s", cid, exc_info=True)
        logger.debug("innertube returned %d dates for %s", len(dates), cid)
        # SECONDARY (opt-in): a configured Piped/Invidious proxy — only if InnerTube
        # came up empty (the public instances are unreliable/API-disabled).
        if not dates:
            instances = self._proxy_instances(db)
            if instances:
                try:
                    dates = yt.proxy_channel_dates(cid, instances=instances) or {}
                except Exception:
                    logger.debug("proxy date fetch failed for %s", cid, exc_info=True)
        if dates:
            db.cache_video_dates([{"youtube_id": k, "published_at": v} for k, v in dates.items()])

        # FALLBACK (the basic method): exact dates per-video via yt-dlp, only for
        # the channel's videos still UNDATED — cheap when the bulk pass worked.
        ids = set(db.wishlisted_video_ids_for_channel(cid))
        if not dates:
            try:
                ch = yt.resolve_channel("https://www.youtube.com/channel/" + cid, limit=60)
                for v in (ch or {}).get("videos") or []:
                    if v.get("youtube_id"):
                        ids.add(v["youtube_id"])
            except Exception:
                logger.debug("flat resolve for date fallback failed for %s", cid, exc_info=True)
        ids = list(ids)
        have = db.get_video_dates(ids)
        missing = [i for i in ids if i not in have and i not in dates][:_FALLBACK_CAP]
        filled = 0
        if missing:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            cur = self._current

            def fetch_date(vid):
                try:
                    v = yt.video_detail(vid) or {}
                    d = v.get("published_at")
                    if d:
                        # Per-item line, matching the other workers' "Matched … -> …".
                        logger.info("Dated %s '%s' -> %s", cur, (v.get("title") or vid)[:70], d)
                    else:
                        logger.info("No date for %s '%s'", cur, (v.get("title") or vid)[:70])
                    return vid, d
                except Exception:
                    return vid, None

            with ThreadPoolExecutor(max_workers=_FALLBACK_WORKERS) as ex:
                for fut in as_completed([ex.submit(fetch_date, v) for v in missing]):
                    vid, d = fut.result()
                    if d:
                        db.cache_video_dates([{"youtube_id": vid, "published_at": d}])
                        filled += 1
        self._channels_done += 1
        self._dates_total += len(dates) + filled
        # Tag the source so legacy (pre-InnerTube) rows are recognisable and upgrade.
        method = "innertube" if dates else "fallback"
        db.mark_channel_dates_enriched(cid, len(dates) + filled, method=method)
        # Terse per-channel summary (like the worker's "Synced full episode list…").
        logger.info("Dated %d videos for %s (%d bulk + %d per-video)",
                    len(dates) + filled, self._current, len(dates), filled)


_enricher = None
_enricher_lock = threading.Lock()


def get_youtube_date_enricher():
    global _enricher
    if _enricher is None:
        with _enricher_lock:
            if _enricher is None:
                _enricher = YoutubeDateEnricher()
    return _enricher
