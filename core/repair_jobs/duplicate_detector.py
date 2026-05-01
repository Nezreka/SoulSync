"""Duplicate Track Detector Job — finds potential duplicate tracks in the library."""

import os
import re
from collections import defaultdict
from difflib import SequenceMatcher

from core.imports.file_ops import _strip_slskd_dedup_suffix
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
        processed_holder = {'count': 0}

        if context.report_progress:
            context.report_progress(phase=f'Comparing {total} tracks...', total=total)

        # Pass 1 — bucket by normalized-title prefix (existing behavior).
        for _bucket_key, bucket_tracks in buckets.items():
            if context.check_stop():
                return result
            self._scan_bucket(
                bucket_tracks=bucket_tracks,
                require_metadata_match=True,
                title_threshold=title_threshold,
                artist_threshold=artist_threshold,
                ignore_cross_album=ignore_cross_album,
                found_groups=found_groups,
                processed_holder=processed_holder,
                total=total,
                result=result,
                context=context,
            )

        # Pass 2 — re-bucket leftover tracks by canonical filename stem
        # (slskd dedup suffix stripped). Catches dupes whose tag metadata
        # disagrees because some copies were never properly tagged after
        # download — e.g. ``Song.flac`` and ``Song_<19-digit-ts>.flac``
        # land in the library with identical filenames sans the slskd
        # dedup tail but get inconsistent ID3 titles from the media-server
        # rescan. Pass-1 buckets them apart by title so they never get
        # compared. Discord-reported scenario: 7 copies of one OST track
        # accumulating in one folder, only 1 caught by the detector.
        filename_buckets = self._build_filename_buckets(
            buckets=buckets,
            found_groups=found_groups,
        )
        for _fname_key, fname_tracks in filename_buckets.items():
            if context.check_stop():
                return result
            # Filename match is itself strong evidence — a shared canonical
            # stem means the files came from the same source download.
            # Drop the metadata gates so dedup orphans get caught even
            # when their tag titles disagree.
            self._scan_bucket(
                bucket_tracks=fname_tracks,
                require_metadata_match=False,
                title_threshold=title_threshold,
                artist_threshold=artist_threshold,
                ignore_cross_album=ignore_cross_album,
                found_groups=found_groups,
                processed_holder=processed_holder,
                total=total,
                result=result,
                context=context,
            )

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Duplicate scan: %d tracks checked, %d duplicate groups found",
                     result.scanned, result.findings_created)
        return result

    def _scan_bucket(
        self,
        *,
        bucket_tracks,
        require_metadata_match,
        title_threshold,
        artist_threshold,
        ignore_cross_album,
        found_groups,
        processed_holder,
        total,
        result,
        context,
    ) -> None:
        """Compare every pair within a bucket; emit duplicate groups.

        ``require_metadata_match`` gates the title / artist similarity
        thresholds and the cross-album guard. Pass ``False`` for buckets
        whose grouping is already strong evidence (e.g. shared canonical
        filename) so that dedup orphans with broken / missing tags still
        get caught.
        """
        for i, t1 in enumerate(bucket_tracks):
            if context.check_stop():
                return

            processed_holder['count'] += 1
            result.scanned += 1
            processed = processed_holder['count']

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

                if require_metadata_match:
                    title_sim = SequenceMatcher(None, t1['norm_title'], t2['norm_title']).ratio()
                    if title_sim < title_threshold:
                        continue
                    artist_sim = SequenceMatcher(None, t1['norm_artist'], t2['norm_artist']).ratio()
                    if artist_sim < artist_threshold:
                        continue
                    if ignore_cross_album and t1['album'] and t2['album'] and t1['album'] != t2['album']:
                        continue

                if _is_same_physical_file(
                    t1['file_path'], t2['file_path'],
                    t1['duration'], t2['duration'],
                ):
                    continue

                group.append(t2)

            if len(group) >= 2:
                for t in group:
                    found_groups.add(t['id'])

                if context.report_progress:
                    context.report_progress(
                        log_line=f'Duplicate: {t1["title"]} — {len(group)} copies',
                        log_type='skip'
                    )

                if context.create_finding:
                    try:
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

        if context.update_progress and processed_holder['count'] % 200 == 0:
            context.update_progress(processed_holder['count'], total)

    def _build_filename_buckets(self, *, buckets, found_groups):
        """Re-bucket all tracks by canonical filename stem.

        The slskd dedup suffix (``_<19+ digit timestamp>``) is stripped so
        ``Song.flac`` and ``Song_639122324339578022.flac`` collapse to the
        same key. Singleton buckets (only one track) are dropped — they
        carry no comparison value.
        """
        filename_buckets = defaultdict(list)
        for bucket_tracks in buckets.values():
            for track in bucket_tracks:
                if track['id'] in found_groups:
                    continue
                fp = track.get('file_path') or ''
                if not fp:
                    continue
                basename = os.path.basename(str(fp).replace('\\', '/'))
                stem, ext = os.path.splitext(basename)
                if not stem:
                    continue
                canonical = _strip_slskd_dedup_suffix(stem)
                key = (canonical.lower(), ext.lower())
                filename_buckets[key].append(track)
        return {k: v for k, v in filename_buckets.items() if len(v) >= 2}

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


def _is_same_physical_file(p1, p2, dur1, dur2) -> bool:
    """Detect when two DB rows point at the same file mounted at different paths.

    When a user binds the same host music directory into both SoulSync
    (e.g. ``/app/Transfer``) and a media server like Plex (e.g.
    ``/media/Music``), the SoulSync scan and the media-server library
    sync each create a track row pointing at the same physical file
    via different mount paths. The two rows then look like a fuzzy-
    match duplicate to this job.

    Returns True when:
    - Both paths share the last 3 segments (filename + album + artist
      folder), so they really are the same release on disk;
    - The leading mount-root segments differ, ruling out the case
      where one row is just a re-scan of the other path; and
    - When both rows carry a duration, the durations agree within 1
      second (defensive — different files at parallel paths would
      almost always disagree on duration even slightly).
    """
    if not p1 or not p2:
        return False
    norm1 = str(p1).replace('\\', '/').rstrip('/')
    norm2 = str(p2).replace('\\', '/').rstrip('/')
    parts1 = [x for x in norm1.split('/') if x]
    parts2 = [x for x in norm2.split('/') if x]
    if len(parts1) < 3 or len(parts2) < 3:
        return False
    tail1 = [s.lower() for s in parts1[-3:]]
    tail2 = [s.lower() for s in parts2[-3:]]
    if tail1 != tail2:
        return False
    # Confirm mount roots actually differ, otherwise we'd skip
    # legitimate duplicates that happen to share the trailing path.
    if parts1[:-3] == parts2[:-3]:
        return False
    if dur1 and dur2 and abs(dur1 - dur2) > 1.0:
        return False
    return True
