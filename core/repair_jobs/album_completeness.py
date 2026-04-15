"""Album Completeness Checker Job — finds albums missing tracks."""

from core.metadata_service import (
    get_album_tracks_for_source,
    get_primary_source,
    get_source_priority,
)
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.album_complete")


@register_job
class AlbumCompletenessJob(RepairJob):
    job_id = 'album_completeness'
    display_name = 'Album Completeness'
    description = 'Checks if all tracks from albums are present'
    help_text = (
        'Compares the number of tracks you have for each album against the expected total '
        'from the active metadata provider first, then other supported sources if needed. '
        'Albums where tracks are missing get flagged as findings with details about which '
        'tracks are absent.\n\n'
        'Useful for catching partial downloads or albums where some tracks failed to download. '
        'You can use the Download Missing feature from the album page to fill gaps.\n\n'
        'Settings:\n'
        '- Min Tracks For Check: Only check albums with at least this many expected tracks '
        '(skips singles and EPs)\n'
        '- Min Completion %: Only flag albums where you already have at least this percentage '
        'of tracks (e.g. 30% skips albums where you only have 1 track from a playlist import, '
        'but catches albums where a download partially failed)'
    )
    icon = 'repair-icon-completeness'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'min_tracks_for_check': 3,
        'min_completion_pct': 0,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        min_tracks = settings.get('min_tracks_for_check', 3)
        min_completion_pct = settings.get('min_completion_pct', 0)
        primary_source = self._get_primary_source()

        # Fetch all albums with ANY external source ID — not just Spotify
        albums = []
        conn = None
        has_itunes = False
        has_deezer = False
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()

            # Check which source columns exist (older DBs may lack some)
            cursor.execute("PRAGMA table_info(albums)")
            columns = {row[1] for row in cursor.fetchall()}
            has_itunes = 'itunes_album_id' in columns
            has_deezer = 'deezer_id' in columns
            has_discogs = 'discogs_id' in columns
            has_hydrabase = 'soul_id' in columns

            # Build SELECT with available source ID columns
            select_cols = [
                ('al.id', 'album_id'),
                ('al.title', 'album_title'),
                ('ar.name', 'artist_name'),
                ('al.spotify_album_id', 'spotify_album_id'),
                ('al.track_count', 'track_count'),
                ('COUNT(t.id)', 'actual_count'),
                ('al.thumb_url', 'album_thumb_url'),
                ('ar.thumb_url', 'artist_thumb_url'),
            ]
            if has_itunes:
                select_cols.append(('al.itunes_album_id', 'itunes_album_id'))
            if has_deezer:
                select_cols.append(('al.deezer_id', 'deezer_album_id'))
            if has_discogs:
                select_cols.append(('al.discogs_id', 'discogs_album_id'))
            if has_hydrabase:
                select_cols.append(('al.soul_id', 'hydrabase_album_id'))

            # WHERE: album has at least one source ID
            where_parts = ["(al.spotify_album_id IS NOT NULL AND al.spotify_album_id != '')"]
            if has_itunes:
                where_parts.append("(al.itunes_album_id IS NOT NULL AND al.itunes_album_id != '')")
            if has_deezer:
                where_parts.append("(al.deezer_id IS NOT NULL AND al.deezer_id != '')")
            if has_discogs:
                where_parts.append("(al.discogs_id IS NOT NULL AND al.discogs_id != '')")
            if has_hydrabase:
                where_parts.append("(al.soul_id IS NOT NULL AND al.soul_id != '')")
            where_clause = ' OR '.join(where_parts)

            select_sql = ', '.join(f'{expr} AS {alias}' for expr, alias in select_cols)
            cursor.execute(f"""
                SELECT {select_sql}
                FROM albums al
                LEFT JOIN artists ar ON ar.id = al.artist_id
                LEFT JOIN tracks t ON t.album_id = al.id
                WHERE {where_clause}
                GROUP BY al.id
            """)
            albums = cursor.fetchall()
            column_index = {alias: idx for idx, (_, alias) in enumerate(select_cols)}
        except Exception as e:
            logger.error("Error fetching albums: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(albums)
        if context.update_progress:
            context.update_progress(0, total)

        logger.info("Checking completeness of %d albums", total)

        if context.report_progress:
            context.report_progress(phase=f'Checking {total} albums...', total=total)

        for i, row in enumerate(albums):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            album_id = row[column_index['album_id']]
            title = row[column_index['album_title']]
            artist_name = row[column_index['artist_name']]
            spotify_album_id = row[column_index['spotify_album_id']]
            db_track_count = row[column_index['track_count']]
            actual_count = row[column_index['actual_count']]
            album_thumb = row[column_index['album_thumb_url']]
            artist_thumb = row[column_index['artist_thumb_url']]
            itunes_album_id = row[column_index['itunes_album_id']] if 'itunes_album_id' in column_index else None
            deezer_album_id = row[column_index['deezer_album_id']] if 'deezer_album_id' in column_index else None
            discogs_album_id = row[column_index['discogs_album_id']] if 'discogs_album_id' in column_index else None
            hydrabase_album_id = row[column_index['hydrabase_album_id']] if 'hydrabase_album_id' in column_index else None

            result.scanned += 1

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Checking {i + 1} / {total}',
                    log_line=f'Album: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )

            # If we don't know the expected track count, try to get it from an API
            expected_total = db_track_count

            album_ids = {
                'spotify': spotify_album_id or '',
                'itunes': itunes_album_id or '',
                'deezer': deezer_album_id or '',
                'discogs': discogs_album_id or '',
                'hydrabase': hydrabase_album_id or '',
            }

            if not expected_total:
                expected_total = self._get_expected_total(context, primary_source, album_ids)

            # Skip singles/EPs based on expected track count (not local count)
            if expected_total and expected_total < min_tracks:
                result.skipped += 1
                if context.update_progress and (i + 1) % 5 == 0:
                    context.update_progress(i + 1, total)
                continue

            if not expected_total or actual_count >= expected_total:
                result.skipped += 1
                if context.update_progress and (i + 1) % 5 == 0:
                    context.update_progress(i + 1, total)
                continue

            # Skip albums with zero local tracks — nothing to auto-fill from
            if actual_count == 0:
                result.skipped += 1
                continue

            # Skip albums below minimum completion percentage
            # (filters out "1 track from a playlist import" false positives)
            if min_completion_pct > 0 and expected_total > 0:
                completion = (actual_count / expected_total) * 100
                if completion < min_completion_pct:
                    result.skipped += 1
                    if context.update_progress and (i + 1) % 5 == 0:
                        context.update_progress(i + 1, total)
                    continue

            # Album is incomplete — try to find which tracks are missing
            missing_tracks = self._find_missing_tracks(context, primary_source, album_id, album_ids)

            if context.report_progress:
                context.report_progress(
                    log_line=f'Incomplete: {title or "Unknown"} ({actual_count}/{expected_total})',
                    log_type='skip'
                )
            if context.create_finding:
                try:
                    context.create_finding(
                        job_id=self.job_id,
                        finding_type='incomplete_album',
                        severity='info',
                        entity_type='album',
                        entity_id=str(album_id),
                        file_path=None,
                        title=f'Incomplete: {title or "Unknown"} ({actual_count}/{expected_total})',
                        description=(
                            f'Album "{title}" by {artist_name or "Unknown"} has {actual_count} of '
                            f'{expected_total} tracks'
                        ),
                        details={
                            'album_id': album_id,
                            'album_title': title,
                            'artist': artist_name,
                            'primary_source': primary_source,
                            'primary_album_id': self._get_album_id_for_source(primary_source, album_ids) or '',
                            'spotify_album_id': spotify_album_id or '',
                            'itunes_album_id': itunes_album_id or '',
                            'deezer_album_id': deezer_album_id or '',
                            'discogs_album_id': discogs_album_id or '',
                            'hydrabase_album_id': hydrabase_album_id or '',
                            'expected_tracks': expected_total,
                            'actual_tracks': actual_count,
                            'missing_tracks': missing_tracks,
                            'album_thumb_url': album_thumb or None,
                            'artist_thumb_url': artist_thumb or None,
                        }
                    )
                    result.findings_created += 1
                except Exception as e:
                    logger.debug("Error creating completeness finding for album %s: %s", album_id, e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Completeness check: %d albums checked, %d incomplete found",
                    result.scanned, result.findings_created)
        return result

    def _get_expected_total(self, context, primary_source, album_ids):
        """Try to get the expected track count from the active metadata provider first."""
        for source in get_source_priority(primary_source):
            album_id = self._get_album_id_for_source(source, album_ids)
            if not album_id:
                continue
            api_tracks = self._get_album_tracks(source, album_id)
            items = self._extract_track_items(api_tracks)
            if items:
                return len(items)

        return 0

    def _find_missing_tracks(self, context, primary_source, album_id, album_ids):
        """Identify which specific tracks are missing using the active metadata provider first."""
        # Get track numbers we already have
        owned_numbers = set()
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT track_number FROM tracks WHERE album_id = ? AND track_number IS NOT NULL",
                (album_id,)
            )
            for tr in cursor.fetchall():
                owned_numbers.add(tr[0])
        except Exception:
            return []
        finally:
            if conn:
                conn.close()

        api_tracks = None
        for source in get_source_priority(primary_source):
            source_album_id = self._get_album_id_for_source(source, album_ids)
            if not source_album_id:
                continue
            api_tracks = self._get_album_tracks(source, source_album_id)
            if self._extract_track_items(api_tracks):
                break

        items = self._extract_track_items(api_tracks)
        if not items:
            return []

        # All supported provider responses expose the same core fields once normalized.
        # items[].track_number, items[].name, items[].disc_number, items[].id, items[].artists
        missing_tracks = []
        for item in items:
            tn = item.get('track_number')
            if tn and tn not in owned_numbers:
                track_artists = []
                for a in item.get('artists', []):
                    if isinstance(a, dict):
                        track_artists.append(a.get('name', ''))
                    elif isinstance(a, str):
                        track_artists.append(a)
                missing_tracks.append({
                    'track_number': tn,
                    'name': item.get('name', ''),
                    'disc_number': item.get('disc_number', 1),
                    'source': item.get('_source', primary_source),
                    'source_track_id': item.get('id', ''),
                    'track_id': item.get('id', ''),
                    'spotify_track_id': item.get('id', ''),
                    'duration_ms': item.get('duration_ms', 0),
                    'artists': track_artists,
                })
        return missing_tracks

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def _get_primary_source(self) -> str:
        """Return the active metadata source used for source prioritization."""
        try:
            return get_primary_source()
        except Exception:
            return 'deezer'

    def _get_album_id_for_source(self, source: str, album_ids: dict) -> str:
        return album_ids.get(source, '')

    def _get_album_tracks(self, source: str, album_id: str):
        """Fetch album tracks from a specific source."""
        try:
            return get_album_tracks_for_source(source, album_id)
        except Exception as e:
            logger.debug("Error getting %s album tracks for %s: %s", source.capitalize(), album_id, e)
            return None

    def _extract_track_items(self, api_tracks):
        """Normalize album track responses to a list of item dicts."""
        if not api_tracks:
            return []
        if isinstance(api_tracks, dict):
            items = api_tracks.get('items') or []
            return items if items else []
        if isinstance(api_tracks, list):
            return api_tracks
        return []

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()

            # Check which columns exist
            cursor.execute("PRAGMA table_info(albums)")
            columns = {row[1] for row in cursor.fetchall()}

            where_parts = ["(spotify_album_id IS NOT NULL AND spotify_album_id != '')"]
            if 'itunes_album_id' in columns:
                where_parts.append("(itunes_album_id IS NOT NULL AND itunes_album_id != '')")
            if 'deezer_id' in columns:
                where_parts.append("(deezer_id IS NOT NULL AND deezer_id != '')")
            if 'discogs_id' in columns:
                where_parts.append("(discogs_id IS NOT NULL AND discogs_id != '')")
            if 'soul_id' in columns:
                where_parts.append("(soul_id IS NOT NULL AND soul_id != '')")

            cursor.execute(f"""
                SELECT COUNT(*) FROM albums
                WHERE {' OR '.join(where_parts)}
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
