"""Metadata Gaps job — movies enrichment never finished dressing.

Scan: owned movies that are unmatched (TMDB never identified them) or missing
overview / genres / poster / backdrop. Fields the user deliberately blanked
and LOCKED (Manage sidebar) are respected — a locked empty tagline is a
choice, not a gap. One finding per movie listing exactly what's missing.

Fix (approve): re-queue the TMDB match when unmatched, then run the same
on-view art/credits refresh the detail page uses — gap-fill only, it never
clobbers what's already there.
"""

from __future__ import annotations

import hashlib
import json

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video.repair.metadata_gaps")

# gap key -> (human label, movies column the Manage-sidebar lock protects)
_GAPS = (("unmatched", "not TMDB-matched", None),
         ("overview", "no summary", "overview"),
         ("genres", "no genres", "genres"),
         ("poster", "no poster", None),
         ("backdrop", "no backdrop", None))


def _sig(gaps) -> str:
    return hashlib.sha1(json.dumps(sorted(gaps)).encode("utf-8")).hexdigest()[:10]


@register_job
class MetadataGapsJob(VideoRepairJob):
    job_id = "metadata_gaps"
    display_name = "Metadata Gaps"
    description = "Finds movies missing summary, genres, poster, backdrop, or a TMDB match."
    help_text = ("Sweeps owned movies for holes enrichment never filled — an "
                 "unmatched title, a missing summary, empty genres, no poster or "
                 "backdrop. Fields you blanked and locked in the Manage sidebar "
                 "are respected. Approving re-queues the TMDB match (when "
                 "unmatched) and re-runs the art/credits refresh — gap-fill "
                 "only, nothing existing is overwritten.")
    icon = "🩹"
    default_enabled = False
    default_interval_hours = 72
    default_settings = {}
    setting_options = {}
    auto_fix = False
    finding_types = ("metadata_gap",)

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        rows = context.db.repair_movie_metadata_gaps()
        context.report(total=len(rows), phase="sweeping metadata")
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=r["title"])
            locked = set()
            try:
                locked = set(json.loads(r.get("locked_fields") or "[]"))
            except (ValueError, TypeError):
                pass
            gaps = []
            unmatched = (r.get("tmdb_match_status") or "") != "matched"
            if unmatched:
                gaps.append("unmatched")
            for key, _label, lock_field in _GAPS[1:]:
                flagged = bool(r.get("no_" + ("genres" if key == "genres" else key)))
                if flagged and (lock_field is None or lock_field not in locked):
                    gaps.append(key)
            if not gaps:
                continue
            labels = {k: lbl for k, lbl, _f in _GAPS}
            entity_id = f"{r['movie_id']}:{_sig(gaps)}"
            context.db.repair_dismiss_stale(self.job_id, "metadata_gap",
                                            f"{r['movie_id']}:", entity_id)
            context.create_finding(
                finding_type="metadata_gap",
                severity="warning" if unmatched else "info",
                entity_type="movie", entity_id=entity_id,
                title=f"{r['title']}" + (f" ({r['year']})" if r.get("year") else "") +
                      f" — {len(gaps)} gap{'s' if len(gaps) != 1 else ''}",
                description=" · ".join(labels[g] for g in gaps),
                details={"movie_id": r["movie_id"], "tmdb_id": r.get("tmdb_id"),
                         "title": r["title"], "year": r.get("year"), "gaps": gaps})
        return result

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        d = finding.get("details") or {}
        movie_id = d.get("movie_id")
        if movie_id is None:
            return {"success": False, "error": "finding has no movie id"}
        if "unmatched" in (d.get("gaps") or []):
            context.db.enrichment_retry("tmdb", "movie", scope="item", item_id=movie_id)
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            get_video_enrichment_engine().refresh_movie_art(movie_id)
        except Exception:   # noqa: BLE001 - the retry queue alone is still progress
            logger.debug("refresh_movie_art failed for %s", movie_id, exc_info=True)
        return {"success": True, "action": "refreshed",
                "message": f"Re-queued enrichment for {d.get('title') or 'the movie'} — "
                           "gaps fill as the workers get to it"}
