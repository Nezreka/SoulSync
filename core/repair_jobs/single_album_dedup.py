"""Single/Album Deduplicator Job — flags singles that also exist on albums in the library."""

import re
from collections import defaultdict
from difflib import SequenceMatcher

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.single_album_dedup")


@register_job
class SingleAlbumDedupJob(RepairJob):
    job_id = 'single_album_dedup'
    display_name = 'Single/Album Dedup'
    description = 'Finds singles that are redundant because the same track exists on an album'
    help_text = (
        'Scans your library for tracks that belong to a single or EP release where the '
        'same song (by title and artist) also exists on a full album. In most cases, the '
        'album version is preferred because it has correct track numbering and keeps your '
        'library organized.\n\n'
        'Each finding shows the single track and the album track it matches so you can '
        'decide whether to remove the single copy.\n\n'
        'Fix action: removes the single/EP version from the database and deletes the file, '
        'keeping the album version.\n\n'
        'Settings:\n'
        '- Title Similarity: How closely titles must match (0.0 - 1.0)\n'
        '- Artist Similarity: How closely artist names must match (0.0 - 1.0)'
    )
    icon = 'repair-icon-duplicate'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'title_similarity': 0.85,
        'artist_similarity': 0.80,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        title_threshold = settings.get('title_similarity', 0.85)
        artist_threshold = settings.get('artist_similarity', 0.80)

        # Fetch all tracks with album type info
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name, al.title, al.record_type, al.track_count,
                       t.file_path, t.bitrate, t.duration, al.thumb_url, ar.thumb_url,
                       t.track_number
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

        if context.report_progress:
            context.report_progress(phase=f'Categorizing {total} tracks...', total=total)

        # Separate tracks into singles/EPs and album tracks
        singles = []
        album_tracks = []

        for row in tracks:
            (track_id, title, artist_name, album_title, album_type,
             total_track_count, file_path, bitrate, duration,
             album_thumb, artist_thumb, track_number) = row

            entry = {
                'id': track_id,
                'title': title,
                'norm_title': _normalize(title),
                'artist': artist_name or '',
                'norm_artist': _normalize(artist_name or ''),
                'album': album_title or '',
                'album_type': (album_type or '').lower(),
                'total_tracks': total_track_count or 0,
                'file_path': file_path,
                'bitrate': bitrate,
                'duration': duration,
                'track_number': track_number,
                'album_thumb_url': album_thumb or None,
                'artist_thumb_url': artist_thumb or None,
            }

            # Classify: single = album_type 'single' or total_tracks <= 3
            # EPs (4-6 tracks) are borderline — only flag as "single" if explicitly typed
            is_single = (
                entry['album_type'] == 'single' or
                (entry['album_type'] == 'ep' and entry['total_tracks'] <= 6) or
                (entry['album_type'] not in ('album', 'compilation') and entry['total_tracks'] <= 3)
            )
            if is_single:
                singles.append(entry)
            elif entry['total_tracks'] > 3:
                album_tracks.append(entry)

        logger.info("Single/Album dedup: %d singles/EPs, %d album tracks", len(singles), len(album_tracks))

        if not singles or not album_tracks:
            result.scanned = total
            return result

        # Bucket album tracks by first 4 chars of normalized title for fast lookup
        album_buckets = defaultdict(list)
        for at in album_tracks:
            key = at['norm_title'][:4] if len(at['norm_title']) >= 4 else at['norm_title']
            album_buckets[key].append(at)

        # For each single, check if a matching album track exists
        flagged_single_ids = set()

        if context.report_progress:
            context.report_progress(phase=f'Checking {len(singles)} singles against albums...', total=len(singles))

        for idx, single in enumerate(singles):
            if context.check_stop():
                return result

            result.scanned += 1

            if idx % 100 == 0:
                if context.report_progress:
                    context.report_progress(
                        scanned=idx, total=len(singles),
                        phase=f'Checking {idx} / {len(singles)}',
                        log_line=f'Checking: {single["title"]} — {single["artist"]}',
                        log_type='info'
                    )
                if context.update_progress:
                    context.update_progress(idx, len(singles))

            if single['id'] in flagged_single_ids:
                continue

            # Look in matching bucket
            bucket_key = single['norm_title'][:4] if len(single['norm_title']) >= 4 else single['norm_title']
            candidates = album_buckets.get(bucket_key, [])

            best_album_match = None
            best_sim = 0

            single_version = _extract_version_tag(single['title'])

            for album_t in candidates:
                # Skip if version tags differ (e.g. live single vs studio album)
                album_version = _extract_version_tag(album_t['title'])
                if single_version != album_version:
                    continue

                # Compare titles
                title_sim = SequenceMatcher(None, single['norm_title'], album_t['norm_title']).ratio()
                if title_sim < title_threshold:
                    continue

                # Compare artists
                artist_sim = SequenceMatcher(None, single['norm_artist'], album_t['norm_artist']).ratio()
                if artist_sim < artist_threshold:
                    continue

                combined = (title_sim + artist_sim) / 2
                if combined > best_sim:
                    best_sim = combined
                    best_album_match = album_t

            if best_album_match:
                flagged_single_ids.add(single['id'])

                if context.report_progress:
                    context.report_progress(
                        log_line=f'Redundant single: {single["title"]} — also on "{best_album_match["album"]}"',
                        log_type='skip'
                    )

                if context.create_finding:
                    try:
                        context.create_finding(
                            job_id=self.job_id,
                            finding_type='single_album_redundant',
                            severity='info',
                            entity_type='track',
                            entity_id=str(single['id']),
                            file_path=single['file_path'],
                            title=f'Redundant single: {single["title"]} by {single["artist"]}',
                            description=(
                                f'"{single["title"]}" exists as a {single["album_type"] or "single"} '
                                f'on "{single["album"]}" but also appears on album '
                                f'"{best_album_match["album"]}" (track #{best_album_match.get("track_number", "?")})'
                            ),
                            details={
                                'single_track': {
                                    'id': single['id'],
                                    'title': single['title'],
                                    'artist': single['artist'],
                                    'album': single['album'],
                                    'album_type': single['album_type'],
                                    'file_path': single['file_path'],
                                    'bitrate': single['bitrate'],
                                    'duration': single['duration'],
                                },
                                'album_track': {
                                    'id': best_album_match['id'],
                                    'title': best_album_match['title'],
                                    'artist': best_album_match['artist'],
                                    'album': best_album_match['album'],
                                    'album_type': best_album_match['album_type'],
                                    'file_path': best_album_match['file_path'],
                                    'bitrate': best_album_match['bitrate'],
                                    'duration': best_album_match['duration'],
                                    'track_number': best_album_match.get('track_number'),
                                },
                                'album_thumb_url': best_album_match.get('album_thumb_url') or single.get('album_thumb_url'),
                                'artist_thumb_url': best_album_match.get('artist_thumb_url') or single.get('artist_thumb_url'),
                            }
                        )
                        result.findings_created += 1
                    except Exception as e:
                        logger.debug("Error creating finding: %s", e)
                        result.errors += 1

        if context.update_progress:
            context.update_progress(len(singles), len(singles))

        logger.info("Single/Album dedup: %d singles checked, %d redundant singles found",
                     result.scanned, result.findings_created)
        return result

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged


_VERSION_KEYWORDS = re.compile(
    r'(live|acoustic|remix|demo|instrumental|radio edit|extended|karaoke|a\s?cappella)',
    re.IGNORECASE,
)


def _extract_version_tag(text: str) -> str:
    """Return a lowercase version keyword if the title contains one, else empty string."""
    m = _VERSION_KEYWORDS.search(text)
    return m.group(1).lower().strip() if m else ''


def _normalize(text: str) -> str:
    """Normalize text for fuzzy comparison."""
    t = text.lower()
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'[^a-z0-9 ]', '', t)
    return t.strip()
