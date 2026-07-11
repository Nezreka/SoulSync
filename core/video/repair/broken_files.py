"""Broken Files job — dead partial downloads hiding as owned movies.

Scan: a cheap corruption heuristic, no ffmpeg — the file's probed runtime vs
the movie's known runtime. A 138-minute film whose file runs 61 minutes is a
truncated download; a file under a few MB is a stub. One finding per movie
(warning severity — these LOOK owned but won't play through).

Fix (approve): a replacement grab through the wishlist drain's own seams
(same path as Quality Upgrades); the import pipeline swaps the file in.
"""

from __future__ import annotations

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video.repair.broken_files")

_MIN_BYTES = 5 * 1024 * 1024      # anything under 5 MB is a stub, full stop


@register_job
class BrokenFilesJob(VideoRepairJob):
    job_id = "broken_files"
    display_name = "Broken Files"
    description = "Finds truncated or stub movie files (runtime far below the film's)."
    help_text = ("Compares each file's probed runtime against the movie's known "
                 "runtime — a file much shorter than the film is a dead partial "
                 "download, and a file under 5 MB is a stub. Approving grabs a "
                 "replacement immediately. The threshold setting is the minimum "
                 "acceptable percentage of the expected runtime.")
    icon = "🧨"
    default_enabled = False
    default_interval_hours = 72
    default_settings = {"min_percent": 75}
    setting_options = {"min_percent": [50, 60, 75, 90]}
    auto_fix = False
    finding_types = ("broken_file",)

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        try:
            min_pct = int(context.settings.get("min_percent", 75))
        except (TypeError, ValueError):
            min_pct = 75
        rows = context.db.repair_owned_movie_files()
        context.report(total=len(rows), phase="checking runtimes")
        valid = []
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=r["title"])
            expected = (r.get("runtime_minutes") or 0) * 60
            actual = r.get("runtime_seconds") or 0
            size = r.get("size_bytes") or 0
            stub = 0 < size < _MIN_BYTES
            truncated = expected > 0 and actual > 0 and (actual / expected) * 100 < min_pct
            if not stub and not truncated:
                continue
            reason = ("stub file (%.1f MB)" % (size / 1048576)) if stub else \
                ("runs %d of %d min" % (actual // 60, expected // 60))
            entity_id = f"{r['movie_id']}:{r['file_id']}"
            valid.append(entity_id)
            context.create_finding(
                finding_type="broken_file", severity="warning",
                entity_type="movie", entity_id=entity_id,
                file_path=r.get("relative_path"),
                title=f"{r['title']} — {reason}",
                description=r.get("relative_path") or "",
                details={"movie_id": r["movie_id"], "tmdb_id": r.get("tmdb_id"),
                         "title": r["title"], "year": r.get("year"),
                         "reason": reason, "expected_seconds": expected,
                         "actual_seconds": actual,
                         "file": {"relative_path": r.get("relative_path"),
                                  "size_bytes": size, "resolution": r.get("resolution"),
                                  "quality": r.get("quality")}})
        # Retire pending findings for files replaced/removed since the scan.
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "broken_file", valid)
        return result

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        from core.video.repair.grab import grab_movie
        return grab_movie(finding.get("details") or {})
