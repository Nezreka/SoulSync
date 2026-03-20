"""Album Completeness Checker Job — finds albums missing tracks."""

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
        'from the album tracklist (via Spotify, iTunes, or Deezer). Albums where tracks are '
        'missing get flagged as findings with details about which tracks are absent.\n\n'
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

        # Fetch all albums with ANY external source ID — not just Spotify
        albums = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()

            # Check which source columns exist (older DBs may lack some)
            cursor.execute("PRAGMA table_info(albums)")
            columns = {row[1] for row in cursor.fetchall()}
            has_itunes = 'itunes_album_id' in columns
            has_deezer = 'deezer_id' in columns

            # Build SELECT with available source ID columns
            select_cols = [
                'al.id', 'al.title', 'ar.name', 'al.spotify_album_id', 'al.track_count',
                'COUNT(t.id) as actual_count', 'al.thumb_url', 'ar.thumb_url',
            ]
            if has_itunes:
                select_cols.append('al.itunes_album_id')
            if has_deezer:
                select_cols.append('al.deezer_id')

            # WHERE: album has at least one source ID
            where_parts = []
            if True:  # spotify always exists
                where_parts.append("(al.spotify_album_id IS NOT NULL AND al.spotify_album_id != '')")
            if has_itunes:
                where_parts.append("(al.itunes_album_id IS NOT NULL AND al.itunes_album_id != '')")
            if has_deezer:
                where_parts.append("(al.deezer_id IS NOT NULL AND al.deezer_id != '')")
            where_clause = ' OR '.join(where_parts)

            cursor.execute(f"""
                SELECT {', '.join(select_cols)}
                FROM albums al
                LEFT JOIN artists ar ON ar.id = al.artist_id
                LEFT JOIN tracks t ON t.album_id = al.id
                WHERE {where_clause}
                GROUP BY al.id
            """)
            albums = cursor.fetchall()
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

        # Determine column positions based on what we selected
        # Fixed: 0=id, 1=title, 2=artist, 3=spotify_id, 4=track_count, 5=actual, 6=album_thumb, 7=artist_thumb
        itunes_col = 8 if has_itunes else None
        deezer_col = (9 if has_itunes else 8) if has_deezer else None

        for i, row in enumerate(albums):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            album_id = row[0]
            title = row[1]
            artist_name = row[2]
            spotify_album_id = row[3]
            db_track_count = row[4]
            actual_count = row[5]
            album_thumb = row[6]
            artist_thumb = row[7]
            itunes_album_id = row[itunes_col] if itunes_col is not None else None
            deezer_album_id = row[deezer_col] if deezer_col is not None else None

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

            if not expected_total:
                expected_total = self._get_expected_total(
                    context, spotify_album_id, itunes_album_id, deezer_album_id
                )

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
            missing_tracks = self._find_missing_tracks(
                context, album_id, spotify_album_id, itunes_album_id, deezer_album_id
            )

            if context.report_progress:
                context.report_progress(
                    log_line=f'Incomplete: {title or "Unknown"} ({actual_count}/{expected_total})',
                    log_type='skip'
                )
            if context.create_finding:
                try:
                    # Use whichever source ID is available
                    source_id = spotify_album_id or itunes_album_id or deezer_album_id or ''
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
                            'spotify_album_id': spotify_album_id or '',
                            'itunes_album_id': itunes_album_id or '',
                            'deezer_album_id': deezer_album_id or '',
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

    def _get_expected_total(self, context, spotify_id, itunes_id, deezer_id):
        """Try to get the expected track count from any available API source."""
        # Try Spotify first
        if spotify_id and context.spotify_client and not context.is_spotify_rate_limited():
            try:
                album_data = context.spotify_client.get_album(spotify_id)
                if album_data:
                    total = album_data.get('total_tracks', 0)
                    if total:
                        return total
            except Exception:
                pass

        # Try fallback client (iTunes or Deezer) — both return Spotify-compatible format
        # Match the ID to the actual client type to avoid passing iTunes ID to Deezer or vice versa
        if context.itunes_client:
            is_deezer = type(context.itunes_client).__name__ == 'DeezerClient'
            primary_id = deezer_id if is_deezer else itunes_id
            secondary_id = itunes_id if is_deezer else deezer_id
            for fid in [primary_id, secondary_id]:
                if not fid:
                    continue
                try:
                    api_tracks = context.itunes_client.get_album_tracks(fid)
                    if api_tracks and 'items' in api_tracks:
                        return len(api_tracks['items'])
                except Exception:
                    pass

        return 0

    def _find_missing_tracks(self, context, album_id, spotify_id, itunes_id, deezer_id):
        """Identify which specific tracks are missing using any available API source."""
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

        # Try Spotify first
        api_tracks = None
        if spotify_id and context.spotify_client and not context.is_spotify_rate_limited():
            try:
                api_tracks = context.spotify_client.get_album_tracks(spotify_id)
            except Exception as e:
                logger.debug("Error getting Spotify album tracks for %s: %s", spotify_id, e)

        # Try fallback client (iTunes or Deezer)
        if not api_tracks or 'items' not in (api_tracks or {}):
            if context.itunes_client:
                is_deezer = type(context.itunes_client).__name__ == 'DeezerClient'
                primary_id = deezer_id if is_deezer else itunes_id
                secondary_id = itunes_id if is_deezer else deezer_id
                for fid in [primary_id, secondary_id]:
                    if not fid:
                        continue
                    try:
                        api_tracks = context.itunes_client.get_album_tracks(fid)
                        if api_tracks and 'items' in api_tracks:
                            break
                    except Exception as e:
                        logger.debug("Error getting fallback album tracks for %s: %s", fid, e)

        if not api_tracks or 'items' not in api_tracks:
            return []

        # Both Spotify, iTunes, and Deezer return the same format:
        # items[].track_number, items[].name, items[].disc_number, items[].id, items[].artists
        missing_tracks = []
        for item in api_tracks['items']:
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
