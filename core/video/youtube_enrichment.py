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

import queue
import threading
import time

from utils.logging_config import get_logger

logger = get_logger("video.youtube_enrichment")

# Throttle the per-video fallback so we don't burst YouTube into rate-limiting.
_FALLBACK_CAP = 60
_FALLBACK_DELAY = 0.4


class YoutubeDateEnricher:
    def __init__(self, db_factory=None):
        self._db_factory = db_factory or self._default_db
        self._q: "queue.Queue[str]" = queue.Queue()
        self._inflight = set()
        self._thread = None
        self._lock = threading.Lock()

    @staticmethod
    def _default_db():
        from database.video_database import VideoDatabase
        return VideoDatabase()

    def enqueue(self, channel_id):
        """Queue a followed channel for full date enrichment (deduped; starts the
        worker thread on first use)."""
        cid = str(channel_id or "").strip()
        if not cid:
            return
        with self._lock:
            if cid in self._inflight:
                return
            self._inflight.add(cid)
            self._q.put(cid)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="yt-date-enricher", daemon=True)
                self._thread.start()

    def _run(self):
        while True:
            try:
                cid = self._q.get(timeout=45)
            except queue.Empty:
                return   # idle → let the thread die; re-spawned on next enqueue
            try:
                self._enrich(cid)
            except Exception:
                logger.exception("YouTube date enrichment failed for %s", cid)
            finally:
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

        dates = {}
        try:
            dates = yt.proxy_channel_dates(cid) or {}
        except Exception:
            logger.info("proxy date fetch failed for %s", cid, exc_info=True)
        if dates:
            db.cache_video_dates([{"youtube_id": k, "published_at": v} for k, v in dates.items()])
            logger.info("YouTube dates: %d cached for %s (proxy)", len(dates), cid)

        # Fallback: per-video for the channel's wished videos still lacking a date.
        ids = db.wishlisted_video_ids_for_channel(cid)
        have = db.get_video_dates(ids)
        missing = [i for i in ids if i not in have and i not in dates]
        filled = 0
        for vid in missing[:_FALLBACK_CAP]:
            try:
                v = yt.video_detail(vid)
            except Exception:
                v = None
            if v and v.get("published_at"):
                db.cache_video_dates([{"youtube_id": vid, "published_at": v["published_at"]}])
                filled += 1
            time.sleep(_FALLBACK_DELAY)
        if filled:
            logger.info("YouTube dates: %d cached for %s (per-video fallback)", filled, cid)

        db.mark_channel_dates_enriched(cid, len(dates) + filled)


_enricher = None
_enricher_lock = threading.Lock()


def get_youtube_date_enricher():
    global _enricher
    if _enricher is None:
        with _enricher_lock:
            if _enricher is None:
                _enricher = YoutubeDateEnricher()
    return _enricher
