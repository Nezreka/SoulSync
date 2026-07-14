"""Video enrichment worker — one per source (TMDB, TVDB).

Mirrors the music worker: a daemon loop that pulls the next item needing
enrichment from video.db, asks its CLIENT to match it, and records the result.
The client is injected (a thin TMDB/TVDB adapter), so the worker's loop/queue/
status logic is fully testable with a fake client. Isolated: imports only
video.db helpers; no music code.
"""

from __future__ import annotations

import threading
import time

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
        self.note = None                 # a human reason when auto-paused (e.g. bad key)
        self._rating_errors = 0          # consecutive ratings failures (transient backoff)
        self._cooldown_until = 0.0       # monotonic time to idle until (daily-limit backoff)

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
            # Daily-limit cooldown: idle until the quota resets, then auto-resume
            # (re-checks periodically so we pick back up shortly after midnight UTC).
            if self._cooldown_until > time.monotonic():
                self.current_item = None
                self._stop.wait(15.0)
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
            logger.debug("enrichment priority lookup failed", exc_info=True)
        item = self.db.enrichment_next(self.service, self.retry_days, priority=priority)
        if not item:
            # No pending matches → use idle time for the full episode-list sync
            # (which also fills details for shows it touches), then the details
            # backfill for everything already episode-synced but missing `status`.
            return self._sync_episodes_once() or self._detail_backfill_one()
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
        from .clients import OMDbAuthError
        item = self.db.ratings_next()
        if not item:
            self._rating_errors = 0
            return False
        self.current_item = {"type": item["kind"], "name": item["title"]}
        try:
            r = self.client.ratings(item["imdb_id"])
        except OMDbAuthError as e:
            msg = str(e)
            if "limit" in msg.lower():
                # Free-tier daily quota (1,000/req) — resets at midnight UTC. Cool
                # down and AUTO-RESUME (so a big library just spreads across days)
                # rather than pausing for good. Item not marked synced → retried.
                if self._cooldown_until <= time.monotonic():
                    logger.warning("OMDb daily request limit reached — idling ratings ~30 min; "
                                   "auto-resumes after the daily reset")
                self._cooldown_until = time.monotonic() + 1800
                self.note = "OMDb daily limit reached — resumes after reset"
                return False
            # Bad/expired/unactivated key affects EVERY item — pause instead of
            # churning the whole library + flooding the log. Transient pause (not
            # persisted) so fixing the key (which rebuilds the engine) resumes
            # automatically. The item is NOT marked synced, so it retries once
            # the key works.
            if not self.paused:
                logger.warning("OMDb rejected the API key — pausing ratings until it's fixed (%s)", msg)
            self.note = "OMDb API key rejected"
            self.pause(persist=False)
            return False
        except Exception as e:
            # Transient (network / rate-limit / 5xx) — don't burn the item to
            # 'synced'; back off, and pause after a few in a row so we don't spin.
            self.stats["errors"] += 1
            self._rating_errors = getattr(self, "_rating_errors", 0) + 1
            logger.warning("OMDb ratings fetch failed for '%s': %s", item["title"], e)
            if self._rating_errors >= 3:
                logger.warning("OMDb: pausing ratings after repeated errors")
                self.note = "OMDb temporarily unavailable"
                self.pause(persist=False)
                self._rating_errors = 0
            return False
        self._rating_errors = 0
        self._cooldown_until = 0.0
        self.note = None
        if r:
            self.db.apply_ratings(item["kind"], item["id"], r)       # marks synced
            self.stats["matched"] += 1
            logger.info("Rated %s '%s' -> IMDb %s", item["kind"], item["title"], item["imdb_id"])
        else:
            self.db.mark_ratings_synced(item["kind"], item["id"])    # genuine no-data
            self.stats["not_found"] += 1
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

    def _detail_backfill_one(self) -> bool:
        """Background TMDB details backfill: re-fetch ONE already-matched show/movie
        that's missing details-only fields (status, network, tagline, rating…) and
        gap-fill them. The matcher skips server-pre-matched items, so without this
        their `status` stays blank — and the watchlist's airing-default can't see
        them. Returns True if it did work (so the loop rate-limits).

        TMDB-ONLY: `status` (+ network/tagline/…) come from TMDB and the queue is
        keyed on tmdb_id. Running it on the TVDB worker would feed a TMDB id to
        TVDB (→ 404 on every show) and double-process the queue."""
        if self.service != "tmdb" or not hasattr(self.client, "match"):
            return False
        item = self.db.detail_backfill_next("show") or self.db.detail_backfill_next("movie")
        if not item:
            return False
        self.current_item = {"type": item["kind"], "name": item["title"]}
        try:
            result = self.client.match(item["kind"], item["title"], item.get("year"),
                                       known_id=item.get("tmdb_id"))
            if result and result.get("id"):
                # Gap-fill only (never clobbers server data); fills status et al.
                self.db.enrichment_apply("tmdb", item["kind"], item["id"], matched=True,
                                         external_id=result["id"], metadata=result.get("metadata"))
                logger.info("Backfilled details for %s '%s'", item["kind"], item["title"])
            self.db.mark_details_synced(item["kind"], item["id"])   # attempted once → don't re-pick
        except Exception:
            # Transient call failure — leave details_synced=0 so it retries later.
            logger.exception("detail backfill failed for %s '%s'", item["kind"], item["title"])
            self.stats["errors"] += 1
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
                logger.debug("episode_sync_pending_count failed", exc_info=True)
        # NB: the TMDB details backfill (status/network/…) is a background gap-fill on
        # ALREADY-matched items — like episode coverage, it doesn't block "Complete".
        # It still runs in the idle loop; it's just not counted as blocking pending.
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
            "needs_key": True,   # matchers (TMDB/TVDB/OMDb) always require an API key
            "running": running,
            "paused": self.paused or cooling,    # cooldown reads as paused in the UI
            "idle": idle,
            "current_item": self.current_item,
            "note": self.note,
            "cooldown": cooling,
            "stats": {**self.stats, "pending": pending},
            "progress": progress,
            "breakdown": breakdown,
        }
