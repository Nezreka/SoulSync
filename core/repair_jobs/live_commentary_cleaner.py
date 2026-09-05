"""Live/Commentary Cleaner Job — finds live, commentary, and interview content in the library."""

import re
from core.library2.maintenance_subjects import (
    active_file_subjects,
    count_active_files,
    subject_details,
)
from collections import defaultdict

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, scoped_file_subjects
from utils.logging_config import get_logger

logger = get_logger("repair_job.live_commentary_cleaner")

# Keywords that indicate unwanted content types
# Each tuple: (keyword, content_type_label)
#
# Live patterns require clear recording context — the bare `\blive\b` was
# too loose and falsely flagged verb uses like "What We Live For" by
# American Authors or "Live Forever" by Oasis.
_CONTENT_PATTERNS = [
    # Live
    (r'[\(\[]live\b', 'live'),                       # (Live), [Live at ...]
    (r'-\s*live\b', 'live'),                         # Song - Live
    (r'\blive (at|from|in|on|version|session|recording|performance|album|show|tour|concert|edit|cut|take)\b', 'live'),
    (r'\bin concert\b', 'live'),
    (r'\bconcert\b', 'live'),
    (r'\bon stage\b', 'live'),
    (r'\bunplugged\b', 'live'),
    # Commentary
    (r'\bcommentary\b', 'commentary'),
    (r'\bcommented\b', 'commentary'),
    (r'\btrack.?by.?track\b', 'commentary'),
    # Interview
    (r'\binterview\b', 'interview'),
    (r'\binterlude\b', 'interview'),
    (r'\bskit\b', 'interview'),
    # Spoken word
    (r'\bspoken\s*word\b', 'spoken_word'),
    (r'\bnarrat(?:ion|ed)\b', 'spoken_word'),
    (r'\bintroduction\b', 'spoken_word'),
    # Acappella
    (r'\ba\s*cappella\b', 'acappella'),
    (r'\bacappella\b', 'acappella'),
]


def _detect_content_type(title, album_title=''):
    """Check title and album for unwanted content keywords. Returns content_type or None."""
    combined = f"{title} {album_title}".lower()
    for pattern, content_type in _CONTENT_PATTERNS:
        if re.search(pattern, combined):
            return content_type
    return None


def _format_type(content_type):
    """Format content type for display."""
    return {
        'live': 'Live',
        'commentary': 'Commentary',
        'interview': 'Interview/Skit',
        'spoken_word': 'Spoken Word',
    }.get(content_type, content_type.title())


@register_job
class LiveCommentaryCleanerJob(RepairJob):
    job_id = 'live_commentary_cleaner'
    display_name = 'Live/Commentary Cleaner'
    description = 'Finds live performances, commentary, interviews, and spoken word content'
    help_text = (
        'Scans your library for tracks and albums that contain live performances, '
        'commentary tracks, interviews, skits, or spoken word content based on '
        'title keywords.\n\n'
        'You can configure which content types to flag using the settings below. '
        'Each finding shows the track, its content type, and the matched keyword.\n\n'
        'Fix action: removes the track from the database and deletes the file. '
        'If all tracks in an album are removed, the empty album is also cleaned up.\n\n'
        'Settings:\n'
        '- Flag Live: Flag live performances and concert recordings\n'
        '- Flag Commentary: Flag commentary and track-by-track content\n'
        '- Flag Interviews: Flag interviews, skits, and interludes\n'
        '- Flag Spoken Word: Flag spoken word, narration, and introductions\n'
        '- Scan Album Titles: Also check album titles (catches "Live at Wembley" albums)\n'
        '- Scope: "tracks" flags individual tracks, "albums" flags entire albums with matching titles'
    )
    icon = 'repair-icon-filter'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'flag_live': True,
        'flag_commentary': True,
        'flag_interviews': True,
        'flag_spoken_word': True,
        'scan_album_titles': True,
        'scope': 'tracks',  # 'tracks' or 'albums'
    }
    auto_fix = False
    supports_file_scope = True

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        return count_active_files(context.db, context.config_manager)

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        enabled_types = {
            content_type
            for content_type, key in (
                ("live", "flag_live"),
                ("commentary", "flag_commentary"),
                ("interview", "flag_interviews"),
                ("spoken_word", "flag_spoken_word"),
            )
            if settings.get(key, True)
        }
        if not enabled_types:
            return result
        scan_album_titles = settings.get("scan_album_titles", True)
        subjects = scoped_file_subjects(context, active_file_subjects(context.db, context.config_manager))
        for subject in subjects:
            if context.check_stop():
                return result
            result.scanned += 1
            content_type = _detect_content_type(subject.get("title"), "")
            album_matched = False
            if not content_type and scan_album_titles:
                content_type = _detect_content_type("", subject.get("album_title"))
                album_matched = bool(content_type)
            if not content_type or content_type not in enabled_types:
                continue
            album_id = int(subject["album_id"])
            type_label = _format_type(content_type)
            details = {
                "track": {
                    "id": f"lib2:{subject['track_id']}",
                    "title": subject.get("title"),
                    "artist": subject.get("artist_name") or "",
                    "album": subject.get("album_title") or "",
                    "album_id": f"lib2:{album_id}",
                    "album_type": subject.get("album_type") or "",
                    "file_path": subject.get("path"),
                    "bitrate": subject.get("bitrate"),
                    "duration": subject.get("duration"),
                    "track_number": subject.get("track_number"),
                },
                "content_type": content_type,
                "type_label": type_label,
                "album_matched": album_matched,
                "album_thumb_url": subject.get("album_image"),
                "artist_thumb_url": subject.get("artist_image"),
                "artist_id": subject.get("artist_id"),
            }
            details.update(subject_details(subject))
            if context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="unwanted_content",
                    severity="info",
                    entity_type="track",
                    entity_id=f"lib2:{subject['track_id']}",
                    file_path=subject.get("path"),
                    title=(
                        f'{type_label}: {subject.get("title")} by '
                        f'{subject.get("artist_name") or "Unknown"}'
                    ),
                    description=(
                        f'{type_label} content detected in '
                        f'{"album" if album_matched else "track"} metadata.'
                    ),
                    details=details,
                )
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
        return result

