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

Safety, two tiers:
- YouTube root set but unreachable (a down SMB mount) → hard error, nothing
  flagged: every path-check would fail for the wrong reason.
- Mass-missing (more than half of >=5 checked files gone at once): could be an
  outage on a drive the root check can't see — but it's equally the "I wiped my
  downloads" / "I moved to a new drive" case, where the old paths will NEVER
  validate again and a silent abort would wedge the job forever. So instead of
  aborting, the event becomes ONE critical ``youtube_mass_missing`` finding;
  approving it creates the individual ghost findings for review (nothing is
  marked deleted by the approve itself).
"""

from __future__ import annotations

import hashlib
import os
from datetime import date

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob

# Below this many checked rows the mass-missing tier stays out of the way —
# with a 2-video library, deleting 1 is a normal Tuesday, not an outage.
_GUARD_MIN_CHECKED = 5
_GUARD_MISSING_FRACTION = 0.5


def _ghost_kwargs(row: dict, channel: str) -> dict:
    """The create_finding kwargs for one ghost — shared by the scan and the
    mass-finding fix so the two paths can never drift apart.

    The HISTORY ROW is the entity, not the video: after a Forget →
    re-download → delete-again cycle the new ledger row must be able to flag
    again (dedup keeps any-status findings forever). Same reason no file_path
    is passed — a re-download recreates the same path, and path-dedup would
    silence the new ghost."""
    return {
        "finding_type": "youtube_ghost", "severity": "warning",
        "entity_type": "youtube_video",
        "entity_id": f"yt:{row['media_id']}:{row['id']}",
        "title": f"{row.get('title') or row['media_id']} — file missing on disk",
        "description": row.get("dest_path") or "",
        "details": {"history_id": row["id"], "media_id": row["media_id"],
                    "title": row.get("title"), "channel_id": row.get("channel_id"),
                    "channel": channel, "dest_path": row.get("dest_path"),
                    "published_at": row.get("published_at"),
                    "completed_at": row.get("completed_at"),
                    "thumb_url": f"https://i.ytimg.com/vi/{row['media_id']}/hqdefault.jpg"},
    }


def _channel_names(db, rows) -> dict:
    """channel_id → display name from the remembered channel meta (the ledger
    only stores the id); one lookup per distinct channel."""
    names = {}
    for cid in {r.get("channel_id") for r in rows if r.get("channel_id")}:
        meta = db.get_channel_meta(cid) or {}
        names[cid] = meta.get("title") or ""
    return names


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
                 "can be downloaded again. Files are never touched. Safety: an "
                 "unreachable YouTube folder aborts the scan, and when most files "
                 "look missing at once (an outage — or a deliberate wipe / drive "
                 "change) you get ONE finding asking whether it's real; approving "
                 "that flags the videos individually for review.")
    icon = "👻"
    default_enabled = False
    default_interval_hours = 24    # Boulder's ask: verify the paths daily
    default_settings = {}
    setting_options = {}
    auto_fix = False
    finding_types = ("youtube_ghost", "youtube_mass_missing")

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

        # Every scan is a COMPLETE enumeration from here on, so both types'
        # valid lists are computed regardless of which tier fires: pending
        # ghosts stay alive while their row is still missing (even during a
        # mass event), and a pending mass finding retires when the situation
        # changes (its entity hash covers the exact missing set).
        ghost_valid = [f"yt:{r['media_id']}:{r['id']}" for r in missing]
        mass_valid = []

        mass = (len(missing) > _GUARD_MISSING_FRACTION * checked
                and checked >= _GUARD_MIN_CHECKED)
        if mass:
            digest = hashlib.sha1(",".join(
                sorted(str(r["id"]) for r in missing)).encode()).hexdigest()[:12]
            mass_entity = f"mass:{digest}"
            mass_valid.append(mass_entity)
            names = _channel_names(context.db, missing[:8])
            context.create_finding(
                finding_type="youtube_mass_missing", severity="critical",
                entity_type="youtube_ledger", entity_id=mass_entity,
                title=f"{len(missing)} of {checked} downloaded YouTube videos "
                      "missing at once",
                description="Could be an unreachable drive — nothing was flagged. "
                            "Approve ONLY if this is real (files deleted or drive "
                            "changed); that creates the individual findings.",
                details={"missing_count": len(missing), "checked": checked,
                         "history_ids": [r["id"] for r in missing],
                         "sample": [{"title": r.get("title"),
                                     "channel": names.get(r.get("channel_id")) or "",
                                     "dest_path": r.get("dest_path")}
                                    for r in missing[:8]]})
        else:
            names = _channel_names(context.db, missing)
            for r in missing:
                context.check_stop()
                kw = _ghost_kwargs(r, names.get(r.get("channel_id")) or "")
                context.create_finding(**kw)

        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "youtube_ghost", ghost_valid)
            context.db.repair_dismiss_absent(self.job_id, "youtube_mass_missing", mass_valid)
        return result

    # ── fixes ────────────────────────────────────────────────────────────────
    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        if finding.get("finding_type") == "youtube_mass_missing":
            return self._fix_mass(context, finding)
        return self._fix_ghost(context, finding, fix_action)

    def _fix_mass(self, context: JobContext, finding: dict) -> dict:
        """The user vouched the mass event is real: create the per-video ghost
        findings (re-verifying each path NOW — rows restored or handled since
        the scan are skipped). Nothing is marked deleted here."""
        ids = set((finding.get("details") or {}).get("history_ids") or [])
        if not ids:
            return {"success": False, "error": "finding carries no history ids"}
        rows = [r for r in context.db.youtube_ledger_rows() or []
                if r["id"] in ids and not os.path.isfile(r["dest_path"])]
        names = _channel_names(context.db, rows)
        created = 0
        for r in rows:
            kw = _ghost_kwargs(r, names.get(r.get("channel_id")) or "")
            if context.db.repair_create_finding(self.job_id, **kw):
                created += 1
        if not created:
            return {"success": True, "action": "flagged",
                    "message": "Nothing left to flag — the files are back or "
                               "already handled"}
        return {"success": True, "action": "flagged",
                "message": f"Flagged {created} missing videos individually — "
                           "review and approve them (bulk select works)"}

    def _fix_ghost(self, context: JobContext, finding: dict, fix_action=None) -> dict:
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
