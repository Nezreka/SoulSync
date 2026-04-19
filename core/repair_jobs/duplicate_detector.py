"""Duplicate Track Detector Job — finds potential duplicate tracks in the library."""

import re
from collections import defaultdict
from difflib import SequenceMatcher

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.duplicates")


@register_job
class DuplicateDetectorJob(RepairJob):
    job_id = 'duplicate_detector'
    display_name = 'Duplicate Detector'
    description = 'Finds potential duplicate tracks in your library'
    help_text = (
        'Groups tracks by similar title and artist name using fuzzy matching, then flags '
        'groups where multiple copies exist. This helps you find accidental duplicates '
        'from re-downloads, compilation albums, or similar-titled tracks.\n\n'
        'Each duplicate group is reported as a finding with details about every copy '
        '(file path, format, bitrate) so you can decide which to keep.\n\n'
        'Settings:\n'
        '- Title Similarity: How closely titles must match to be considered duplicates (0.0 - 1.0)\n'
        '- Artist Similarity: How closely artist names must match (0.0 - 1.0)\n'
        '- Ignore Cross-Album: When enabled, tracks on different albums are not flagged as duplicates. '
        'Turn this OFF if you have duplicate downloads filed under different album entries — '
        'this is the most common cause of missed duplicates from re-downloads'
    )
    icon = 'repair-icon-duplicate'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'title_similarity': 0.85,
        'artist_similarity': 0.80,
        'ignore_cross_album': False,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        title_threshold = float(settings.get('title_similarity', 0.85))
        artist_threshold = float(settings.get('artist_similarity', 0.80))
        ignore_cross_album = settings.get('ignore_cross_album', True)

        # Respect the global "allow duplicate tracks across albums" setting —
        # if the user explicitly allows duplicates across albums, never flag them
        if context.config_manager and context.config_manager.get('library.allow_duplicate_tracks', False):
            ignore_cross_album = True

        # Fetch all tracks with artist/album names via JOIN
        tracks = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name, al.title, t.file_path,
                       t.bitrate, t.duration, al.thumb_url, ar.thumb_url
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.title IS NOT NULL AND t.title != ''
                  AND t.file_path IS NOT NULL AND t.file_path != ''
            """)
            tracks = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching tracks from DB: %s", e, exc_info=True)
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

        # Group tracks by normalized key for fast comparison
        # Bucket by first 4 chars of normalized title for efficiency
        buckets = defaultdict(list)
        for row in tracks:
            track_id, title, artist_name, album_title, file_path, bitrate, duration, album_thumb, artist_thumb = row
            norm_title = _normalize(title)
            bucket_key = norm_title[:4] if len(norm_title) >= 4 else norm_title
            buckets[bucket_key].append({
                'id': track_id,
                'title': title,
                'norm_title': norm_title,
                'artist': artist_name or '',
                'norm_artist': _normalize(artist_name or ''),
                'album': album_title,
                'file_path': file_path,
                'bitrate': bitrate,
                'duration': duration,
                'album_thumb_url': album_thumb or None,
                'artist_thumb_url': artist_thumb or None,
            })

        # Find duplicates within each bucket
        found_groups = set()  # Track IDs already in a group
        processed = 0

        if context.report_progress:
            context.report_progress(phase=f'Comparing {total} tracks...', total=total)

        for bucket_key, bucket_tracks in buckets.items():
            if context.check_stop():
                return result

            for i, t1 in enumerate(bucket_tracks):
                if context.check_stop():
                    return result

                processed += 1
                result.scanned += 1

                if context.report_progress and processed % 100 == 0:
                    context.report_progress(
                        scanned=processed, total=total,
                        phase=f'Comparing {processed} / {total}',
                        log_line=f'Checking: {t1["title"]} — {t1["artist"]}',
                        log_type='info'
                    )

                if t1['id'] in found_groups:
                    continue

                group = [t1]

                for j in range(i + 1, len(bucket_tracks)):
                    t2 = bucket_tracks[j]
                    if t2['id'] in found_groups:
                        continue

                    # Compare titles
                    title_sim = SequenceMatcher(None, t1['norm_title'], t2['norm_title']).ratio()
                    if title_sim < title_threshold:
                        continue

                    # Compare artists
                    artist_sim = SequenceMatcher(None, t1['norm_artist'], t2['norm_artist']).ratio()
                    if artist_sim < artist_threshold:
                        continue

                    # Skip cross-album duplicates — same song on different albums is intentional
                    if ignore_cross_album and t1['album'] and t2['album'] and t1['album'] != t2['album']:
                        continue

                    group.append(t2)

                if len(group) >= 2:
                    # Found a duplicate group
                    for t in group:
                        found_groups.add(t['id'])

                    if context.report_progress:
                        context.report_progress(
                            log_line=f'Duplicate: {t1["title"]} — {len(group)} copies',
                            log_type='skip'
                        )

                    if context.create_finding:
                        try:
                            # Sort group by quality (highest bitrate first)
                            group.sort(key=lambda t: (t['bitrate'] or 0), reverse=True)

                            context.create_finding(
                                job_id=self.job_id,
                                finding_type='duplicate_tracks',
                                severity='info',
                                entity_type='track',
                                entity_id=str(group[0]['id']),
                                file_path=group[0]['file_path'],
                                title=f'Duplicate: {group[0]["title"]} by {group[0]["artist"]}',
                                description=f'{len(group)} copies found with similar title/artist',
                                details={
                                    'tracks': [{
                                        'id': t['id'],
                                        'title': t['title'],
                                        'artist': t['artist'],
                                        'album': t['album'],
                                        'file_path': t['file_path'],
                                        'bitrate': t['bitrate'],
                                        'duration': t['duration'],
                                    } for t in group],
                                    'count': len(group),
                                    'album_thumb_url': group[0].get('album_thumb_url'),
                                    'artist_thumb_url': group[0].get('artist_thumb_url'),
                                }
                            )
                            result.findings_created += 1
                        except Exception as e:
                            logger.debug("Error creating duplicate finding: %s", e)
                            result.errors += 1

            if context.update_progress and processed % 200 == 0:
                context.update_progress(processed, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Duplicate scan: %d tracks checked, %d duplicate groups found",
                     result.scanned, result.findings_created)
        return result

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged


def _normalize(text: str) -> str:
    """Normalize text for fuzzy comparison.

    Keeps parenthetical content (remixes, live, etc.) so that similarity
    thresholds can distinguish 'title' from 'title xxx remix'.
    """
    t = text.lower()
    t = re.sub(r'[^a-z0-9() ]', '', t)
    return t.strip()
