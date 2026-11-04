"""Media Info job: the audio and subtitle languages of every library file.

Thibs asked for the audio language beside the subtitles and resolution. A
jellyfin scan sends every track's language; a plex library listing sends no
stream data at all, and files that were in the library before soulsync read
languages have none recorded. This job reads them with ffprobe, a batch per
run, biggest files first, and keeps them (video_file_probes) so the next
server scan fills them back in. Read only: it never touches a file.

A file it can't find locally is skipped and tried again next run; a file it
read with no language tags is recorded as such and never read again.
"""

from __future__ import annotations

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video.repair.media_info")


@register_job
class MediaInfoJob(VideoRepairJob):
    job_id = "media_info"
    display_name = "Media Info"
    description = "Reads each file's audio and subtitle languages with ffprobe."
    help_text = ("Reads the audio and embedded subtitle languages of library files the "
                 "media server didn't report them for (Plex never does in a library "
                 "listing), so every file shows its languages and the $audiolangs / "
                 "$sublangs naming tokens can be used. Read only, a batch per run, "
                 "biggest files first. Needs ffprobe.")
    icon = "🎧"
    default_enabled = True
    default_interval_hours = 12
    default_settings = {"files_per_run": 300}
    setting_options = {"files_per_run": [100, 300, 1000, 3000]}
    auto_fix = False
    finding_types = ()

    def estimate_scope(self, context: JobContext) -> int:
        return context.db.media_files_unprobed_count()

    def scan(self, context: JobContext, *, prober=None) -> JobResult:
        from core.video import mediainfo
        from core.video.path_resolver import resolve_video_file_path, video_base_dirs
        result = JobResult()
        probe = prober or mediainfo.probe
        if prober is None and not mediainfo.ffprobe_available():
            logger.info("media info: ffprobe isn't installed, nothing to read")
            return result
        try:
            per_run = int(context.settings.get("files_per_run", 300))
        except (TypeError, ValueError):
            per_run = 300
        rows = context.db.media_files_unprobed(per_run)
        base_dirs = video_base_dirs(context.db)
        context.report(total=len(rows), phase="reading languages")
        for i, r in enumerate(rows, 1):
            context.check_stop()
            result.scanned += 1
            label = r.get("title") or "?"
            if r.get("season") is not None:
                label = "%s S%02dE%02d" % (label, r.get("season") or 0, r.get("episode") or 0)
            context.report(processed=i, current_item=label)
            real = resolve_video_file_path(r.get("relative_path"), base_dirs,
                                           size_bytes=r.get("size_bytes"))
            if not real:
                result.skipped += 1          # not reachable from here: try again next run
                continue
            try:
                info = probe(real)
            except Exception:   # noqa: BLE001 - one unreadable file never stops the run
                info = None
            if not info or not info.get("ok"):
                result.skipped += 1          # unreadable right now: try again next run
                continue
            if context.db.record_file_probe(r.get("relative_path") or real,
                                            r.get("size_bytes"), info):
                result.auto_fixed += 1
            else:
                result.errors += 1
        return result
