"""Suspect Album Tag Detector — finds tracks that are probably filed under the
wrong album due to bad embedded tags (e.g. a Daughtry song filed under "Hitzone 43").

Detection signals (any one is sufficient):
  1. The album has exactly 1 locally-owned track AND the album has no cover art.
  2. The album name matches a known compilation / sampler pattern (case-insensitive)
     BUT the track's artist is NOT "Various Artists" or "Various".
  3. The album has exactly 1 locally-owned track AND the DB track_count field says
     the real release has more than 3 tracks (i.e. a lone track inside a big album).

The fix action is "reidentify" — the existing Re-identify modal handles the actual fix.
This job only DETECTS and emits findings; it never modifies files itself.
"""

import re
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, drop_hand_tagged
from utils.logging_config import get_logger

logger = get_logger("repair_job.suspect_album_tag")

# Album name patterns that are almost always compilations / samplers.
# A track by a named artist inside one of these is very likely wrong-tagged.
_COMPILATION_PATTERNS = re.compile(
    r'\b(hitzone|now\s*(?:\d+|that\'?s\s+what\s+i\s+call\s+music)|top\s*\d{2,3}|vol\.?\s*\d+|various\s*artists?|'
    r'best\s+of|greatest\s+hits?|ultimate\s+collection|the\s+collection|'
    r'essential\s+hits?|playlist|soundtrack|ost|karaoke|tribute\s+to|'
    r'years\s+of\s+hits?|summer\s+hits?|chart\s+hits?)\b',
    re.IGNORECASE,
)

_VARIOUS_ARTIST_NAMES = {'various artists', 'various', 'va', 'v.a.', 'v.a'}


def _is_various_artist(name: str) -> bool:
    return (name or '').strip().lower() in _VARIOUS_ARTIST_NAMES


def _owned_track_sql(alias: str) -> str:
    """``alias`` is a track with a live file in the library the run is for."""
    from core.library2.sql_util import owned_sql
    return owned_sql('track', alias)


@register_job
class SuspectAlbumTagDetector(RepairJob):
    job_id = 'suspect_album_tag_detector'
    display_name = 'Suspect Album Tags'
    description = (
        'Finds tracks that are probably filed under the wrong album due to bad '
        'embedded tags (e.g. a single song by a named artist inside a compilation).'
    )
    help_text = (
        'Scans for tracks that look like they ended up in the wrong album.\n\n'
        'Detection signals:\n'
        '- The album has only 1 of your tracks AND no cover art\n'
        '- The album name matches a known compilation pattern (Hitzone, Now, Vol., '
        'Greatest Hits, etc.) but the artist is not "Various Artists"\n'
        '- The album has only 1 of your tracks AND the original release has 4+ tracks\n\n'
        'For each finding, click Re-identify to search for the correct album and '
        'let SoulSync re-file the track automatically.\n\n'
        'Settings:\n'
        '- Min compilation track count: only flag lone-track albums where the full '
        'release has at least this many tracks (default: 4)\n'
        '- Check cover art: flag albums with 1 local track and no cover art (default: on)'
    )
    icon = 'repair-icon-metadata'
    default_enabled = False
    default_interval_hours = 168   # weekly
    default_settings = {
        'min_compilation_track_count': 4,
        'check_missing_art': True,
    }
    auto_fix = False   # findings only; fix via Re-identify modal
    writes_library_files = False

    # ------------------------------------------------------------------ #
    def _get_settings(self, context: JobContext):
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(
            f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        if isinstance(cfg, dict):
            merged.update(cfg)
        return merged

    def _get_setting(self, context: JobContext, key: str, default=None):
        return self._get_settings(context).get(key, default)

    # ------------------------------------------------------------------ #
    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(f"""
                    SELECT COUNT(*) FROM (
                        SELECT al.id
                        FROM lib2_albums al
                        JOIN lib2_tracks t ON t.album_id = al.id
                        WHERE {_owned_track_sql('t')}
                        GROUP BY al.id
                        HAVING COUNT(t.id) = 1
                    )
                """)
                row = cur.fetchone()
                return row[0] if row else 0
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Scope estimate failed: %s", e)
            return 0

    # ------------------------------------------------------------------ #
    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        min_track_count = int(settings.get('min_compilation_track_count', 4))
        check_art = bool(settings.get('check_missing_art', True))

        if context.report_progress:
            context.report_progress(phase='Scanning for suspect album tags…',
                                    log_line='Loading tracks…', log_type='info')

        conn = context.db._get_connection()
        try:
            cur = conn.cursor()
            # Pull every album that has exactly 1 locally-owned track.
            # Include the metadata source's track count so we can tell if the
            # full release is actually bigger. The track's own credit (its
            # primary track artist) next to the album artist gives the
            # track-level identity.
            from core.library2.sql_util import scoped_primary_file_join
            cur.execute(f"""
                SELECT
                    al.id          AS album_id,
                    al.title       AS album_title,
                    al.image_url   AS thumb_url,
                    COALESCE(al.expected_track_count, al.track_count) AS full_track_count,
                    COALESCE(tar.id, aar.id)   AS artist_id,
                    COALESCE(tar.name, aar.name, '') AS artist_name,
                    aar.name       AS album_artist_name,
                    t.id           AS track_id,
                    t.title        AS track_title,
                    f.path         AS file_path
                FROM lib2_albums al
                JOIN lib2_tracks t ON t.album_id = al.id
                JOIN lib2_track_files f ON {scoped_primary_file_join('t', 'f')}
                LEFT JOIN lib2_artists aar ON aar.id = al.primary_artist_id
                LEFT JOIN lib2_artists tar ON tar.id = (
                    SELECT ta.artist_id FROM lib2_track_artists ta
                     WHERE ta.track_id = t.id
                     ORDER BY CASE ta.role WHEN 'primary' THEN 0 ELSE 1 END,
                              ta.position, ta.artist_id
                     LIMIT 1)
                WHERE {_owned_track_sql('t')}
                  AND f.path IS NOT NULL AND f.path != ''
                GROUP BY al.id
                HAVING COUNT(t.id) = 1
                ORDER BY al.title
                LIMIT 2000
            """)
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
        # Library v2 keeps the "tagged it myself" lock on the file: a hand-tagged
        # release is the user's call, never a suspect.
        rows = drop_hand_tagged(context, rows, path_key='file_path')

        total = len(rows)
        if context.report_progress:
            context.report_progress(phase=f'Checking {total} single-track albums…',
                                    total=total, log_line=f'{total} candidates',
                                    log_type='info')

        for i, row in enumerate(rows):
            if context.check_stop():
                break
            if i % 50 == 0 and context.wait_if_paused():
                break

            result.scanned += 1
            if i % 50 == 0 and context.update_progress:
                context.update_progress(i, total)

            album_title   = (row['album_title'] or '').strip()
            artist_name   = (row['artist_name'] or '').strip()
            album_artist  = (row.get('album_artist_name') or '').strip()
            thumb_url     = row['thumb_url']
            full_count    = row['full_track_count'] or 0
            track_id      = row['track_id']
            track_title   = (row['track_title'] or '').strip()

            reasons = []

            # Signal 1: no cover art + lone track
            if check_art and not thumb_url:
                reasons.append('no cover art')

            # Signal 2: compilation-pattern album name OR Various Artists album with named track artist
            is_compilation_title = bool(_COMPILATION_PATTERNS.search(album_title))
            is_va_track = _is_various_artist(artist_name)
            is_va_album = _is_various_artist(album_artist)

            if is_compilation_title and not is_va_track:
                reasons.append(f'album name "{album_title}" looks like a compilation')
            elif is_va_album and not is_va_track:
                reasons.append(f'lone track in Various Artists compilation "{album_title}"')

            # Signal 3: full release has many tracks but we only own 1
            if full_count >= min_track_count:
                reasons.append(
                    f'only 1 of {full_count} tracks locally owned')

            if not reasons:
                result.skipped += 1
                continue

            # Build the description shown in the Repair dashboard card
            reason_text = '; '.join(reasons)
            title = f'Suspect album tag: "{track_title}"'
            description = (
                f'Track "{track_title}" by {artist_name} is the only track in '
                f'album "{album_title}" ({reason_text}). '
                f'It may be tagged with the wrong album.'
            )

            details = {
                'track_id':    track_id,
                'track_title': track_title,
                'album_title': album_title,
                'artist_name': artist_name,
                'album_artist_name': album_artist,
                'reasons':     reasons,
                'full_track_count': full_count,
                'has_cover':   bool(thumb_url),
                'album_thumb_url': thumb_url or '',
                'thumb_url':   thumb_url or '',
                # Pre-fill the Re-identify search query
                'reidentify_query': f'{track_title} {artist_name}'.strip(),
            }

            try:
                if context.create_finding:
                    created = context.create_finding(
                        job_id=self.job_id,
                        finding_type='suspect_album_tag',
                        severity='warning',
                        entity_type='track',
                        entity_id=f'lib2:{track_id}',
                        file_path=row['file_path'] or '',
                        title=title,
                        description=description,
                        details=details,
                    )
                    if created:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
            except Exception as e:
                logger.warning('Could not create finding for track %s: %s', track_id, e)
                result.errors += 1

        if context.update_progress:
            context.update_progress(total, total)
        if context.report_progress:
            context.report_progress(
                phase='Done',
                log_line=(f'Found {result.findings_created} suspect album tag(s)'),
                log_type='success' if result.findings_created == 0 else 'warning',
            )

        return result
