"""Missing Cover Art Filler Job — finds albums without artwork and locates art from APIs."""

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.cover_art")


@register_job
class MissingCoverArtJob(RepairJob):
    job_id = 'missing_cover_art'
    display_name = 'Cover Art Filler'
    description = 'Finds albums missing artwork and locates art from Spotify/iTunes'
    help_text = (
        'Scans your library for albums that have no cover art stored in the database. '
        'For each missing cover, it searches Spotify and iTunes APIs using the album name '
        'and artist to find matching artwork.\n\n'
        'When artwork is found, a finding is created with the image URL so you can review '
        'and apply it. The job does not download or embed artwork automatically.\n\n'
        'Settings:\n'
        '- Prefer Source: Which API to try first for artwork (spotify or itunes)'
    )
    icon = 'repair-icon-coverart'
    default_enabled = True
    default_interval_hours = 48
    default_settings = {
        'prefer_source': 'spotify',
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        prefer_source = settings.get('prefer_source', 'spotify')

        # Fetch albums with missing artwork
        albums = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT al.id, al.title, ar.name, al.spotify_album_id, al.thumb_url,
                       ar.thumb_url
                FROM albums al
                LEFT JOIN artists ar ON ar.id = al.artist_id
                WHERE (al.thumb_url IS NULL OR al.thumb_url = '')
                  AND al.title IS NOT NULL AND al.title != ''
            """)
            albums = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching albums without artwork: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(albums)
        if context.update_progress:
            context.update_progress(0, total)

        logger.info("Found %d albums missing cover art", total)

        if context.report_progress:
            context.report_progress(phase=f'Searching artwork for {total} albums...', total=total)

        for i, row in enumerate(albums):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            album_id, title, artist_name, spotify_album_id, _, artist_thumb = row
            result.scanned += 1

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Searching {i + 1} / {total}',
                    log_line=f'Searching: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )

            artwork_url = None

            # Try to find artwork URL from APIs
            if prefer_source == 'spotify':
                artwork_url = self._try_spotify(spotify_album_id, title, artist_name, context)
                if not artwork_url:
                    artwork_url = self._try_itunes(title, artist_name, context)
            else:
                artwork_url = self._try_itunes(title, artist_name, context)
                if not artwork_url:
                    artwork_url = self._try_spotify(spotify_album_id, title, artist_name, context)

            if artwork_url:
                if context.report_progress:
                    context.report_progress(
                        log_line=f'Found art: {title or "Unknown"}',
                        log_type='success'
                    )
                # Create finding for user to approve
                if context.create_finding:
                    try:
                        context.create_finding(
                            job_id=self.job_id,
                            finding_type='missing_cover_art',
                            severity='info',
                            entity_type='album',
                            entity_id=str(album_id),
                            file_path=None,
                            title=f'Missing artwork: {title or "Unknown"}',
                            description=f'Album "{title}" by {artist_name or "Unknown"} has no cover art. Found artwork from API.',
                            details={
                                'album_id': album_id,
                                'album_title': title,
                                'artist': artist_name,
                                'found_artwork_url': artwork_url,
                                'spotify_album_id': spotify_album_id,
                                'artist_thumb_url': artist_thumb or None,
                            }
                        )
                        result.findings_created += 1
                    except Exception as e:
                        logger.debug("Error creating cover art finding for album %s: %s", album_id, e)
                        result.errors += 1
            else:
                result.skipped += 1

            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Cover art scan: %d albums checked, %d found art, %d skipped",
                     result.scanned, result.findings_created, result.skipped)
        return result

    def _try_spotify(self, spotify_album_id, title, artist_name, context):
        """Try to get album art from Spotify."""
        client = context.spotify_client
        if not client:
            return None

        try:
            # If we have a Spotify album ID, fetch directly
            if spotify_album_id and client.is_spotify_authenticated():
                album_data = client.get_album(spotify_album_id)
                if album_data:
                    images = album_data.get('images', [])
                    if images:
                        return images[0].get('url')

            # Search by name
            if title and client.is_spotify_authenticated():
                query = f"{artist_name} {title}" if artist_name else title
                results = client.search_albums(query, limit=1)
                if results and hasattr(results[0], 'image_url') and results[0].image_url:
                    return results[0].image_url
        except Exception as e:
            logger.debug("Spotify art lookup failed for '%s': %s", title, e)
        return None

    def _try_itunes(self, title, artist_name, context):
        """Try to get album art from iTunes."""
        client = context.itunes_client
        if not client:
            return None

        try:
            query = f"{artist_name} {title}" if artist_name else title
            results = client.search_albums(query, limit=1)
            if results and hasattr(results[0], 'image_url') and results[0].image_url:
                return results[0].image_url
        except Exception as e:
            logger.debug("iTunes art lookup failed for '%s': %s", title, e)
        return None

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM albums
                WHERE (thumb_url IS NULL OR thumb_url = '')
                  AND title IS NOT NULL AND title != ''
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
