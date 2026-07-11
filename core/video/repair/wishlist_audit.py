"""Wishlist Audit job — rows the acquisition pipeline will never act on.

Scan: wishlist entries whose target is ALREADY OWNED **and done** — the owned
copy meets the quality cutoff (nothing left to chase), or its quality is
unreadable (the upgrader can't judge it, so the drain skips it forever).
Owned-but-BELOW-cutoff rows are deliberately NOT flagged: upgrade-until keeps
those alive so the drain can chase a better copy.

Fix (approve): remove the row. Nothing else — the owned copy is untouched.
"""

from __future__ import annotations

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob


@register_job
class WishlistAuditJob(VideoRepairJob):
    job_id = "wishlist_audit"
    display_name = "Wishlist Audit"
    description = "Finds wishlist entries whose owned copy is already done — dead weight."
    help_text = ("A wishlist row for something you own at (or above) your quality "
                 "cutoff has nothing left to do — the upgrader is finished with "
                 "it. Rows whose owned copy sits BELOW the cutoff are left alone: "
                 "those are live upgrade watches. Owned copies whose quality "
                 "can't be read are flagged too (the upgrader can't judge them). "
                 "Approving removes the wishlist row only — files are never "
                 "touched.")
    icon = "🧹"
    default_enabled = False
    default_interval_hours = 1     # cheap DB sweep — keeps pace with the download engine
    default_settings = {}
    setting_options = {}
    auto_fix = False
    finding_types = ("stale_wishlist",)

    def scan(self, context: JobContext) -> JobResult:
        from core.video.quality_eval import resolution_rank
        from core.video.quality_profile import load as load_profile
        result = JobResult()
        cutoff_rank = resolution_rank(
            (load_profile(context.db) or {}).get("cutoff_resolution"))
        rows = context.db.repair_stale_wishlist()
        context.report(total=len(rows), phase="auditing wishlist")
        valid = []
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=r.get("title"))
            rks = [resolution_rank(x) for x in str(r.get("owned_resolutions") or "").split(",")
                   if x.strip()]
            cur = max(rks, default=0)
            if cur and (not cutoff_rank or cur < cutoff_rank):
                continue   # below the cutoff (or chasing 'always best') — a live upgrade watch
            reason = ("already at your quality cutoff" if cur
                      else "owned, but its quality can't be read — the upgrader can't judge it")
            if r["kind"] == "movie":
                entity_id = f"m:{r['tmdb_id']}"
                title = f"{r.get('title') or '?'} — {reason}"
            else:
                code = "S%02dE%02d" % (r.get("season_number") or 0, r.get("episode_number") or 0)
                entity_id = f"e:{r['tmdb_id']}:{r.get('season_number')}:{r.get('episode_number')}"
                title = f"{r.get('title') or '?'} {code} — {reason}"
            valid.append(entity_id)
            context.create_finding(
                finding_type="stale_wishlist", severity="info",
                entity_type="wishlist", entity_id=entity_id,
                title=title, description="This wishlist row has nothing left to do.",
                details={"kind": r["kind"], "tmdb_id": r["tmdb_id"], "title": r.get("title"),
                         "reason": reason,
                         "poster_url": r.get("poster_url"), "library_id": r.get("library_id"),
                         "season_number": r.get("season_number"),
                         "episode_number": r.get("episode_number")})
        # Retire pending findings for rows that got removed by hand since.
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "stale_wishlist", valid)
        return result

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        d = finding.get("details") or {}
        if not d.get("tmdb_id"):
            return {"success": False, "error": "finding has no tmdb id"}
        n = context.db.remove_from_wishlist(
            d.get("kind") or "movie", tmdb_id=d["tmdb_id"],
            season_number=d.get("season_number"), episode_number=d.get("episode_number"))
        if not n:
            return {"success": False, "error": "row already gone"}
        return {"success": True, "action": "removed",
                "message": f"Removed {d.get('title') or 'the entry'} from the wishlist"}
