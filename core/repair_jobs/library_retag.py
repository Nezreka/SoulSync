"""Library Re-tag — find files whose embedded tags disagree with the catalogue.

This job existed on the legacy library and read ``albums``/``artists``/
``tracks``. Library v2 removed those tables, and its replacement engine
(``core/library2/retag.py``) came back as an interactive dialog only: no
schedule, no findings, no dry-run-then-apply. Nothing noticed tag drift on its
own any more — you had to open an album and look.

So the job returns, on top of that same engine rather than beside it. Two
things it does differently from the version it replaces:

* **Scoped.** The old scan walked the whole albums table every run, with no
  narrowing at all. That is exactly how "run this for one artist" produced
  library-wide findings that were one Fix All away from touching everything
  else, so this one honours the file allowlist
  (``supports_file_scope``/``scoped_file_subjects``).
* **Honest about hand-set fields.** lib2 keeps a per-field user override layer
  and the engine now projects it, so a title someone corrected IS the value
  that gets written. Where the catalogue wanted something else, the finding
  carries BOTH — the fix takes the hand-set value unless the user explicitly
  releases the field (``fix_action='overwrite_manual'``).

Dry run by design: the scan only ever creates findings. Nothing touches a file
until one is applied.
"""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, scoped_file_subjects
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.library_retag")

#: Preview batch size. The engine's own MAX_TRACKS bounds one query; this
#: bounds how much file I/O happens between two stop checks, so a cancelled
#: run stops promptly on a large library.
_BATCH = 100


def _describe(entry: dict, manual_fields: list) -> str:
    changed = [d.get("field") for d in entry.get("diff") or [] if d.get("field")]
    head = (f"{len(changed)} tag field(s) differ from the library: "
            f"{', '.join(changed[:6])}")
    if len(changed) > 6:
        head += f" and {len(changed) - 6} more"
    if manual_fields:
        head += (f". {', '.join(manual_fields)} was set by hand — applying keeps "
                 "your value unless you choose to overwrite it")
    return head + "."


@register_job
class LibraryRetagJob(RepairJob):
    job_id = "library_retag"
    display_name = "Library Re-tag"
    description = "Finds files whose embedded tags are behind the library's metadata"
    help_text = (
        "Compares the tags written in each audio file against what Library v2 "
        "holds for that track, and reports every field that differs.\n\n"
        "The scan never writes. Applying a finding writes the library's values "
        "into the file — the same engine the Re-tag dialog uses, so the preview "
        "and the finding agree.\n\n"
        "A field you edited by hand wins by default. Where the catalogue wanted "
        "something else, the finding shows both values, and you can release that "
        "field deliberately."
    )
    icon = "repair-icon-retag"
    default_enabled = False
    default_interval_hours = 168
    default_settings = {}
    auto_fix = False
    supports_file_scope = True
    # Moves/rewrites real library files, so a LIVE run is refused when the
    # preflight says this process cannot see the library (upstream 36f13cdd8).
    writes_library_files = True

    def _subjects(self, context: JobContext) -> list:
        from core.library2.maintenance_subjects import active_file_subjects

        return scoped_file_subjects(context, active_file_subjects(
            context.db, context.config_manager,
        ))

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(self._subjects(context))
        except Exception:  # noqa: BLE001 - an estimate is never worth failing on
            return 0

    def scan(self, context: JobContext) -> JobResult:
        from core.library2 import retag

        result = JobResult()
        try:
            subjects = self._subjects(context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Library Re-tag] subject enumeration failed: %s", exc)
            result.errors += 1
            return result

        by_track = {int(s["track_id"]): s for s in subjects if s.get("track_id")}
        track_ids = list(by_track)
        total = len(track_ids)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(
                phase=f"Comparing tags for {total} files...", total=total)

        done = 0
        for start in range(0, total, _BATCH):
            if context.check_stop():
                return result
            if context.wait_if_paused():
                return result
            batch = track_ids[start:start + _BATCH]
            conn = context.db._get_connection()
            try:
                contexts = retag.track_contexts(conn, batch)
            finally:
                conn.close()
            for entry in retag.tag_preview(contexts):
                done += 1
                result.scanned += 1
                subject = by_track.get(int(entry.get("track_id") or 0)) or {}
                if entry.get("error"):
                    # A finding promises a fix. Nothing can be written to a file
                    # that cannot be read, so raising one would create a row
                    # whose button could only ever fail.
                    logger.debug("[Library Re-tag] %s: %s",
                                 entry.get("file_path"), entry["error"])
                    result.errors += 1
                    continue
                if not entry.get("has_changes"):
                    continue
                diff = entry.get("diff") or []
                manual_fields = [d.get("field") for d in diff if d.get("manual")]
                artist = subject.get("artist_name") or "Unknown"
                title = entry.get("title") or subject.get("title") or "Unknown"
                if not context.create_finding:
                    continue
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type="library_retag",
                        severity="info",
                        entity_type="track",
                        entity_id=f"lib2:{entry['track_id']}",
                        file_path=entry.get("file_path"),
                        title=f"Tags out of date: {artist} - {title}",
                        description=_describe(entry, manual_fields),
                        details={
                            "track_id": entry["track_id"],
                            "title": title,
                            "artist": artist,
                            "album": entry.get("album_title"),
                            "diff": diff,
                            "has_manual_conflict": bool(entry.get("has_manual_conflict")),
                            "manual_fields": manual_fields,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[Library Re-tag] create finding failed for %s: %s",
                                 entry.get("track_id"), exc)
                    result.errors += 1
                    continue
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
            if context.update_progress:
                context.update_progress(done, total)
        return result
