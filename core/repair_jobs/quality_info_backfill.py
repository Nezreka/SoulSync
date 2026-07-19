"""Quality Info Backfill — re-measures real audio quality for already
imported Library-v2 files whose bitrate/sample-rate/bit-depth was never
actually probed (review A4, clarified: the gap is library entries with no
quality info shown, not files with no DB row at all — that's a separate,
narrower problem the review also flagged but which isn't handled here).

``core.library2.scan`` explains the root cause directly: "The importer seeds
file rows from the legacy DB, which only reliably knows format+bitrate."
Sample-rate and bit-depth — the facts hi-res/lossless quality tiers actually
need — only get filled in when someone manually presses "Refresh & Scan" for
that one album/artist. A track imported before that habit existed, or
downloaded through a path that skipped the probe, keeps NULL sample_rate/
bit_depth forever and its quality badge/tier can never be judged correctly.

This job finds exactly those rows and re-probes them with the same
ground-truth reader "Refresh & Scan" uses
(``core.library2.scan.rescan_files`` -> ``core.imports.file_ops
.probe_audio_quality``, mutagen), just swept across the whole library on a
schedule instead of requiring N manual per-album clicks. Purely additive:
``rescan_files`` only fills currently-NULL columns (COALESCE), never
overwrites a good measured value, so running this is always safe — auto_fix
because there's nothing to review, only real quality facts get written.

No-op when ``features.library_v2`` is off. Never touches files.
"""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.quality_info_backfill")

# Bit depth is only meaningful for lossless containers — a lossy file (mp3,
# aac, ogg, opus, wma) legitimately has NULL bit_depth forever, that must
# never be mistaken for "never probed".
_LOSSLESS_FORMATS = ("flac", "wav", "aiff", "alac", "dsf", "dff", "ape", "wv")

_CANDIDATE_QUERY = f"""
    SELECT id FROM lib2_track_files
     WHERE path IS NOT NULL AND path <> ''
       AND COALESCE(file_state,'active') = 'active'
       AND (
            sample_rate IS NULL
         OR format IS NULL OR format = ''
         OR (LOWER(format) IN ({','.join('?' for _ in _LOSSLESS_FORMATS)}) AND bit_depth IS NULL)
       )
     ORDER BY id
"""


def _candidate_file_ids(conn) -> list:
    return [row[0] for row in conn.execute(_CANDIDATE_QUERY, _LOSSLESS_FORMATS)]


@register_job
class QualityInfoBackfillJob(RepairJob):
    job_id = "quality_info_backfill"
    display_name = "Quality Info Backfill"
    description = "Re-measures real audio quality for library files whose bitrate/sample-rate/bit-depth was never probed"
    help_text = (
        "Some library entries — usually older imports, or ones that came in "
        "through a path that skipped the probe — only ever got format and "
        "bitrate from the legacy import, never sample rate or bit depth. "
        "Those facts are exactly what a hi-res/lossless quality badge needs, "
        "so those entries show incomplete or misleading quality info until "
        "someone manually presses 'Refresh & Scan' for that album.\n\n"
        "This job finds every file still missing that data and re-probes it "
        "with the same ground-truth reader Refresh & Scan uses (mutagen), so "
        "the whole library catches up automatically instead of one album at "
        "a time. Purely additive — it only fills in currently-missing "
        "values, never overwrites a good measured one, so there is nothing "
        "to review. Does nothing when the new library feature is off."
    )
    icon = "repair-icon-quality"
    default_enabled = True
    default_interval_hours = 24
    default_settings = {}
    auto_fix = True  # queueing IS the fix; only real measured facts get written

    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                return len(_candidate_file_ids(conn))
            finally:
                conn.close()
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        try:
            if context.config_manager.get("features.library_v2", True) is not True:
                return result
        except Exception:
            return result

        from core.library2.scan import rescan_files

        conn = context.db._get_connection()
        try:
            file_ids = _candidate_file_ids(conn)
        except Exception as e:  # noqa: BLE001
            logger.error("Quality info backfill: candidate query failed: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            conn.close()

        total = len(file_ids)
        if context.update_progress:
            context.update_progress(0, total)
        if not file_ids:
            return result

        # Small batches so stop/pause requests take effect quickly and
        # progress is visible on a library-wide first run.
        batch = 200
        updated = 0
        for start in range(0, total, batch):
            if context.check_stop() or context.wait_if_paused():
                break
            chunk = file_ids[start:start + batch]
            stats = rescan_files(context.db, file_ids=chunk)
            updated += stats.get("updated", 0)
            result.scanned += stats.get("scanned", 0) + stats.get("missing", 0)
            if context.update_progress:
                context.update_progress(min(start + batch, total), total)
            if context.report_progress:
                context.report_progress(
                    scanned=min(start + batch, total), total=total,
                    phase=f"Probing {min(start + batch, total)} / {total}",
                    log_line=f"{updated} file(s) updated so far",
                    log_type="info",
                )

        result.auto_fixed = updated
        logger.info("Quality info backfill: %d candidates, %d updated",
                     total, updated)
        return result
