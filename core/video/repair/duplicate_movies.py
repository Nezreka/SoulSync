"""Duplicate Movies job — the same film living twice.

Scan: two signals, one finding each —
  · the same tmdb_id owned as TWO separate library rows (usually the same film
    in two server libraries, or a bad match), and
  · one movie carrying 2+ version files (editions/upgrades that never cleaned up).

REPORT-ONLY by design: the finding shows every copy side by side (path, size,
resolution) so you can decide; deleting files from a bulk-approvable finding
needs a live shakedown before it gets teeth. Dismiss what's intentional
(editions you keep on purpose) — dismissed findings never come back.
"""

from __future__ import annotations

import hashlib
import json

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob


def _sig(vals) -> str:
    return hashlib.sha1(json.dumps(sorted(vals)).encode("utf-8")).hexdigest()[:10]


@register_job
class DuplicateMoviesJob(VideoRepairJob):
    job_id = "duplicate_movies"
    display_name = "Duplicate Movies"
    description = "Finds the same film owned twice — duplicate rows or stacked version files."
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
    finding_types = ("duplicate_movie",)
    # No fix() override: report-only — the UI offers Dismiss + details only.

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        dupes = context.db.repair_duplicate_movies()
        groups = dupes.get("rows") or []
        multi = dupes.get("files") or []
        context.report(total=len(groups) + len(multi), phase="comparing copies")
        done = 0
        for rows in groups:
            context.check_stop()
            done += 1
            result.scanned += 1
            head = rows[0]
            context.report(processed=done, current_item=head["title"])
            context.create_finding(
                finding_type="duplicate_movie", severity="info",
                entity_type="movie", entity_id=f"rows:{head['tmdb_id']}:{_sig([r['id'] for r in rows])}",
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
            context.create_finding(
                finding_type="duplicate_movie", severity="info",
                entity_type="movie",
                entity_id=f"files:{head['movie_id']}:{_sig([f['file_id'] for f in files])}",
                title=f"{head['title']} — {len(files)} version files",
                description=" · ".join(f"{f.get('resolution') or '?'} "
                                       f"({(f.get('size_bytes') or 0) / 1073741824:.1f} GB)"
                                       for f in files),
                details={"kind": "files", "movie_id": head["movie_id"],
                         "tmdb_id": head.get("tmdb_id"), "title": head["title"],
                         "year": head.get("year"), "files": files})
        return result
