"""Wishlist Audit job — rows the acquisition pipeline will never act on.

Scan: wishlist entries whose target is ALREADY OWNED — a movie with an owned
library match, or an episode whose file landed through another path. The
drain skips these forever (its owned-exclusion breaks re-grab loops), so
they're pure clutter that makes the wishlist read bigger than it is.

Fix (approve): remove the row. Nothing else — the owned copy is untouched.
"""

from __future__ import annotations

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob


@register_job
class WishlistAuditJob(VideoRepairJob):
    job_id = "wishlist_audit"
    display_name = "Wishlist Audit"
    description = "Finds wishlist entries you already own — dead weight the downloader skips."
    help_text = ("The download engine deliberately never re-grabs something you "
                 "own, so a wishlist row for an owned movie or episode just sits "
                 "there forever. This sweeps them out. Approving removes the "
                 "wishlist row only — your files are never touched.")
    icon = "🧹"
    default_enabled = False
    default_interval_hours = 1     # cheap DB sweep — keeps pace with the download engine
    default_settings = {}
    setting_options = {}
    auto_fix = False
    finding_types = ("stale_wishlist",)

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        rows = context.db.repair_stale_wishlist()
        context.report(total=len(rows), phase="auditing wishlist")
        valid = []
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=r.get("title"))
            if r["kind"] == "movie":
                entity_id = f"m:{r['tmdb_id']}"
                title = f"{r.get('title') or '?'} — already owned, still wishlisted"
            else:
                code = "S%02dE%02d" % (r.get("season_number") or 0, r.get("episode_number") or 0)
                entity_id = f"e:{r['tmdb_id']}:{r.get('season_number')}:{r.get('episode_number')}"
                title = f"{r.get('title') or '?'} {code} — already owned, still wishlisted"
            valid.append(entity_id)
            context.create_finding(
                finding_type="stale_wishlist", severity="info",
                entity_type="wishlist", entity_id=entity_id,
                title=title, description="The downloader skips owned items — this row is dead weight.",
                details={"kind": r["kind"], "tmdb_id": r["tmdb_id"], "title": r.get("title"),
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
