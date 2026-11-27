"""Loudness job: how loud each video file is, so the player can level it.

read only: ffmpeg ebur128 decodes the first audio track (no video), under
nice 19 / idle io on one thread, and the result is kept in video.db
(core/video/loudness). newest files first, for a set time per run, so a
big library is worked through over a few nights and tonight's episode is
done first. a file that can't be measured is tried 3 times, then left.
"""

from __future__ import annotations

import time

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video.repair.loudness")


@register_job
class LoudnessJob(VideoRepairJob):
    job_id = "loudness"
    display_name = "Loudness"
    description = "Measures how loud each video is, so the player can level very quiet or very loud ones."
    help_text = ("Plays each file's main audio track through ffmpeg's loudness meter (EBU R128) in "
                 "the background, at the lowest priority, and keeps the result in SoulSync's database. "
                 "The player uses it for \"Level video loudness\" (Settings, You, Music). Nothing is "
                 "written into your files. Newest files first; each run stops after its time is up "
                 "and the next run carries on. Needs ffmpeg.")
    icon = "🔊"
    default_enabled = True
    default_interval_hours = 3
    default_settings = {"minutes_per_run": 30}
    setting_options = {"minutes_per_run": [10, 30, 60, 120]}
    auto_fix = False
    finding_types = ()

    def estimate_scope(self, context: JobContext) -> int:
        from core.video import loudness as VL
        conn = context.db._get_connection()
        try:
            return VL.unmeasured_count(conn)
        finally:
            conn.close()

    def scan(self, context: JobContext, *, measurer=None, resolver=None, clock=time.monotonic) -> JobResult:
        from core.video import loudness as VL
        result = JobResult()
        if measurer is None:
            from core.replaygain import is_ffmpeg_available
            if not is_ffmpeg_available():
                logger.info("loudness: ffmpeg isn't installed, nothing to measure")
                return result
        measure = measurer or VL.measure
        if resolver is None:
            from core.video.path_resolver import resolve_video_file_path, video_base_dirs
            base_dirs = video_base_dirs(context.db)

            def resolver(row):
                return resolve_video_file_path(row.get("relative_path"), base_dirs,
                                               size_bytes=row.get("size_bytes"))
        try:
            budget = float(context.settings.get("minutes_per_run", 30)) * 60
        except (TypeError, ValueError):
            budget = 1800.0
        started = clock()
        conn = context.db._get_connection()
        try:
            rows = VL.unmeasured(conn, 500)
        finally:
            conn.close()
        context.report(total=len(rows), phase="measuring loudness")
        for i, r in enumerate(rows, 1):
            context.check_stop()
            if clock() - started > budget:
                break
            result.scanned += 1
            label = r.get("title") or "?"
            if r.get("season") is not None:
                label = "%s S%02dE%02d" % (label, r.get("season") or 0, r.get("episode") or 0)
            context.report(processed=i, current_item=label)
            real = resolver(r)
            if not real:
                result.skipped += 1          # not reachable from here: next run
                continue
            # ffmpeg runs with no connection open
            try:
                measured, error = measure(real), None
            except Exception as e:  # noqa: BLE001 - counted, tried again next run
                measured, error = None, str(e) or e.__class__.__name__
            conn = context.db._get_connection()
            try:
                VL.record(conn, r.get("relative_path") or real, r.get("size_bytes"), measured, error=error)
            finally:
                conn.close()
            if measured is None:
                result.errors += 1
            else:
                result.auto_fixed += 1
        return result
