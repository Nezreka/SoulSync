"""Naming Conformance job — retroactively apply the naming templates.

The organization templates only ever applied to NEW imports; a template change
(or a library that predates SoulSync) leaves existing files on old names
forever — the Sonarr/Radarr "preview rename" gap. This job renders the
CURRENT template for every owned movie/episode file and flags the ones whose
real on-disk path differs. The findings list IS the preview (current → new);
approving renames the file (plus its same-stem sidecars and subtitles) into
place. Bulk select = mass rename.

Safety: files that can't be located locally are skipped (never guessed); an
occupied destination is a per-finding error, nothing is overwritten; DB paths
are NOT rewritten here — the stored path is the SERVER's view, and the next
scan reconciles it after the server notices the rename (dedup keeps handled
findings from re-flagging meanwhile).
"""

from __future__ import annotations

import os
import shutil

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob

_SIDECAR_SUFFIXES = (".nfo", "-thumb.jpg", "-thumb.jpeg", "-thumb.png", "-thumb.webp",
                     ".srt", ".ass", ".sub", ".idx", ".jpg")


def _fields_of(row: dict) -> dict:
    return {"title": row.get("title"), "series": row.get("series"),
            "year": row.get("year"), "season": row.get("season"),
            "episode": row.get("episode"), "episode_title": row.get("episode_title"),
            "quality": row.get("quality"), "resolution": row.get("resolution"),
            "codec": row.get("video_codec"), "tmdbid": row.get("tmdb_id")}


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _move_sidecars(old_path: str, new_path: str) -> int:
    """Bring same-stem sidecars (nfo/thumb/subs incl. language-tagged .srt)
    along with the video. Best-effort; returns how many moved."""
    old_stem = os.path.splitext(old_path)[0]
    new_stem = os.path.splitext(new_path)[0]
    moved = 0
    try:
        names = os.listdir(os.path.dirname(old_path) or ".")
    except OSError:
        return 0
    base = os.path.basename(old_stem)
    for n in names:
        full = os.path.join(os.path.dirname(old_path), n)
        if full == old_path or not n.startswith(base):
            continue
        rest = n[len(base):]
        # sidecars share the stem plus "." or "-thumb" and a known suffix —
        # language subs like ".en.srt" pass via the .srt suffix; anything else
        # sharing the stem (an unrelated file) is left alone
        if not (rest.startswith(".") or rest.startswith("-thumb")):
            continue
        if not any(rest.endswith(sfx) for sfx in _SIDECAR_SUFFIXES):
            continue
        try:
            shutil.move(full, new_stem + rest)
            moved += 1
        except OSError:   # noqa: PERF203 - per-sidecar resilience
            continue
    return moved


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
    finding_types = ("naming_mismatch",)

    def estimate_scope(self, context: JobContext) -> int:
        return len(context.db.repair_library_files() or [])

    def scan(self, context: JobContext) -> JobResult:
        from core.video import organization
        from core.video.path_resolver import resolve_video_file_path, video_base_dirs
        result = JobResult()
        settings = organization.load(context.db)
        base_dirs = video_base_dirs(context.db)
        roots = {"movie": str(context.db.get_setting("movies_path") or "").strip(),
                 "episode": str(context.db.get_setting("tv_path") or "").strip()}
        rows = context.db.repair_library_files() or []
        context.report(total=len(rows), phase="checking names")
        valid = []
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=r.get("title"))
            root = roots.get(r["scope"])
            if not root:
                result.skipped += 1          # that library has no configured folder
                continue
            real = resolve_video_file_path(r.get("relative_path"), base_dirs,
                                           size_bytes=r.get("size_bytes"))
            if not real:
                result.skipped += 1          # can't locate locally — never guess
                continue
            ext = os.path.splitext(real)[1]
            expected = organization.render_path(r["scope"], root, _fields_of(r),
                                                settings, ext)["path"]
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
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "naming_mismatch", valid)
        return result

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        d = finding.get("details") or {}
        cur, want = d.get("current_path"), d.get("expected_path")
        if not cur or not want:
            return {"success": False, "error": "finding has no paths"}
        if not os.path.isfile(cur):
            return {"success": False, "error": "the file moved since the scan — re-run it"}
        if os.path.exists(want):
            return {"success": False, "error": "destination already exists — nothing overwritten"}
        try:
            os.makedirs(os.path.dirname(want) or ".", exist_ok=True)
            shutil.move(cur, want)
        except OSError as e:
            return {"success": False, "error": f"rename failed: {e}"}
        moved = _move_sidecars(cur, want)
        # tidy an emptied folder (only ever the file's own former dir)
        try:
            old_dir = os.path.dirname(cur)
            if old_dir and not os.listdir(old_dir):
                os.rmdir(old_dir)
        except OSError:
            pass
        extra = f" (+{moved} sidecar{'s' if moved != 1 else ''})" if moved else ""
        return {"success": True, "action": "renamed",
                "message": f"Renamed to {os.path.basename(want)}{extra}"}
