"""Quality Upgrade job — movies below the quality profile's cutoff.

Scan: each owned movie's BEST file (highest resolution, then size) is judged
against the shared video quality profile with the same ``quality_eval`` seam
the Download modal uses. Below the cutoff → one finding per movie. With no
cutoff configured ('always chase the best') the job stays quiet — flagging the
entire library isn't a finding, it's noise.

Fix (approve): a real upgrade grab — the wishlist drain's own search/pick/
enqueue seams run for the single movie (the drain itself refuses owned
titles, so upgrades can't ride it). The toast reports the actual outcome.
"""

from __future__ import annotations

from core.video.quality_eval import meets_cutoff, resolution_label, resolution_rank
from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video.repair.quality_upgrade")

_FILE_FIELDS = ("relative_path", "size_bytes", "resolution", "quality",
                "video_codec", "audio_codec", "release_source")


def best_file(files: list) -> dict:
    return sorted(files, key=lambda f: (resolution_rank(f.get("resolution")),
                                        f.get("size_bytes") or 0), reverse=True)[0]


@register_job
class QualityUpgradeJob(VideoRepairJob):
    job_id = "quality_upgrade"
    display_name = "Quality Upgrades"
    description = "Finds movies whose best file sits below your quality cutoff."
    help_text = ("Judges every owned movie's best file against your quality "
                 "profile's cutoff (Settings → Downloads). Approving searches "
                 "for a better release right away and enqueues it — the import "
                 "step replaces the old file only if the grab really is an "
                 "upgrade. No cutoff set ('always chase the best') = no scan, "
                 "so the whole library isn't flagged as noise.")
    icon = "⬆️"
    default_enabled = False
    default_interval_hours = 168
    default_settings = {}
    setting_options = {}
    auto_fix = False
    finding_types = ("quality_upgrade",)

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        from core.video.quality_profile import load as load_profile
        profile = load_profile(context.db)
        cutoff = (profile or {}).get("cutoff_resolution") or ""
        if not cutoff:
            logger.info("quality_upgrade: no cutoff configured — nothing to judge")
            return result
        by_movie: dict = {}
        for r in context.db.repair_owned_movie_files():
            by_movie.setdefault(r["movie_id"], []).append(r)
        context.report(total=len(by_movie), phase="judging files")
        valid = []
        for i, (movie_id, files) in enumerate(by_movie.items(), 1):
            context.check_stop()
            result.scanned += 1
            best = best_file(files)
            context.report(processed=i, current_item=best["title"])
            if meets_cutoff(best.get("resolution"), profile):
                continue
            label = resolution_label(best.get("resolution")) or best.get("resolution") or "unknown"
            entity_id = f"{movie_id}:{resolution_rank(best.get('resolution'))}"
            valid.append(entity_id)
            context.create_finding(
                finding_type="quality_upgrade", severity="info",
                entity_type="movie", entity_id=entity_id,
                file_path=best.get("relative_path"),
                title=f"{best['title']} — {label}, cutoff is {resolution_label(cutoff) or cutoff}",
                description=f"Best file: {label}"
                            + (f" · {best.get('video_codec')}" if best.get("video_codec") else "")
                            + (f" · {(best.get('size_bytes') or 0) / 1073741824:.1f} GB"
                               if best.get("size_bytes") else ""),
                details={"movie_id": movie_id, "tmdb_id": best.get("tmdb_id"),
                         "title": best["title"], "year": best.get("year"),
                         "cutoff": resolution_label(cutoff) or cutoff,
                         "file": {k: best.get(k) for k in _FILE_FIELDS}})
        # A complete scan retires pending findings for movies that got upgraded
        # or removed since (never on a partial/errored pass).
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "quality_upgrade", valid)
        return result

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        from core.video.repair.grab import grab_movie
        return grab_movie(finding.get("details") or {})
