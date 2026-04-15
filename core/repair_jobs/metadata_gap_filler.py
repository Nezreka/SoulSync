"""Metadata Gap Filler Job — finds tracks missing key metadata and locates it from APIs."""

from core.metadata_service import get_client_for_source, get_primary_source, get_source_priority
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.metadata_gap")


@register_job
class MetadataGapFillerJob(RepairJob):
    job_id = 'metadata_gap_filler'
    display_name = 'Metadata Gap Filler'
    description = 'Finds tracks missing ISRC or MusicBrainz IDs and locates them'
    help_text = (
        'Searches for tracks in your library that are missing important metadata identifiers: '
        'ISRC codes and MusicBrainz recording IDs. These identifiers are used for accurate '
        'matching, scrobbling, and enrichment.\n\n'
        'For each track with gaps, the job queries MusicBrainz by title and artist to find '
        'the correct IDs. Results are reported as findings for your review.\n\n'
        'Settings:\n'
        '- Fill ISRC: Look up missing ISRC codes\n'
        '- Fill MusicBrainz ID: Look up missing MusicBrainz recording IDs'
    )
    icon = 'repair-icon-metadata'
    default_enabled = False
    default_interval_hours = 72
    default_settings = {
        'fill_isrc': True,
        'fill_musicbrainz_id': True,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        fill_isrc = settings.get('fill_isrc', True)
        fill_mb_id = settings.get('fill_musicbrainz_id', True)
        source_priority = get_source_priority(get_primary_source())

        # Build WHERE clauses for missing fields (only columns that exist on tracks)
        conditions = []
        if fill_isrc:
            conditions.append("(t.isrc IS NULL OR t.isrc = '')")
        if fill_mb_id:
            conditions.append("(t.musicbrainz_recording_id IS NULL OR t.musicbrainz_recording_id = '')")

        if not conditions:
            return result

        where = " OR ".join(conditions)

        # Fetch tracks with gaps, prioritizing those with source track IDs.
        tracks = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tracks)")
            track_columns = {column[1] for column in cursor.fetchall()}

            select_cols = [
                "t.id",
                "t.title",
                "ar.name",
                "al.title",
                "t.isrc",
                "t.musicbrainz_recording_id",
                "al.thumb_url",
                "ar.thumb_url",
            ]
            column_map = [
                ("spotify_track_id", "t.spotify_track_id"),
                ("itunes_track_id", "t.itunes_track_id"),
                ("deezer_track_id", "t.deezer_track_id"),
            ]
            column_index = {}
            for alias, column in column_map:
                if column.split('.', 1)[1] in track_columns:
                    column_index[alias] = len(select_cols)
                    select_cols.append(f"{column} AS {alias}")

            cursor.execute(f"""
                SELECT {', '.join(select_cols)}
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.title IS NOT NULL AND t.title != ''
                  AND ({where})
                LIMIT 500
            """)
            tracks = cursor.fetchall()
            tracks = sorted(tracks, key=lambda row: _track_row_priority(row, column_index, source_priority))
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

        if context.report_progress:
            context.report_progress(phase=f'Enriching {total} tracks...', total=total)

        for i, row in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 20 == 0 and context.wait_if_paused():
                return result

            track_id, title, artist_name, album_title, isrc, mb_id, album_thumb, artist_thumb = row[:8]
            source_track_ids = {
                'spotify': row[column_index['spotify_track_id']] if 'spotify_track_id' in column_index else None,
                'itunes': row[column_index['itunes_track_id']] if 'itunes_track_id' in column_index else None,
                'deezer': row[column_index['deezer_track_id']] if 'deezer_track_id' in column_index else None,
            }
            result.scanned += 1

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Enriching {i + 1} / {total}',
                    log_line=f'Looking up: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )
            found_fields = {}
            resolved_source = None
            resolved_track_id = None

            # Try source-aware track detail lookups only when ISRC enrichment is enabled.
            if fill_isrc and not isrc:
                for source in source_priority:
                    track_source_id = source_track_ids.get(source)
                    if not track_source_id:
                        continue
                    try:
                        client = get_client_for_source(source)
                        if not client or not hasattr(client, 'get_track_details'):
                            continue
                        track_data = client.get_track_details(track_source_id)
                        if track_data:
                            isrc_value = _extract_isrc(track_data)
                            if isrc_value:
                                found_fields['isrc'] = isrc_value
                                resolved_source = source
                                resolved_track_id = track_source_id
                                break
                    except Exception as e:
                        logger.debug("%s enrichment failed for track %s: %s", source.capitalize(), track_id, e)

            # Try MusicBrainz for MB recording ID
            if fill_mb_id and not mb_id and context.mb_client:
                try:
                    recordings = context.mb_client.search_recording(
                        title, artist_name=artist_name, limit=1
                    )
                    if recordings:
                        found_fields['musicbrainz_recording_id'] = recordings[0].get('id', '')
                except Exception as e:
                    logger.debug("MusicBrainz lookup failed for track %s: %s", track_id, e)

            # Create finding for user to review instead of auto-writing
            if found_fields:
                if context.report_progress:
                    context.report_progress(
                        log_line=f'Found: {", ".join(found_fields.keys())} for {title or "Unknown"}',
                        log_type='success'
                    )
                if context.create_finding:
                    try:
                        field_names = ', '.join(found_fields.keys())
                        context.create_finding(
                            job_id=self.job_id,
                            finding_type='metadata_gap',
                            severity='info',
                            entity_type='track',
                            entity_id=str(track_id),
                            file_path=None,
                            title=f'Missing metadata: {title or "Unknown"}',
                            description=(
                                f'Track "{title}" by {artist_name or "Unknown"} is missing: {field_names}. '
                                f'Found values from API lookup.'
                            ),
                            details={
                                'track_id': track_id,
                                'title': title,
                                'artist': artist_name,
                                'album': album_title,
                                'track_ids': source_track_ids,
                                'resolved_source': resolved_source,
                                'resolved_track_id': resolved_track_id,
                                'found_fields': found_fields,
                                'album_thumb_url': album_thumb or None,
                                'artist_thumb_url': artist_thumb or None,
                            }
                        )
                        result.findings_created += 1
                    except Exception as e:
                        logger.debug("Error creating metadata gap finding for track %s: %s", track_id, e)
                        result.errors += 1
            else:
                result.skipped += 1

            # Rate limit API calls
            if fill_isrc and any(source_track_ids.values()):
                if context.sleep_or_stop(0.5):
                    return result

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Metadata gap scan: %d tracks checked, %d gaps found, %d skipped",
                     result.scanned, result.findings_created, result.skipped)
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
                  AND ((isrc IS NULL OR isrc = '')
                    OR (musicbrainz_recording_id IS NULL OR musicbrainz_recording_id = ''))
            """)
            row = cursor.fetchone()
            return min(row[0], 500) if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()


def _extract_isrc(track_data):
    """Extract ISRC from a track detail payload."""
    if not track_data or not isinstance(track_data, dict):
        return None

    external_ids = track_data.get('external_ids')
    if isinstance(external_ids, dict):
        isrc = external_ids.get('isrc')
        if isrc:
            return isrc

    isrc = track_data.get('isrc')
    if isrc:
        return isrc

    raw_data = track_data.get('raw_data')
    if isinstance(raw_data, dict):
        external_ids = raw_data.get('external_ids')
        if isinstance(external_ids, dict) and external_ids.get('isrc'):
            return external_ids['isrc']
        if raw_data.get('isrc'):
            return raw_data['isrc']

    return None


def _track_row_priority(row, column_index, source_priority):
    """Sort rows by the first source track ID available in priority order."""
    source_columns = {
        'spotify': 'spotify_track_id',
        'itunes': 'itunes_track_id',
        'deezer': 'deezer_track_id',
    }

    for idx, source in enumerate(source_priority):
        column_name = source_columns.get(source)
        if not column_name:
            continue
        column_pos = column_index.get(column_name)
        if column_pos is not None and row[column_pos]:
            return idx

    return len(source_priority)
