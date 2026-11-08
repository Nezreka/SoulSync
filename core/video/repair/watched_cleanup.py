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

Several viewers (#1941): once any profile keeps its own watch state (a linked
Plex account), a movie is a candidate only when every viewer who has watched
anything in the last ``active_days`` has watched it -- the owner (the shared
columns) counts as a viewer too. Anyone with it in progress holds it back,
counted or not, and the age runs from the last of those watches. With nobody
active the job flags nothing. Each finding names who watched and who wasn't
counted. With no linked profiles at all the job behaves exactly as before.

Heads-up in help_text: a cleaned movie is no longer "owned" — list collections
set to wishlist-missing members can re-add it. That's by design (you chose
those lists); exclude titles there if it bites.
"""

from __future__ import annotations

from datetime import datetime

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video_watched_cleanup")


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


def _int_setting(settings, key, default) -> int:
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def _viewers(db, active_days: int, now: datetime):
    """(counted, not_counted) for a run, or (None, None) when no profile keeps
    its own watch state -- the single-viewer case, judged as it always was.
    Each viewer is {key, name, last_active}; ``key`` is 'owner' or a profile id."""
    from core import media_accounts
    linked = [pid for pid, _token in media_accounts.plex_linked_profiles()]
    if not linked:
        return None, None
    names, admin_name = media_accounts.profile_names()
    activity = db.watch_last_activity(linked)
    everyone = [{"key": "owner", "name": admin_name or "Owner",
                 "last_active": activity.get("owner")}]
    everyone += [{"key": pid, "name": names.get(pid) or f"profile {pid}",
                  "last_active": activity["profiles"].get(pid)} for pid in linked]
    counted, not_counted = [], []
    for v in everyone:
        age = _age_days(v["last_active"], now)
        (counted if age is not None and age < active_days else not_counted).append(v)
    return counted, not_counted


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
                 "movies are never re-flagged. With profiles linked to their own Plex "
                 "accounts, a movie waits until everyone who has watched anything in the "
                 "last 'active days' has seen it, and anyone part-way through holds it "
                 "back. Note: if a list collection wishlists its "
                 "missing members, a cleaned movie can be re-acquired by it.")
    icon = "🍿"
    default_enabled = False
    default_interval_hours = 24
    default_settings = {"watched_days": 30, "include_unwatched": False, "unwatched_days": 365,
                        "active_days": 90}
    setting_options = {"watched_days": [7, 14, 30, 60, 90, 180],
                       "include_unwatched": [False, True],
                       "unwatched_days": [90, 180, 365, 730],
                       "active_days": [30, 60, 90, 180, 365]}
    auto_fix = False
    finding_types = ("watched_cleanup",)

    def estimate_scope(self, context: JobContext) -> int:
        return len(context.db.repair_watched_movies() or [])

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        watched_days = _int_setting(context.settings, "watched_days", 30)
        include_unwatched = bool(context.settings.get("include_unwatched", False))
        unwatched_days = _int_setting(context.settings, "unwatched_days", 365)
        active_days = _int_setting(context.settings, "active_days", 90)

        now = datetime.now()
        rows = context.db.repair_watched_movies() or []
        counted, not_counted = _viewers(context.db, active_days, now)
        members = context.db.repair_movie_watch_by_profile() if counted is not None else {}
        if counted is not None and not counted:
            # Several viewers, none active: "never watched by anyone counted" would
            # hold for the whole library, so nothing is a candidate.
            logger.info("Watched Cleanup: no viewer watched anything in the last %d days -- "
                        "nothing flagged", active_days)
            rows = []
        context.report(total=len(rows), phase="checking watch state")
        valid = []
        held_in_progress = held_partial = 0
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=r.get("title"))
            viewers = None
            if counted is None:
                watched = (r.get("play_count") or 0) > 0
                last_viewed = r.get("last_viewed_at")
            else:
                states = {"owner": {"play_count": r.get("play_count"),
                                    "last_viewed_at": r.get("last_viewed_at"),
                                    "view_offset_ms": r.get("view_offset_ms")}}
                states.update(members.get(r["movie_id"], {}))
                if any((st.get("view_offset_ms") or 0) > 0 for st in states.values()):
                    held_in_progress += 1     # someone is part-way through -- counted or not
                    continue
                viewers = []
                for v in counted:
                    st = states.get(v["key"]) or {}
                    viewers.append({"name": v["name"], "counted": True,
                                    "watched": (st.get("play_count") or 0) > 0,
                                    "last_viewed_at": st.get("last_viewed_at")})
                seen = [v for v in viewers if v["watched"]]
                if seen and len(seen) < len(viewers):
                    held_partial += 1
                    continue
                watched = bool(seen)
                stamps = [v["last_viewed_at"] for v in seen]
                if watched and not all(stamps):
                    result.skipped += 1       # a watch with no date -- never guess
                    continue
                last_viewed = max(stamps) if stamps else None
                viewers += [{"name": v["name"], "counted": False, "watched": None,
                             "last_active": v["last_active"]} for v in not_counted]
            if watched:
                age = _age_days(last_viewed, now)
                if age is None:          # watched, but the server gave no date — never guess
                    result.skipped += 1
                    continue
                if age < watched_days:
                    continue
                reason = "watched %d days ago" % int(age)
                if viewers is not None and len(counted) > 1:
                    reason = "watched by all %d viewers, last %d days ago" % (len(counted), int(age))
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
            details = {"movie_id": r["movie_id"], "tmdb_id": r.get("tmdb_id"),
                       "title": r.get("title"), "year": r.get("year"),
                       "reason": reason, "watched": watched,
                       "play_count": r.get("play_count") or 0,
                       "last_viewed_at": last_viewed,
                       "size_bytes": r.get("size_bytes"),
                       "relative_path": r.get("relative_path")}
            if viewers is not None:
                details["viewers"] = viewers
                details["active_days"] = active_days
            context.create_finding(
                finding_type="watched_cleanup", severity="info",
                entity_type="movie", entity_id=entity_id,
                title=f"{title} — {reason} ({_gb(r.get('size_bytes'))})",
                description=r.get("relative_path") or "",
                details=details)
        if counted is not None:
            logger.info("Watched Cleanup: %d viewer(s) counted, %d not counted (nothing watched "
                        "in %d days); held back %d in progress, %d not watched by everyone",
                        len(counted), len(not_counted), active_days, held_in_progress, held_partial)
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
        base_dirs = video_base_dirs(context.db)
        settings = organization.load(context.db)
        # Recycle EVERY version of the movie (a multi-part movie has >1 file);
        # marking it file-less while a smaller copy survived on disk would orphan
        # it. Fall back to the finding's single path if the file list is empty.
        files = context.db.movie_file_paths(d["movie_id"]) or [
            {"relative_path": d["relative_path"], "size_bytes": d.get("size_bytes")}]
        located = [(f, resolve_video_file_path(f["relative_path"], base_dirs,
                                               size_bytes=f.get("size_bytes"))) for f in files]
        if not any(real for _f, real in located):
            return {"success": False,
                    "error": "couldn't locate the file locally — check your library folders"}
        recycled_any = False
        for _f, real in located:
            if not real:
                continue                 # a version already gone is fine — keep going
            res = recycle.discard(real, settings, context.db, reason="watched cleanup")
            if not res.get("ok"):
                return {"success": False, "error": "couldn't move the file to the recycle bin"}
            recycled_any = recycled_any or res.get("recycled")
        context.db.repair_mark_movie_fileless(d["movie_id"])
        title = d.get("title") or "the movie"
        where = "recycle bin" if recycled_any else "gone (recycling is off)"
        return {"success": True, "action": "cleaned",
                "message": f"Cleaned {title} — {_gb(d.get('size_bytes'))} to the {where}"}
