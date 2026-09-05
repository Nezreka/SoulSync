"""Metadata Gap Filler Job — finds tracks missing key metadata and locates it from APIs."""

from typing import Any, Dict
from core.library2.maintenance_subjects import subject_details
from core.library2.maintenance_subjects import active_file_subjects
from core.metadata_service import get_client_for_source, get_primary_source, get_source_priority
from core.repair_jobs import register_job
from core.repair_jobs.base import get_scope_artist, JobContext, JobResult, RepairJob, scoped_file_subjects
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
    supports_file_scope = True
    supports_artist_scope = True

    def scan(self, context: JobContext) -> JobResult:
        # These three are already imported at module scope. Re-importing them
        # here rebound the names locally, which silently made the module-level
        # ones impossible to substitute — a test that swapped the source
        # priority or the client factory was overridden by the real thing and
        # went out to the live provider instead.
        result = JobResult()
        settings = self._get_settings(context)
        fill_isrc = settings.get("fill_isrc", True)
        fill_mb_id = settings.get("fill_musicbrainz_id", True)
        if not (fill_isrc or fill_mb_id):
            return result
        source_priority = list(get_source_priority(get_primary_source()))
        scope_artist = get_scope_artist(context)
        scope_key = scope_artist.casefold() if scope_artist else None

        subjects: Dict[int, Dict[str, Any]] = {}
        for subject in scoped_file_subjects(context, active_file_subjects(context.db, context.config_manager)):
            track_id = int(subject["track_id"])
            if track_id in subjects and not subject.get("is_primary"):
                continue
            if scope_key and str(subject.get("artist_name") or "").casefold() != scope_key:
                continue
            missing_isrc = fill_isrc and not str(subject.get("isrc") or "").strip()
            missing_mbid = fill_mb_id and not str(
                (subject.get("track_source_ids") or {}).get("musicbrainz") or ""
            ).strip()
            if missing_isrc or missing_mbid:
                subjects[track_id] = subject
        work = list(subjects.values())
        work.sort(key=lambda item: int(item["track_id"]))
        cursor_key = (
            f"repair.metadata_gap.cursor:{int(bool(fill_isrc))}:"
            f"{int(bool(fill_mb_id))}:{scope_key or '*'}"
        )
        cursor = _gap_cursor(context, cursor_key)
        page = [item for item in work if int(item["track_id"]) > cursor][
            :_METADATA_GAP_BATCH
        ]
        if not page and work:
            page = work[:_METADATA_GAP_BATCH]
        work = page
        total = len(work)

        for index, subject in enumerate(work):
            if context.check_stop() or (index % 20 == 0 and context.wait_if_paused()):
                return result
            result.scanned += 1
            source_ids = dict(subject.get("track_source_ids") or {})
            order = list(source_priority)
            order.extend(sorted(set(source_ids) - set(order)))
            found: Dict[str, Any] = {}
            resolved_source = None
            resolved_track_id = None

            if fill_isrc and not subject.get("isrc"):
                for source in order:
                    provider_id = source_ids.get(source)
                    if not provider_id:
                        continue
                    try:
                        client = get_client_for_source(source)
                        getter = getattr(client, "get_track_details", None) if client else None
                        if not callable(getter):
                            continue
                        try:
                            payload = getter(provider_id, allow_fallback=False)
                        except TypeError:
                            payload = getter(provider_id)
                        value = _extract_isrc(payload)
                        if value:
                            found["isrc"] = value
                            resolved_source = source
                            resolved_track_id = provider_id
                            break
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("%s ISRC lookup failed: %s", source, exc)

            if fill_mb_id and not source_ids.get("musicbrainz") and context.mb_client:
                try:
                    rows = context.mb_client.search_recording(
                        subject.get("title"),
                        artist_name=subject.get("artist_name"),
                        limit=1,
                    )
                    if rows and rows[0].get("id"):
                        found["musicbrainz_recording_id"] = rows[0]["id"]
                except Exception as exc:  # noqa: BLE001
                    logger.debug("MusicBrainz recording lookup failed: %s", exc)

            if not found:
                result.skipped += 1
                continue
            details = {
                "track_id": f"lib2:{subject['track_id']}",
                "title": subject.get("title"),
                "artist": subject.get("artist_name"),
                "artist_id": subject.get("artist_id"),
                "album": subject.get("album_title"),
                "track_ids": source_ids,
                "resolved_source": resolved_source,
                "resolved_track_id": resolved_track_id,
                "found_fields": found,
                "album_thumb_url": subject.get("album_image"),
                "artist_thumb_url": subject.get("artist_image"),
            }
            details.update(subject_details(subject))
            if context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="metadata_gap",
                    severity="info",
                    entity_type="track",
                    entity_id=f"lib2:{subject['track_id']}",
                    file_path=subject.get("path"),
                    title=f"Missing metadata: {subject.get('title') or 'Unknown'}",
                    description=(
                        f'Found {", ".join(found)} for "{subject.get("title")}" '
                        f'by {subject.get("artist_name") or "Unknown"}.'
                    ),
                    details=details,
                )
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
        if context.update_progress:
            context.update_progress(total, total)
        if work:
            _gap_cursor(context, cursor_key, int(work[-1]["track_id"]))
        return result

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        try:
            track_ids = set()
            for subject in scoped_file_subjects(context, active_file_subjects(context.db, context.config_manager)):
                ids = subject.get("track_source_ids") or {}
                if not subject.get("isrc") or not ids.get("musicbrainz"):
                    track_ids.add(int(subject["track_id"]))
            return len(track_ids)
        except Exception:
            return 0




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

_METADATA_GAP_BATCH = 500


def _gap_cursor(context: JobContext, key: str, value: int | None = None) -> int:
    """Read or persist the bounded metadata-gap scan cursor."""
    try:
        with context.db._get_connection() as conn:
            if value is None:
                row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
                return int(row[0]) if row else 0
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value,updated_at) "
                "VALUES(?,?,CURRENT_TIMESTAMP)", (key, str(value)),
            )
            conn.commit()
    except Exception:  # Cursor persistence must not disable the repair tool.
        return 0
    return value or 0
