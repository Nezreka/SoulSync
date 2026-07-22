"""Genre Tag Cleanup Job — re-applies the strict genre whitelist to genres
stored BEFORE it was enabled (#1057, follow-up to #321).

Strict genre filtering gates NEW enrichment fetches; anything enriched earlier
keeps its off-whitelist genres in artists.genres / albums.genres, and every
downstream surface (server metadata sync, Write Tags before its own #1057
filter) reproduces them. This job is the mop: when strict mode is ON it scans
stored genres, raises a finding per artist/album whose genre list the whitelist
would shrink, and the fix rewrites the stored list to the kept genres only.

Report-only until approved (auto_fix = False). The fix never invents genres —
it only removes; an entity whose genres are ALL off-whitelist ends up with none
(strict means strict), and the finding says so up front. With strict mode OFF
the scan is a no-op skip, so the job is safe to leave enabled.
"""

import json

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.genre_cleanup")


def parse_stored_genres(raw):
    """Tolerant parse of a stored genres value: JSON list, JSON string, or a
    comma-separated string (legacy rows). Returns a list of clean strings."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(g).strip() for g in raw if str(g).strip()]
    s = str(raw).strip()
    if not s:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(g).strip() for g in v if str(g).strip()]
        s = str(v)
    except (ValueError, TypeError):
        pass
    return [g.strip() for g in s.split(',') if g.strip()]


@register_job
class GenreCleanupJob(RepairJob):
    job_id = 'genre_cleanup'
    display_name = 'Genre Tag Cleanup'
    description = 'Removes off-whitelist genres stored before strict genre filtering was enabled'
    help_text = (
        'Strict genre filtering (Settings → Metadata) only applies to NEW metadata as it is '
        'fetched — genres that were stored before you enabled it stay on your artists and '
        'albums, and get pushed to your media server and written into file tags.\n\n'
        'This job re-checks every stored genre list against your whitelist. Each artist or '
        'album that would lose genres becomes a finding showing exactly what would be kept '
        'and what would be removed. Approving the fix rewrites the stored list to the kept '
        'genres only — it never adds or substitutes genres, and if every genre is off the '
        'whitelist the entity is left with none.\n\n'
        'After cleaning, your normal metadata sync pushes the cleaned genres to your server, '
        'and "Write Tags" writes them into the files.\n\n'
        'When strict genre filtering is disabled this job does nothing.'
    )
    icon = 'repair-icon-consistency'
    default_enabled = True
    default_interval_hours = 24 * 7
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        cfgm = context.config_manager
        if not (cfgm and cfgm.get('genre_whitelist.enabled', False)):
            if context.report_progress:
                context.report_progress(
                    phase='Skipped — strict genre filtering is off',
                    log_line='Enable strict genre filtering (Settings → Metadata) for this job to have anything to clean.',
                    log_type='info')
            return result

        from core.genre_filter import filter_genres

        rows = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            # Artists with stored genres. ar.id doubles as the clickable-card id.
            cursor.execute("""
                SELECT 'artist', ar.id, ar.name, ar.genres, ar.thumb_url, NULL, ar.id, ar.name, ar.thumb_url
                FROM artists ar
                WHERE ar.genres IS NOT NULL AND ar.genres != '' AND ar.genres != '[]'
            """)
            rows.extend(cursor.fetchall())
            # Albums with stored genres (+ their artist for the finding card).
            cursor.execute("""
                SELECT 'album', al.id, al.title, al.genres, al.thumb_url, al.thumb_url, ar.id, ar.name, ar.thumb_url
                FROM albums al
                LEFT JOIN artists ar ON ar.id = al.artist_id
                WHERE al.genres IS NOT NULL AND al.genres != '' AND al.genres != '[]'
            """)
            rows.extend(cursor.fetchall())
        except Exception as e:
            logger.error("Error fetching stored genres: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(rows)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Checking {total} genre lists...', total=total)

        for i, row in enumerate(rows):
            if context.check_stop():
                return result
            if i % 100 == 0 and context.wait_if_paused():
                return result

            kind, entity_id, name, raw_genres, entity_thumb, album_thumb, artist_id, artist_name, artist_thumb = row
            result.scanned += 1

            original = parse_stored_genres(raw_genres)
            if not original:
                continue
            kept = filter_genres(list(original), cfgm)
            if kept == original:
                continue

            removed = [g for g in original if g not in kept]
            label = 'artist' if kind == 'artist' else 'album'
            emptied = not kept
            if context.report_progress:
                context.report_progress(
                    log_line=f'{name}: {len(removed)} off-whitelist genre(s)'
                             + (' — would be left with none' if emptied else ''),
                    log_type='warning' if emptied else 'info')

            if context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='genre_cleanup',
                        severity='info',
                        entity_type=kind,
                        entity_id=str(entity_id),
                        file_path=None,
                        title=f'Off-whitelist genres: {name}',
                        description=(
                            f'{len(removed)} of {len(original)} stored genre(s) on this {label} '
                            f'are not on your whitelist'
                            + (' — cleaning will leave it with NO genres' if emptied else '')
                        ),
                        details={
                            'entity': kind,
                            'name': name,
                            'original_genres': original,
                            'kept_genres': kept,
                            'removed_genres': removed,
                            'artist_id': artist_id,
                            'artist_name': artist_name,
                            'artist_thumb_url': artist_thumb or None,
                            'album_thumb_url': album_thumb or None,
                            'album_title': name if kind == 'album' else None,
                        },
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("Error creating genre finding for %s %s: %s", kind, entity_id, e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 200 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)
        return result
