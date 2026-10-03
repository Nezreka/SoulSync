"""Broken Files job — dead partial downloads hiding as owned media.

Scan: a cheap corruption heuristic, no ffmpeg — the file's probed runtime vs
the item's known runtime. A 138-minute film whose file runs 61 minutes is a
truncated download; a 42-minute episode whose file runs 20 minutes is the same;
a file under a few MB is a stub. One finding per item (warning severity — these
LOOK owned but won't play through).

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
    description = "Finds truncated or stub movie and episode files (runtime far below the item's)."
    help_text = ("Compares each file's probed runtime against the movie/episode's known "
                 "runtime — a file much shorter than the item is a dead partial "
                 "download, and a file under 5 MB is a stub. Approving grabs a "
                 "replacement immediately. The threshold setting is the minimum "
                 "acceptable percentage of the expected runtime.")
    icon = "🧨"
    default_enabled = True
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
        movie_rows = context.db.repair_owned_movie_files()
        episode_rows = context.db.repair_owned_episode_files()
        context.report(total=len(movie_rows) + len(episode_rows), phase="checking runtimes")
        valid = []
        valid += self._scan_movies(context, result, min_pct, movie_rows)
        valid += self._scan_episodes(context, result, min_pct, episode_rows)
        # Retire pending findings for files replaced/removed since the scan.
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "broken_file", valid)
        return result

    @staticmethod
    def _verdict(size: int, expected: float, actual: float, min_pct: int):
        """(is_broken, reason) for one file: stub, truncated, or fine."""
        if 0 < size < _MIN_BYTES:
            return True, "stub file (%.1f MB)" % (size / 1048576)
        if expected > 0 and actual > 0 and (actual / expected) * 100 < min_pct:
            return True, "runs %d of %d min" % (actual // 60, expected // 60)
        return False, ""

    def _scan_movies(self, context: JobContext, result: JobResult, min_pct: int,
                     rows: list) -> list:
        context.report(phase="checking movie runtimes")
        valid = []
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=r["title"])
            expected = (r.get("runtime_minutes") or 0) * 60
            broken, reason = self._verdict(r.get("size_bytes") or 0, expected,
                                          r.get("runtime_seconds") or 0, min_pct)
            if not broken:
                continue
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
                         "actual_seconds": r.get("runtime_seconds") or 0,
                         "file": {"relative_path": r.get("relative_path"),
                                  "size_bytes": r.get("size_bytes") or 0,
                                  "resolution": r.get("resolution"),
                                  "quality": r.get("quality")}})
        return valid

    def _scan_episodes(self, context: JobContext, result: JobResult, min_pct: int,
                       rows: list) -> list:
        context.report(phase="checking episode runtimes")
        valid = []
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i,
                           current_item="%s S%02dE%02d" % (r["show_title"], r["season_number"],
                                                          r["episode_number"]))
            expected = (r.get("runtime_minutes") or 0) * 60
            broken, reason = self._verdict(r.get("size_bytes") or 0, expected,
                                          r.get("runtime_seconds") or 0, min_pct)
            if not broken:
                continue
            entity_id = f"episode:{r['episode_id']}:{r['file_id']}"
            valid.append(entity_id)
            context.create_finding(
                finding_type="broken_file", severity="warning",
                entity_type="episode", entity_id=entity_id,
                file_path=r.get("relative_path"),
                title=f"{r['show_title']} S{r['season_number']:02d}E{r['episode_number']:02d} — {reason}",
                description=r.get("relative_path") or "",
                details={"episode_id": r["episode_id"],
                         "show_tmdb_id": r.get("show_tmdb_id"), "tmdb_id": r.get("show_tmdb_id"),
                         "show_title": r["show_title"], "season_number": r["season_number"],
                         "episode_number": r["episode_number"],
                         "episode_title": r.get("episode_title"),
                         "reason": reason, "expected_seconds": expected,
                         "actual_seconds": r.get("runtime_seconds") or 0,
                         "file": {"relative_path": r.get("relative_path"),
                                  "size_bytes": r.get("size_bytes") or 0,
                                  "resolution": r.get("resolution"),
                                  "quality": r.get("quality")}})
        return valid

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        details = finding.get("details") or {}
        if details.get("episode_id") is not None:
            from core.video.repair.grab import grab_episode
            return grab_episode(details)
        from core.video.repair.grab import grab_movie
        return grab_movie(details)
