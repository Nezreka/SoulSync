"""Quality Upgrade job — movies/episodes below the quality profile's cutoff.

Scan: each owned movie/episode's BEST file (highest resolution, then size) is judged
against the shared video quality profile with the same ``quality_eval`` seam
the Download modal uses. Below the cutoff → one finding per title. With no
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
    description = "Finds movies and episodes whose best file sits below your quality cutoff."
    help_text = ("Judges every owned movie and episode's best file against your quality "
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
        from core.video.quality_profile import list_profiles, load as load_profile, profile_by_id
        default_profile = load_profile(context.db)
        # per-title profiles (P2): a title may have its own cutoff even when the
        # Default has none — only bail early when NO profile anywhere has one
        if not any((p.get("profile") or {}).get("cutoff_resolution")
                   for p in list_profiles(context.db)):
            logger.info("quality_upgrade: no cutoff configured on any profile — nothing to judge")
            return result
        _prof_memo: dict = {}

        def _memoized_profile(pid):
            pid = pid or 0
            if pid not in _prof_memo:
                _prof_memo[pid] = profile_by_id(context.db, pid) if pid else default_profile
            return _prof_memo[pid]

        def _profile_for_movie(movie_id):
            return _memoized_profile(context.db.quality_profile_id_for("movie", library_id=movie_id))

        def _profile_for_show(row):
            return _memoized_profile(context.db.quality_profile_id_for("show", tmdb_id=row.get("tmdb_id")))

        by_movie: dict = {}
        for r in context.db.repair_owned_movie_files():
            by_movie.setdefault(("movie", r["movie_id"]), []).append(r)
        by_episode: dict = {}
        for r in context.db.repair_library_files():
            if r.get("scope") == "episode":
                by_episode.setdefault(("episode", r["item_id"]), []).append(r)

        groups = list(by_movie.items()) + list(by_episode.items())
        context.report(total=len(groups), phase="judging files")
        valid = []
        for i, ((kind, item_id), files) in enumerate(groups, 1):
            context.check_stop()
            result.scanned += 1
            best = best_file(files)
            title = best["title"] if kind == "movie" else "%s S%02dE%02d" % (
                best.get("series") or best.get("title") or "?",
                int(best.get("season") or 0), int(best.get("episode") or 0))
            context.report(processed=i, current_item=title)
            profile = _profile_for_movie(item_id) if kind == "movie" else _profile_for_show(best)
            cutoff = (profile or {}).get("cutoff_resolution") or ""
            if not cutoff or meets_cutoff(best.get("resolution"), profile):
                continue
            label = resolution_label(best.get("resolution")) or best.get("resolution") or "unknown"
            rank = resolution_rank(best.get("resolution"))
            entity_id = f"{item_id}:{rank}" if kind == "movie" else f"ep:{item_id}:{rank}"
            valid.append(entity_id)
            details = {"kind": kind, "tmdb_id": best.get("tmdb_id"),
                       "title": best.get("title"), "year": best.get("year"),
                       "cutoff": resolution_label(cutoff) or cutoff,
                       "file": {k: best.get(k) for k in _FILE_FIELDS}}
            if kind == "movie":
                details["movie_id"] = item_id
            else:
                details.update({"episode_id": item_id, "show_tmdb_id": best.get("tmdb_id"),
                                "show_title": best.get("series") or best.get("title"),
                                "season_number": best.get("season"),
                                "episode_number": best.get("episode"),
                                "episode_title": best.get("episode_title")})
            context.create_finding(
                finding_type="quality_upgrade", severity="info",
                entity_type=kind, entity_id=entity_id,
                file_path=best.get("relative_path"),
                title=f"{title} — {label}, cutoff is {resolution_label(cutoff) or cutoff}",
                description=f"Best file: {label}"
                            + (f" · {best.get('video_codec')}" if best.get("video_codec") else "")
                            + (f" · {(best.get('size_bytes') or 0) / 1073741824:.1f} GB"
                               if best.get("size_bytes") else ""),
                details=details)
        # A complete scan retires pending findings for movies/episodes that got upgraded
        # or removed since (never on a partial/errored pass).
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "quality_upgrade", valid)
        return result

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        from core.video.repair.grab import grab_episode, grab_movie
        details = finding.get("details") or {}
        if details.get("kind") == "episode" or finding.get("entity_type") == "episode":
            return grab_episode(details)
        return grab_movie(details)
