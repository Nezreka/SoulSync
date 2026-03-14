"""Missing Cover Art Filler Job — finds albums without artwork and fills from APIs."""

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.cover_art")


@register_job
class MissingCoverArtJob(RepairJob):
    job_id = 'missing_cover_art'
    display_name = 'Cover Art Filler'
    description = 'Finds albums missing artwork and fills from Spotify/iTunes'
    icon = 'repair-icon-coverart'
    default_enabled = True
    default_interval_hours = 48
    default_settings = {
        'prefer_source': 'spotify',
        'embed_in_file': False,
    }
    auto_fix = True

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
                SELECT id, title, artist, spotify_id, artwork_url
                FROM albums
                WHERE (artwork_url IS NULL OR artwork_url = '')
                  AND title IS NOT NULL AND title != ''
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

        for i, row in enumerate(albums):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            album_id, title, artist, spotify_id, _ = row
            result.scanned += 1

            artwork_url = None

            # Try Spotify first (or iTunes first based on preference)
            if prefer_source == 'spotify':
                artwork_url = self._try_spotify(spotify_id, title, artist, context)
                if not artwork_url:
                    artwork_url = self._try_itunes(title, artist, context)
            else:
                artwork_url = self._try_itunes(title, artist, context)
                if not artwork_url:
                    artwork_url = self._try_spotify(spotify_id, title, artist, context)

            if artwork_url:
                # Update DB
                try:
                    conn2 = context.db._get_connection()
                    cursor2 = conn2.cursor()
                    cursor2.execute(
                        "UPDATE albums SET artwork_url = ? WHERE id = ?",
                        (artwork_url, album_id)
                    )
                    conn2.commit()
                    conn2.close()
                    result.auto_fixed += 1
                    logger.debug("Filled artwork for album '%s': %s", title, artwork_url[:80])
                except Exception as e:
                    logger.debug("Error updating artwork for album %s: %s", album_id, e)
                    result.errors += 1
            else:
                result.skipped += 1

            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Cover art fill: %d albums checked, %d filled, %d skipped",
                     result.scanned, result.auto_fixed, result.skipped)
        return result

    def _try_spotify(self, spotify_id, title, artist, context):
        """Try to get album art from Spotify."""
        client = context.spotify_client
        if not client:
            return None

        try:
            # If we have a Spotify album ID, fetch directly
            if spotify_id and client.is_spotify_authenticated():
                album_data = client.get_album(spotify_id)
                if album_data:
                    images = album_data.get('images', [])
                    if images:
                        return images[0].get('url')

            # Search by name
            if title and client.is_spotify_authenticated():
                query = f"{artist} {title}" if artist else title
                results = client.search_albums(query, limit=1)
                if results and hasattr(results[0], 'image_url') and results[0].image_url:
                    return results[0].image_url
        except Exception as e:
            logger.debug("Spotify art lookup failed for '%s': %s", title, e)
        return None

    def _try_itunes(self, title, artist, context):
        """Try to get album art from iTunes."""
        client = context.itunes_client
        if not client:
            return None

        try:
            query = f"{artist} {title}" if artist else title
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
                WHERE (artwork_url IS NULL OR artwork_url = '')
                  AND title IS NOT NULL AND title != ''
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
