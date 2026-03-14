"""Metadata Gap Filler Job — fills missing track metadata from APIs."""

import time

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.metadata_gap")


@register_job
class MetadataGapFillerJob(RepairJob):
    job_id = 'metadata_gap_filler'
    display_name = 'Metadata Gap Filler'
    description = 'Fills missing genre, year, ISRC, and MusicBrainz IDs'
    icon = 'repair-icon-metadata'
    default_enabled = False
    default_interval_hours = 72
    default_settings = {
        'fill_genre': True,
        'fill_year': True,
        'fill_isrc': True,
        'fill_musicbrainz_id': True,
        'write_to_file': False,
    }
    auto_fix = True

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        fill_genre = settings.get('fill_genre', True)
        fill_year = settings.get('fill_year', True)
        fill_isrc = settings.get('fill_isrc', True)
        fill_mb_id = settings.get('fill_musicbrainz_id', True)

        # Build WHERE clauses for missing fields
        conditions = []
        if fill_genre:
            conditions.append("(genre IS NULL OR genre = '')")
        if fill_year:
            conditions.append("(year IS NULL OR year = '' OR year = '0')")
        if fill_isrc:
            conditions.append("(isrc IS NULL OR isrc = '')")
        if fill_mb_id:
            conditions.append("(musicbrainz_id IS NULL OR musicbrainz_id = '')")

        if not conditions:
            return result

        where = " OR ".join(conditions)

        # Fetch tracks with gaps, prioritizing those with spotify_id
        tracks = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT id, title, artist, album, spotify_id, isrc, genre, year, musicbrainz_id
                FROM tracks
                WHERE title IS NOT NULL AND title != ''
                  AND ({where})
                ORDER BY
                    CASE WHEN spotify_id IS NOT NULL AND spotify_id != '' THEN 0 ELSE 1 END,
                    id
                LIMIT 500
            """)
            tracks = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching tracks with metadata gaps: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(tracks)
        if context.update_progress:
            context.update_progress(0, total)

        logger.info("Found %d tracks with metadata gaps", total)

        for i, row in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 20 == 0 and context.wait_if_paused():
                return result

            track_id, title, artist, album, spotify_id, isrc, genre, year, mb_id = row
            result.scanned += 1
            updates = {}

            # Try Spotify enrichment first (most reliable)
            if spotify_id and context.spotify_client:
                try:
                    track_data = context.spotify_client.get_track_details(spotify_id)
                    if track_data:
                        if fill_isrc and not isrc:
                            ext_ids = track_data.get('external_ids', {})
                            if ext_ids.get('isrc'):
                                updates['isrc'] = ext_ids['isrc']

                        if fill_year and not year:
                            album_data = track_data.get('album', {})
                            rd = album_data.get('release_date', '')
                            if rd and len(rd) >= 4:
                                updates['year'] = rd[:4]

                    # Get album for genre (genres are on artists in Spotify)
                    if fill_genre and not genre:
                        artists = track_data.get('artists', []) if track_data else []
                        if artists:
                            artist_data = context.spotify_client.get_artist(artists[0].get('id', ''))
                            if artist_data and artist_data.get('genres'):
                                updates['genre'] = ', '.join(artist_data['genres'][:3])

                except Exception as e:
                    logger.debug("Spotify enrichment failed for track %s: %s", track_id, e)

            # Try MusicBrainz for MB ID
            if fill_mb_id and not mb_id and context.mb_client:
                try:
                    search_query = f'"{title}" AND artist:"{artist}"' if artist else f'"{title}"'
                    mb_results = context.mb_client.search_recordings(search_query, limit=1)
                    if mb_results:
                        recordings = mb_results.get('recording-list', [])
                        if recordings:
                            updates['musicbrainz_id'] = recordings[0].get('id', '')
                except Exception as e:
                    logger.debug("MusicBrainz lookup failed for track %s: %s", track_id, e)

            # Apply updates
            if updates:
                try:
                    conn2 = context.db._get_connection()
                    cursor2 = conn2.cursor()
                    set_parts = [f"{k} = ?" for k in updates.keys()]
                    values = list(updates.values()) + [track_id]
                    cursor2.execute(
                        f"UPDATE tracks SET {', '.join(set_parts)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        values
                    )
                    conn2.commit()
                    conn2.close()
                    result.auto_fixed += 1
                    logger.debug("Filled %d metadata fields for track '%s' (id=%s)",
                                 len(updates), title, track_id)
                except Exception as e:
                    logger.debug("Error updating metadata for track %s: %s", track_id, e)
                    result.errors += 1
            else:
                result.skipped += 1

            # Rate limit API calls
            if spotify_id:
                time.sleep(0.5)

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Metadata gap fill: %d tracks checked, %d enriched, %d skipped",
                     result.scanned, result.auto_fixed, result.skipped)
        return result

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
                SELECT COUNT(*) FROM tracks
                WHERE title IS NOT NULL AND title != ''
                  AND ((genre IS NULL OR genre = '')
                    OR (year IS NULL OR year = '' OR year = '0')
                    OR (isrc IS NULL OR isrc = '')
                    OR (musicbrainz_id IS NULL OR musicbrainz_id = ''))
            """)
            row = cursor.fetchone()
            return min(row[0], 500) if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
