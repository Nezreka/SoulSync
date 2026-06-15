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

        # OMDb is a ratings filler, not a matcher — it fetches scores by imdb_id
        # instead of running a match queue.
        self.is_ratings = hasattr(client, "ratings") and not hasattr(client, "match")

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
        if self.is_ratings:
            return self._process_ratings_one()
        priority = None
        try:
            priority = self.db.get_setting("enrichment_priority") or None
        except Exception:
            pass
        item = self.db.enrichment_next(self.service, self.retry_days, priority=priority)
        if not item:
            return self._sync_episodes_once()
        self.current_item = {"type": item["kind"], "name": item["title"]}
        try:
            # Prefer the provider id the server already gave us (enrich BY ID, no
            # re-search); the client falls back to a title/year search if it's None.
            result = self.client.match(item["kind"], item["title"], item.get("year"),
                                       known_id=item.get("known_id"))
        except Exception:
            logger.exception("video enrichment %s match failed for %s", self.service, item["title"])
            self.stats["errors"] += 1
            # The CALL failed (network/rate-limit/timeout) — record 'error', NOT
            # 'not_found', so a transient blip isn't permanently logged as "no
            # match". enrichment_next retries 'error' items after retry_days.
            self.db.enrichment_apply(self.service, item["kind"], item["id"], matched=False, error=True)
            return True
        if result and result.get("id"):
            self.db.enrichment_apply(self.service, item["kind"], item["id"], matched=True,
                                     external_id=result["id"], metadata=result.get("metadata"))
            self.stats["matched"] += 1
            # Visible progress in app.log, mirroring the music workers' style.
            logger.info("Matched %s '%s' -> %s ID: %s%s", item["kind"], item["title"],
                        self.display_name, result["id"],
                        " (by server id)" if item.get("known_id") else "")
            # Cascade: a matched show backfills its episodes' art/overview/rating
            # from the same provider (one call per season), so episodes ride along
            # with their show instead of being a separate (huge) queue.
            if item["kind"] == "show" and hasattr(self.client, "season_episodes"):
                nums = [s["season_number"] for s in (result.get("metadata") or {}).get("seasons") or []]
                self._cascade_episodes(item["id"], result["id"], nums)
        else:
            self.db.enrichment_apply(self.service, item["kind"], item["id"], matched=False)
            self.stats["not_found"] += 1
            logger.info("No %s match for %s '%s'", self.display_name, item["kind"], item["title"])
        return True

    def _process_ratings_one(self) -> bool:
        """OMDb worker: fetch IMDb/RT/Metacritic for the next library item that has
        an imdb_id but no ratings yet."""
        item = self.db.ratings_next()
        if not item:
            return False
        self.current_item = {"type": item["kind"], "name": item["title"]}
        try:
            r = self.client.ratings(item["imdb_id"])
            if r:
                self.db.apply_ratings(item["kind"], item["id"], r)   # marks synced
                self.stats["matched"] += 1
                logger.info("Rated %s '%s' -> IMDb %s", item["kind"], item["title"], item["imdb_id"])
            else:
                self.db.mark_ratings_synced(item["kind"], item["id"])
                self.stats["not_found"] += 1
        except Exception:
            logger.exception("OMDb ratings fetch failed for '%s'", item["title"])
            self.stats["errors"] += 1
            self.db.mark_ratings_synced(item["kind"], item["id"])    # move on (no loop)
        return True

    def _sync_episodes_once(self) -> bool:
        """Background episode-sync: pull the FULL season/episode list for one
        already-matched show that hasn't been synced, so library cards show real
        owned/total. TMDB-only (it owns season_episodes). Returns True if it did
        work (so the loop rate-limits between shows)."""
        if not hasattr(self.client, "season_episodes"):
            return False
        show = self.db.episode_sync_next()
        if not show:
            return False
        self.current_item = {"type": "episodes", "name": show["title"]}
        try:
            result = self.client.match("show", show["title"], show.get("year"),
                                       known_id=show.get("tmdb_id"))
            if result and result.get("id"):
                self.db.enrichment_apply("tmdb", "show", show["id"], matched=True,
                                         external_id=result["id"], metadata=result.get("metadata"))
                nums = [s["season_number"] for s in (result.get("metadata") or {}).get("seasons") or []]
                self._cascade_episodes(show["id"], result["id"], nums)   # marks synced
                logger.info("Synced full episode list for show '%s'", show["title"])
            else:
                self.db.mark_episodes_synced(show["id"])     # no match → don't re-pick
        except Exception:
            logger.exception("episode sync failed for show '%s'", show["title"])
            self.db.mark_episodes_synced(show["id"])         # move on (never loop on one show)
        return True

    def _cascade_episodes(self, show_id, tv_id, season_numbers=None) -> None:
        """Backfill a show's FULL episode list from the provider (one call per
        season) — owned + missing. Best-effort: a season failure never aborts the
        show's enrichment. Falls back to the known seasons if none are passed."""
        seasons = season_numbers
        if not seasons:
            try:
                seasons = self.db.show_season_numbers(show_id)
            except Exception:
                logger.exception("episode backfill: season list failed for show %s", show_id)
                return
        for snum in seasons:
            try:
                data = self.client.season_episodes(tv_id, snum)
                if data and data.get("episodes"):
                    self.db.backfill_episodes(show_id, snum, data["episodes"],
                                              data.get("overview"), data.get("poster_url"))
            except Exception:
                logger.exception("episode backfill failed: show %s season %s", show_id, snum)
        try:
            self.db.mark_episodes_synced(show_id)
        except Exception:
            logger.exception("episode backfill: could not mark synced for show %s", show_id)

    # ── status (same shape the music enrichment API returns) ──────────────────
    def get_stats(self) -> dict:
        breakdown = self.db.enrichment_breakdown(self.service)
        # Errored items are outstanding (retried later), so they count as pending
        # work — the worker isn't "Complete" while any remain. Episode art is a
        # coverage-only cascade (no queue), so it's excluded from idle/pending.
        pending = sum(b["pending"] + b.get("errors", 0)
                      for b in breakdown.values() if not b.get("coverage_only"))
        # Shows still needing their full episode list pulled count as outstanding
        # work for the TMDB worker (so it isn't "Complete" while syncing).
        if hasattr(self.client, "season_episodes"):
            try:
                pending += self.db.episode_sync_pending_count()
            except Exception:
                pass
        running = self.running and not self.paused and self.enabled
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
            "paused": self.paused,
            "idle": idle,
            "current_item": self.current_item,
            "stats": {**self.stats, "pending": pending},
            "progress": progress,
            "breakdown": breakdown,
        }
