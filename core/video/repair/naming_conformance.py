"""Naming Conformance job — retroactively apply the naming templates.

The organization templates only ever applied to NEW imports; a template change
(or a library that predates SoulSync) leaves existing files on old names
forever — the Sonarr/Radarr "preview rename" gap. This job renders the
CURRENT template for every owned movie/episode file and flags the ones whose
real on-disk path differs. The findings list IS the preview (current → new);
approving renames the file (plus its same-stem sidecars and subtitles) into
place. Bulk select = mass rename.

Safety: files that can't be located locally are skipped (never guessed); an
occupied destination is a per-finding error, nothing is overwritten. The stored
path is the SERVER's view, so a rename is mirrored into it the way Mass Rename
does (the new local name mapped back onto the server's prefix): left stale until
the next scan, every lookup by that path -- an upgrade finding the file it
replaces, a new episode finding its show's folder -- missed the renamed file and
fell back to the template path. The next scan still has the final word.

A media-server scan records no quality, so '$quality' is read off the file's
own current name when the library has none; otherwise every approved rename
stripped a quality tag the file already carried.

A template that names a title id ($imdbid ...) the title has no stored value
for would render it empty and strip the '{imdb-tt...}' the folder carries.
Those files are left alone, and the title gets one 'naming_id_missing'
finding saying so; approving it re-queues the title's TMDB enrichment (and
TVDB's, for a show), which fills the id when a source has it, and the next
check names the files as usual.
"""

from __future__ import annotations

import os

from core.video.file_ops import (
    move_into_place, move_sidecars, name_check, naming_fields, same_path,
    update_stored_path)
from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video.repair.naming_conformance")

# the naming + move logic lives in core.video.file_ops, shared with the
# library tab on a title's page; these names stay for the job's callers
_fields_of = naming_fields
_update_stored_path = update_stored_path
_same_path = same_path
_move_sidecars = move_sidecars


@register_job
class NamingConformanceJob(VideoRepairJob):
    job_id = "naming_conformance"
    display_name = "Naming Conformance"
    description = "Finds files whose names don't match your naming templates; approve = rename."
    help_text = ("Your naming templates (Settings → Library Organization) normally apply "
                 "only to new imports. This job checks every owned movie and episode "
                 "file against the CURRENT templates and lists the ones that differ — "
                 "the findings are the rename preview. Approving renames the file and "
                 "brings its sidecars/subtitles along; bulk select renames en masse. "
                 "Nothing is ever overwritten, and your media server picks the new "
                 "names up on its next scan.")
    icon = "🏷️"
    default_enabled = False
    default_interval_hours = 168      # weekly — template changes are rare events
    default_settings = {}
    setting_options = {}
    auto_fix = False
    finding_types = ("naming_mismatch", "naming_id_missing")

    def estimate_scope(self, context: JobContext) -> int:
        return len(context.db.repair_library_files() or [])

    def scan(self, context: JobContext) -> JobResult:
        from core.video import organization
        from core.video.path_resolver import resolve_video_file_path, video_base_dirs
        result = JobResult()
        settings = organization.load(context.db)
        base_dirs = video_base_dirs(context.db)
        from core.video.library_roots import library_roots
        roots = {kind: library_roots(context.db, kind) for kind in ("movie", "episode")}
        rows = context.db.repair_library_files() or []
        context.report(total=len(rows), phase="checking names")
        valid = []
        held = {}   # (scope, title_id) -> the title whose files wait on an id
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=r.get("title"))
            configured_roots = roots.get(r["scope"])
            if not configured_roots:
                result.skipped += 1          # that library has no configured folder
                continue
            real = resolve_video_file_path(r.get("relative_path"), base_dirs,
                                           size_bytes=r.get("size_bytes"))
            if not real:
                result.skipped += 1          # can't locate locally — never guess
                continue
            check = name_check(r, real, configured_roots, settings)
            expected = check["path"]
            if check["why"] == "ids":
                h = held.setdefault((r["scope"], r.get("title_id")), {
                    "scope": r["scope"], "title_id": r.get("title_id"), "title": r.get("title"),
                    "year": r.get("year"), "missing": check["missing"], "files": 0,
                    "example_path": real})
                h["files"] += 1
                result.skipped += 1
                continue
            if not expected:
                result.skipped += 1
                continue
            if _same_path(real, expected):
                continue
            entity_id = f"{r['scope']}:{r['item_id']}:{r['file_id']}"
            valid.append(entity_id)
            label = r.get("title") or "?"
            if r["scope"] == "episode":
                label = "%s S%02dE%02d" % (label, r.get("season") or 0, r.get("episode") or 0)
            context.create_finding(
                finding_type="naming_mismatch", severity="info",
                entity_type=r["scope"], entity_id=entity_id,
                title=f"{label} — file name doesn't match the template",
                description=os.path.basename(real) + "  →  " + os.path.basename(expected),
                details={"scope": r["scope"], "item_id": r["item_id"],
                         "file_id": r["file_id"], "title": r.get("title"),
                         "season": r.get("season"), "episode": r.get("episode"),
                         "current_path": real, "expected_path": expected,
                         "size_bytes": r.get("size_bytes")})
        held_ids = [self._held_finding(context, h) for h in held.values()]
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "naming_mismatch", valid)
            context.db.repair_dismiss_absent(self.job_id, "naming_id_missing", held_ids)
        return result

    def _held_finding(self, context: JobContext, h: dict) -> str:
        """one finding per title whose files wait on an id, not one per file"""
        from core.video import organization
        kind = "movie" if h["scope"] == "movie" else "show"
        entity_id = "%s:%s:%s" % (kind, h["title_id"], "+".join(h["missing"]))
        ids = " or ".join(organization.ID_LABELS[m] for m in h["missing"])
        label = h.get("title") or "?"
        if h.get("year") and not label.endswith("(%s)" % h["year"]):
            label += " (%s)" % h["year"]   # Plex's 'The Body (2026)' already carries it
        n = h["files"]
        context.create_finding(
            finding_type="naming_id_missing", severity="warning",
            entity_type=kind, entity_id=entity_id,
            title=f"{label} — name not checked: no {ids} stored",
            description=(f"Your naming template uses the {ids}, and this title has none stored, so "
                         f"its {n} file{'s' if n != 1 else ''} keep their names rather than lose the "
                         "id tag they may carry. Re-enrich asks TMDB for it again"
                         + (", and TVDB" if kind == "show" else "")
                         + ". If no source has it, set it in the title's Manage panel, under Matches."),
            # movie_id / show_id give the finding its poster on the tools page
            details={"kind": kind, "title_id": h["title_id"], kind + "_id": h["title_id"],
                     "title": h.get("title"),
                     "year": h.get("year"), "missing": h["missing"], "files": n,
                     "example_path": h["example_path"]})
        return entity_id

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        d = finding.get("details") or {}
        if finding.get("finding_type") == "naming_id_missing":
            if d.get("title_id") is None or d.get("kind") not in ("movie", "show"):
                return {"success": False, "error": "finding has no title"}
            context.db.enrichment_retry("tmdb", d["kind"], scope="item", item_id=d["title_id"])
            sources = "TMDB"
            if d["kind"] == "show":   # TVDB links a show to its IMDb and TMDB ids too
                context.db.enrichment_retry("tvdb", "show", scope="item", item_id=d["title_id"])
                sources = "TMDB and TVDB"
            return {"success": True, "action": "queued",
                    "message": f"Re-queued {sources} enrichment for {d.get('title') or 'the title'}. "
                               "The next naming check renames its files once the id is in."}
        cur, want = d.get("current_path"), d.get("expected_path")
        if not cur or not want:
            return {"success": False, "error": "finding has no paths"}
        return move_into_place(context.db, d.get("file_id"), cur, want)
