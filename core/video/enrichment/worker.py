"""Video enrichment worker — one per source (TMDB, TVDB).

Mirrors the music worker: a daemon loop that pulls the next item needing
enrichment from video.db, asks its CLIENT to match it, and records the result.
The client is injected (a thin TMDB/TVDB adapter), so the worker's loop/queue/
status logic is fully testable with a fake client. Isolated: imports only
video.db helpers; no music code.
"""

from __future__ import annotations

import threading

from utils.logging_config import get_logger

logger = get_logger("video_enrichment.worker")


class VideoEnrichmentWorker:
    def __init__(self, db, service, client, display_name=None, interval=2.0, retry_days=30):
        self.db = db
        self.service = service
        self.client = client
        self.display_name = display_name or service.upper()
        self.interval = interval
        self.retry_days = retry_days

        self.running = False
        self.paused = False
        self.should_stop = False
        self._thread = None
        self._stop = threading.Event()
        self.current_item = None
        self.stats = {"matched": 0, "not_found": 0, "errors": 0}

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
        # Survives restart, like music's <service>_enrichment_paused config flag.
        try:
            self.db.set_setting(self.service + "_paused", "1" if self.paused else "0")
        except Exception:
            logger.exception("video enrichment: could not persist pause for %s", self.service)

    def restore_paused(self):
        try:
            self.paused = str(self.db.get_setting(self.service + "_paused") or "") == "1"
        except Exception:
            logger.exception("video enrichment: could not restore pause for %s", self.service)

    @property
    def enabled(self):
        return bool(getattr(self.client, "enabled", False))

    # ── loop ──────────────────────────────────────────────────────────────────
    def _run(self):
        while not self.should_stop:
            if self.paused or not self.enabled:
                self._stop.wait(1.0)
                continue
            try:
                did = self.process_one()
            except Exception:
                logger.exception("video enrichment %s loop error", self.service)
                self.stats["errors"] += 1
                self._stop.wait(5.0)
                continue
            if did:
                self._stop.wait(self.interval)       # rate-limit between items
            else:
                self.current_item = None
                self._stop.wait(10.0)                # nothing to do — back off

    def process_one(self) -> bool:
        """Process a single item. Returns True if one was processed."""
        item = self.db.enrichment_next(self.service, self.retry_days)
        if not item:
            return False
        self.current_item = {"type": item["kind"], "name": item["title"]}
        try:
            result = self.client.match(item["kind"], item["title"], item.get("year"))
        except Exception:
            logger.exception("video enrichment %s match failed for %s", self.service, item["title"])
            self.stats["errors"] += 1
            self.db.enrichment_apply(self.service, item["kind"], item["id"], matched=False)
            return True
        if result and result.get("id"):
            self.db.enrichment_apply(self.service, item["kind"], item["id"], matched=True,
                                     external_id=result["id"], metadata=result.get("metadata"))
            self.stats["matched"] += 1
        else:
            self.db.enrichment_apply(self.service, item["kind"], item["id"], matched=False)
            self.stats["not_found"] += 1
        return True

    # ── status (same shape the music enrichment API returns) ──────────────────
    def get_stats(self) -> dict:
        breakdown = self.db.enrichment_breakdown(self.service)
        pending = sum(b["pending"] for b in breakdown.values())
        running = self.running and not self.paused and self.enabled
        idle = running and pending == 0 and self.current_item is None
        progress = {}
        for kind, b in breakdown.items():
            total = b["matched"] + b["not_found"] + b["pending"]
            done = b["matched"] + b["not_found"]
            progress[kind] = {"matched": b["matched"], "total": total,
                              "percent": round(done / total * 100) if total else 0}
        return {
            "enabled": self.enabled,
            "running": running,
            "paused": self.paused,
            "idle": idle,
            "current_item": self.current_item,
            "stats": {**self.stats, "pending": pending},
            "progress": progress,
            "breakdown": breakdown,
        }
