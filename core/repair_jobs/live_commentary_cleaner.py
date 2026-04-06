"""Live/Commentary Cleaner Job — finds live, commentary, and interview content in the library."""

import re
from collections import defaultdict

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.live_commentary_cleaner")

# Keywords that indicate unwanted content types
# Each tuple: (keyword, content_type_label)
_CONTENT_PATTERNS = [
    # Live
    (r'\blive\b', 'live'),
    (r'\blive at\b', 'live'),
    (r'\blive from\b', 'live'),
    (r'\blive in\b', 'live'),
    (r'\bin concert\b', 'live'),
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

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        enabled_types = set()
        if settings.get('flag_live', True):
            enabled_types.add('live')
        if settings.get('flag_commentary', True):
            enabled_types.add('commentary')
        if settings.get('flag_interviews', True):
            enabled_types.add('interview')
        if settings.get('flag_spoken_word', True):
            enabled_types.add('spoken_word')

        if not enabled_types:
            return result

        scan_album_titles = settings.get('scan_album_titles', True)
        scope = settings.get('scope', 'tracks')

        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name, al.title, al.id, al.record_type,
                       t.file_path, t.bitrate, t.duration, t.track_number,
                       al.thumb_url, ar.thumb_url
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.title IS NOT NULL AND t.title != ''
                  AND t.file_path IS NOT NULL AND t.file_path != ''
            """)
            tracks = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching tracks: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        if not tracks:
            return result

        total = len(tracks)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Scanning {total} tracks...', total=total)

        # Track which albums we've already flagged (for album scope)
        flagged_album_ids = set()

        for idx, row in enumerate(tracks):
            if context.check_stop():
                return result

            result.scanned += 1

            if idx % 200 == 0:
                if context.report_progress:
                    context.report_progress(
                        scanned=idx, total=total,
                        phase=f'Scanning {idx} / {total}',
                        log_line=f'Checking: {row[1]}',
                        log_type='info'
                    )
                if context.update_progress:
                    context.update_progress(idx, total)

            (track_id, title, artist_name, album_title, album_id,
             album_type, file_path, bitrate, duration, track_number,
             album_thumb, artist_thumb) = row

            # Check track title
            content_type = _detect_content_type(title, '')

            # Check album title if enabled and track title didn't match
            album_matched = False
            if not content_type and scan_album_titles and album_title:
                content_type = _detect_content_type('', album_title)
                if content_type:
                    album_matched = True

            if not content_type:
                continue

            # Skip if this content type isn't enabled
            if content_type not in enabled_types:
                continue

            # Album scope: flag once per album, not per track
            if scope == 'albums' and album_matched and album_id:
                if album_id in flagged_album_ids:
                    continue
                flagged_album_ids.add(album_id)

            type_label = _format_type(content_type)
            match_source = f'album "{album_title}"' if album_matched else f'track "{title}"'

            if context.create_finding:
                try:
                    context.create_finding(
                        job_id=self.job_id,
                        finding_type='unwanted_content',
                        severity='info',
                        entity_type='album' if (scope == 'albums' and album_matched) else 'track',
                        entity_id=str(album_id if (scope == 'albums' and album_matched) else track_id),
                        file_path=file_path,
                        title=f'{type_label}: {title} by {artist_name or "Unknown"}',
                        description=(
                            f'{type_label} content detected in {match_source}. '
                            f'Album: "{album_title or "Unknown"}" ({album_type or "unknown"} type).'
                        ),
                        details={
                            'track': {
                                'id': track_id,
                                'title': title,
                                'artist': artist_name or '',
                                'album': album_title or '',
                                'album_id': album_id,
                                'album_type': album_type or '',
                                'file_path': file_path,
                                'bitrate': bitrate,
                                'duration': duration,
                                'track_number': track_number,
                            },
                            'content_type': content_type,
                            'type_label': type_label,
                            'album_matched': album_matched,
                            'album_thumb_url': album_thumb or None,
                            'artist_thumb_url': artist_thumb or None,
                        }
                    )
                    result.findings_created += 1
                except Exception as e:
                    logger.debug("Error creating finding: %s", e)
                    result.errors += 1

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Live/Commentary cleaner: scanned %d tracks, found %d",
                     result.scanned, result.findings_created)
        return result
