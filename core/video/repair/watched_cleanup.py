"""Watched Cleanup job — reclaim disk from movies you've already seen.

The video Expired Download Cleaner (Maintainerr's core rule): a movie WATCHED
at least once whose last watch is ``watched_days`` behind is a cleanup
candidate. Optionally (off by default) never-watched movies that have sat in
the library ``unwatched_days`` are flagged too.

Approving a finding moves the movie's file into the RECYCLE BIN (never a hard
delete — see core/video/recycle.py) and marks the library row file-less; the
weekly deep scan reconciles the server view. Movies whose watch state carries
no date (the server never reported one) are skipped, not guessed.

Freshness: watch-state changes now ride the incremental scan (the Plex
lastViewedAt delta), so candidates appear without waiting for a deep scan.

Heads-up in help_text: a cleaned movie is no longer "owned" — list collections
set to wishlist-missing members can re-add it. That's by design (you chose
those lists); exclude titles there if it bites.
"""

from __future__ import annotations

from datetime import datetime

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob


def _age_days(stamp: str | None, now: datetime) -> float | None:
    """Days between an ISO-ish DB stamp and now; None when unparseable."""
    if not stamp:
        return None
    raw = str(stamp)[:19].replace("T", " ")
    for text, fmt in ((raw, "%Y-%m-%d %H:%M:%S"), (raw[:10], "%Y-%m-%d")):
        try:
            return (now - datetime.strptime(text, fmt)).total_seconds() / 86400
        except ValueError:
            continue
    return None


def _gb(size) -> str:
    return "%.1f GB" % ((size or 0) / 1024 ** 3)


@register_job
class WatchedCleanupJob(VideoRepairJob):
    job_id = "watched_cleanup"
    display_name = "Watched Cleanup"
    description = "Flags watched (and optionally long-unwatched) movies to reclaim disk."
    help_text = ("The Maintainerr rule, SoulSync-style: movies you've watched and left "
                 "sitting are flagged once the last watch is old enough; approving moves "
                 "the file into the recycle bin (Settings → Library Organization) — never "
                 "a hard delete — and the weekly deep scan tidies the server view. "
                 "Optionally flag never-watched movies that have gathered dust. Dismissed "
                 "movies are never re-flagged. Note: if a list collection wishlists its "
                 "missing members, a cleaned movie can be re-acquired by it.")
    icon = "🍿"
    default_enabled = False
    default_interval_hours = 24
    default_settings = {"watched_days": 30, "include_unwatched": False, "unwatched_days": 365}
    setting_options = {"watched_days": [7, 14, 30, 60, 90, 180],
                       "include_unwatched": [False, True],
                       "unwatched_days": [90, 180, 365, 730]}
    auto_fix = False
    finding_types = ("watched_cleanup",)

    def estimate_scope(self, context: JobContext) -> int:
        return len(context.db.repair_watched_movies() or [])

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        try:
            watched_days = int(context.settings.get("watched_days", 30))
        except (TypeError, ValueError):
            watched_days = 30
        include_unwatched = bool(context.settings.get("include_unwatched", False))
        try:
            unwatched_days = int(context.settings.get("unwatched_days", 365))
        except (TypeError, ValueError):
            unwatched_days = 365

        now = datetime.now()
        rows = context.db.repair_watched_movies() or []
        context.report(total=len(rows), phase="checking watch state")
        valid = []
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=r.get("title"))
            watched = (r.get("play_count") or 0) > 0
            if watched:
                age = _age_days(r.get("last_viewed_at"), now)
                if age is None:          # watched, but the server gave no date — never guess
                    result.skipped += 1
                    continue
                if age < watched_days:
                    continue
                reason = "watched %d days ago" % int(age)
            else:
                if not include_unwatched:
                    continue
                age = _age_days(r.get("added_at"), now)
                if age is None or age < unwatched_days:
                    continue
                reason = "never watched in %d days" % int(age)

            entity_id = f"m:{r['movie_id']}"
            valid.append(entity_id)
            title = r.get("title") or "?"
            if r.get("year"):
                title = "%s (%s)" % (title, r["year"])
            context.create_finding(
                finding_type="watched_cleanup", severity="info",
                entity_type="movie", entity_id=entity_id,
                title=f"{title} — {reason} ({_gb(r.get('size_bytes'))})",
                description=r.get("relative_path") or "",
                details={"movie_id": r["movie_id"], "tmdb_id": r.get("tmdb_id"),
                         "title": r.get("title"), "year": r.get("year"),
                         "reason": reason, "watched": watched,
                         "play_count": r.get("play_count") or 0,
                         "last_viewed_at": r.get("last_viewed_at"),
                         "size_bytes": r.get("size_bytes"),
                         "relative_path": r.get("relative_path")})
        # A movie re-watched recently (or upgraded away) leaves the candidate set.
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "watched_cleanup", valid)
        return result

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        from core.video import organization, recycle
        from core.video.path_resolver import resolve_video_file_path, video_base_dirs
        d = finding.get("details") or {}
        if not d.get("movie_id") or not d.get("relative_path"):
            return {"success": False, "error": "finding has no file to clean"}
        real = resolve_video_file_path(d["relative_path"], video_base_dirs(context.db),
                                       size_bytes=d.get("size_bytes"))
        if not real:
            return {"success": False,
                    "error": "couldn't locate the file locally — check your library folders"}
        res = recycle.discard(real, organization.load(context.db), context.db,
                              reason="watched cleanup")
        if not res.get("ok"):
            return {"success": False, "error": "couldn't move the file to the recycle bin"}
        context.db.repair_mark_movie_fileless(d["movie_id"])
        title = d.get("title") or "the movie"
        where = "recycle bin" if res.get("recycled") else "gone (recycling is off)"
        return {"success": True, "action": "cleaned",
                "message": f"Cleaned {title} — {_gb(d.get('size_bytes'))} to the {where}"}
