"""Duplicate Copies job — the same title living twice.

Scan: three signals, one finding each —
  · the same tmdb_id owned as TWO separate library rows (usually the same film
    in two server libraries, or a bad match),
  · one movie carrying 2+ version files (editions/upgrades that never cleaned up), and
  · one EPISODE carrying 2+ files — the TV half, which never had a report at all.

Every finding now names the DRIVE each copy sits on. That is the fact this job was
missing: when SoulSync cannot resolve the copy it already owns (the stored path is
the media server's view of a mount SoulSync has no mapping for), an upgrade files a
second copy in the template location instead of replacing the first. Nothing is
corrupted — you just end up with the old copy on the old drive and, until now, no
way to find it from the app. "Two copies" is not actionable; "one on
/mnt/easystore3, one on /mnt/plex_20tb" is.

Copies at the SAME resolution on DIFFERENT drives are flagged warning — that is the
shape a fork leaves. A mix of resolutions stays info: on the live library 3,409 of
6,838 multi-file titles have differing resolutions, which is usually a 4K and a
1080p somebody keeps on purpose. From the database alone those are indistinguishable
from a fork, which is why this job still refuses to talk about reclaimable space.

REPORT-ONLY by design: the finding shows every copy side by side (path, size,
resolution) so you can decide; deleting files from a bulk-approvable finding
needs a live shakedown before it gets teeth. Dismiss what's intentional
(editions you keep on purpose) — dismissed findings never come back.
"""

from __future__ import annotations

import hashlib
import json

from core.video.duplicate_copies import describe_copies, severity_for, summary_line
from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob


def _sig(vals) -> str:
    return hashlib.sha1(json.dumps(sorted(vals)).encode("utf-8")).hexdigest()[:10]


@register_job
class DuplicateMoviesJob(VideoRepairJob):
    job_id = "duplicate_movies"
    display_name = "Duplicate Copies"
    description = "Finds the same title owned twice — duplicate rows, stacked movie files, or an episode with two copies. Shows which drive each is on."
    help_text = ("Two signals: the same TMDB title existing as two separate "
                 "library entries, and a single movie holding multiple video "
                 "files. Report-only — every copy is shown side by side and YOU "
                 "decide; nothing is ever deleted from here. Dismiss the "
                 "intentional ones (kept editions) and they stay dismissed.")
    icon = "👯"
    default_enabled = False
    default_interval_hours = 168
    default_settings = {}
    setting_options = {}
    auto_fix = False
    finding_types = ("duplicate_movie", "duplicate_episode")
    # No fix() override: report-only — the UI offers Dismiss + details only.

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        dupes = context.db.repair_duplicate_movies()
        groups = dupes.get("rows") or []
        multi = dupes.get("files") or []
        context.report(total=len(groups) + len(multi), phase="comparing copies")
        done = 0
        valid = []
        for rows in groups:
            context.check_stop()
            done += 1
            result.scanned += 1
            head = rows[0]
            context.report(processed=done, current_item=head["title"])
            entity_rows = f"rows:{head['tmdb_id']}:{_sig([r['id'] for r in rows])}"
            valid.append(entity_rows)
            context.create_finding(
                finding_type="duplicate_movie", severity="info",
                entity_type="movie", entity_id=entity_rows,
                title=f"{head['title']} — {len(rows)} library entries",
                description=" · ".join(f"#{r['id']} ({r.get('server_source') or '?'})"
                                       for r in rows),
                details={"kind": "rows", "tmdb_id": head["tmdb_id"], "title": head["title"],
                         "year": head.get("year"), "rows": rows})
        for files in multi:
            context.check_stop()
            done += 1
            result.scanned += 1
            head = files[0]
            context.report(processed=done, current_item=head["title"])
            entity_files = f"files:{head['movie_id']}:{_sig([f['file_id'] for f in files])}"
            valid.append(entity_files)
            summary = describe_copies(files)
            context.create_finding(
                finding_type="duplicate_movie", severity=severity_for(summary),
                entity_type="movie",
                entity_id=entity_files,
                title=f"{head['title']} — {len(files)} version files",
                description=summary_line(summary),
                details={"kind": "files", "movie_id": head["movie_id"],
                         "tmdb_id": head.get("tmdb_id"), "title": head["title"],
                         "year": head.get("year"), "files": files,
                         "roots": summary["roots"], "spans_drives": summary["spans_drives"],
                         "same_resolution": summary["same_resolution"]})
        # ── episodes: the TV half, which had no report at all ───────────────
        # A season that upgraded and forked left two files per episode and said
        # nothing anywhere; only movies were ever checked.
        ep_valid = []
        try:
            ep_groups = context.db.repair_duplicate_episodes() or []
        except Exception:   # noqa: BLE001 - a missing/old DB method must not fail the whole job
            ep_groups = []
            result.errors += 1
        for files in ep_groups:
            context.check_stop()
            done += 1
            result.scanned += 1
            head = files[0]
            label = "%s S%02dE%02d" % (head.get("show_title") or "Show",
                                       int(head.get("season_number") or 0),
                                       int(head.get("episode_number") or 0))
            context.report(processed=done, current_item=label)
            entity = f"ep:{head['episode_id']}:{_sig([f['file_id'] for f in files])}"
            ep_valid.append(entity)
            summary = describe_copies(files)
            context.create_finding(
                finding_type="duplicate_episode", severity=severity_for(summary),
                entity_type="episode", entity_id=entity,
                title=f"{label} — {len(files)} copies",
                description=summary_line(summary),
                details={"kind": "episode_files", "episode_id": head["episode_id"],
                         "show_id": head.get("show_id"), "tmdb_id": head.get("tmdb_id"),
                         "show_title": head.get("show_title"),
                         "episode_title": head.get("episode_title"),
                         "season_number": head.get("season_number"),
                         "episode_number": head.get("episode_number"),
                         "files": files, "roots": summary["roots"],
                         "spans_drives": summary["spans_drives"],
                         "same_resolution": summary["same_resolution"]})

        # Retire pending findings for duplicates cleaned up since the scan. Each
        # type is swept against ITS OWN valid list — passing the movie list while
        # sweeping episodes would dismiss every episode finding on sight.
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "duplicate_movie", valid)
            context.db.repair_dismiss_absent(self.job_id, "duplicate_episode", ep_valid)
        return result
