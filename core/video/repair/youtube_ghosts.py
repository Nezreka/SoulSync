"""YouTube Ghosts job — ledger rows whose file is gone from disk.

The ownership ledger (video_download_history, source=youtube, outcome=completed)
is SoulSync's memory of what it downloaded. When the user deletes episodes on
the server side, the ledger still says "owned" — badges lie, retention counts
lie, and the Channels tab overcounts. This job path-checks every un-pruned
ledger row and flags the ghosts.

Fix (approve) — two actions, both DB-only, files are never touched:
- default / ``prune``: stamp ``pruned_at`` — "the file is gone, but remember we
  had it". The badge clears; the scan dedup keeps excluding it (no re-download
  storm on the next channel scan).
- ``forget``: delete the history row — a real forget, the video becomes
  eligible for download again.

Safety: if the YouTube library folder is set but unreachable (a down SMB
mount), or more than half the checked files are missing at once, the scan
aborts with an error and creates nothing — an unmounted share must not
tombstone the whole library.
"""

from __future__ import annotations

import os
from datetime import date

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob

# Below this many checked rows the mass-missing guard stays out of the way —
# with a 2-video library, deleting 1 is a normal Tuesday, not an outage.
_GUARD_MIN_CHECKED = 5
_GUARD_MISSING_FRACTION = 0.5


@register_job
class YoutubeGhostsJob(VideoRepairJob):
    job_id = "youtube_ghosts"
    display_name = "YouTube Ghost Files"
    description = "Finds downloaded YouTube episodes whose file is gone from disk."
    help_text = ("SoulSync remembers every YouTube episode it downloaded. If you "
                 "delete files on the server side, that memory goes stale: episodes "
                 "still show as Downloaded and channel counts overcount. This job "
                 "verifies every remembered file still exists. Approving marks the "
                 "episode as deleted (badge clears, it will NOT be re-downloaded); "
                 "the Forget option in the details wipes the memory entirely so it "
                 "can be downloaded again. Files are never touched. If the YouTube "
                 "folder is unreachable or most files look missing at once, the scan "
                 "aborts instead of flagging your whole library.")
    icon = "👻"
    default_enabled = False
    default_interval_hours = 24    # Boulder's ask: verify the paths daily
    default_settings = {}
    setting_options = {}
    auto_fix = False
    finding_types = ("youtube_ghost",)

    def estimate_scope(self, context: JobContext) -> int:
        return len(context.db.youtube_ledger_rows() or [])

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        # Unreachable root = every path-check would "fail" for the wrong reason.
        # Raising surfaces the reason in the job log (worker → status 'error').
        root = str(context.db.get_setting("youtube_path") or "").strip()
        if root and not os.path.isdir(root):
            raise RuntimeError(
                f"YouTube folder unreachable ({root}) — aborted, nothing flagged")

        rows = context.db.youtube_ledger_rows() or []
        context.report(total=len(rows), phase="verifying files")
        missing, checked = [], 0
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=r.get("title"))
            checked += 1
            if not os.path.isfile(r["dest_path"]):
                missing.append(r)

        # Mass-missing guard: half the library "gone" is an outage, not cleanup.
        if (len(missing) > _GUARD_MISSING_FRACTION * checked
                and checked >= _GUARD_MIN_CHECKED):
            raise RuntimeError(
                f"{len(missing)} of {checked} files missing at once — looks like an "
                "unreachable drive, aborted (nothing flagged)")

        # Channel display names come from the remembered channel meta (the
        # ledger only stores the id); one lookup per distinct channel.
        names = {}
        for cid in {r.get("channel_id") for r in missing if r.get("channel_id")}:
            meta = context.db.get_channel_meta(cid) or {}
            names[cid] = meta.get("title") or ""

        valid = []
        for r in missing:
            context.check_stop()
            channel = names.get(r.get("channel_id")) or ""
            # The HISTORY ROW is the entity, not the video: after a Forget →
            # re-download → delete-again cycle the new ledger row must be able
            # to flag again (dedup keeps any-status findings forever). Same
            # reason no file_path is passed — a re-download recreates the same
            # path, and path-dedup would silence the new ghost.
            entity_id = f"yt:{r['media_id']}:{r['id']}"
            valid.append(entity_id)
            context.create_finding(
                finding_type="youtube_ghost", severity="warning",
                entity_type="youtube_video", entity_id=entity_id,
                title=f"{r.get('title') or r['media_id']} — file missing on disk",
                description=r.get("dest_path") or "",
                details={"history_id": r["id"], "media_id": r["media_id"],
                         "title": r.get("title"), "channel_id": r.get("channel_id"),
                         "channel": channel, "dest_path": r.get("dest_path"),
                         "published_at": r.get("published_at"),
                         "completed_at": r.get("completed_at"),
                         "thumb_url": f"https://i.ytimg.com/vi/{r['media_id']}/hqdefault.jpg"})
        # Retire pending findings whose file came back (or whose row got handled).
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "youtube_ghost", valid)
        return result

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        d = finding.get("details") or {}
        if not d.get("history_id"):
            return {"success": False, "error": "finding has no history id"}
        path = d.get("dest_path")
        if path and os.path.isfile(path):
            return {"success": False,
                    "error": "the file is back on disk — re-run the scan instead"}
        title = d.get("title") or "the episode"
        if fix_action == "forget":
            if not context.db.delete_download_history(d["history_id"]):
                return {"success": False, "error": "history row already gone"}
            return {"success": True, "action": "forgotten",
                    "message": f"Forgot {title} — it can be downloaded again"}
        if not context.db.mark_download_pruned(d["history_id"], date.today().isoformat()):
            return {"success": False, "error": "row already marked deleted (or gone)"}
        return {"success": True, "action": "marked_deleted",
                "message": f"Marked {title} as deleted — it won't be re-downloaded"}
