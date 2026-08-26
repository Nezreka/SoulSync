"""Track Number Repair Job — fixes embedded track number tags and filename prefixes.

Detects albums where 3+ files share the same track number (the "all tracks = 01"
bug pattern), then uses cascading API lookups in metadata-source priority order
before falling back to MusicBrainz and AudioDB to resolve the correct tracklist
and repair each file.
"""

import os
import re
from core.library2.provider_ids import parse_external_ids
from core.repair_jobs.base import get_scope_artist
from core.library2.maintenance_subjects import active_file_subjects
from core.library2.maintenance_subjects import subject_details
from core.library2.maintenance_subjects import count_active_files
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from core.metadata_service import (
    get_album_tracks_for_source,
    get_client_for_source,
    get_primary_source,
    get_source_priority,
)
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.track_number")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}

# Placeholder album IDs that are not real API identifiers
_PLACEHOLDER_IDS = {
    'wishlist_album', 'explicit_album', 'explicit_artist',
    'unknown', 'none', 'null', '',
}

_SOURCE_ALBUM_ID_COLUMNS = (
    ('spotify', 'spotify_album_id'),
    ('itunes', 'itunes_album_id'),
    ('deezer', 'deezer_id'),
    ('discogs', 'discogs_id'),
    ('hydrabase', 'soul_id'),
)


@register_job
class TrackNumberRepairJob(RepairJob):
    job_id = 'track_number_repair'
    display_name = 'Track Number Repair'
    description = 'Detects mismatched track numbers using API lookups (dry run by default)'
    help_text = (
        'Scans album folders and compares each file\'s track number against the correct '
        'tracklist from the configured metadata sources. If a file\'s embedded track '
        'number doesn\'t match the API data, the job creates a finding showing what '
        'needs to change.\n\n'
        'In dry run mode (default), no files are modified — you review each proposed change '
        'in the Findings tab and decide what to approve. Disable dry run in settings to let '
        'the job automatically rename and re-number files.\n\n'
        'Settings:\n'
        '- Title Similarity: How closely a filename must match the API track title (0.0 - 1.0)\n'
        '- Dry Run: When enabled, only reports issues without modifying files'
    )
    icon = 'repair-icon-tracknumber'
    default_enabled = True
    default_interval_hours = 24
    default_settings = {
        'anomaly_threshold': 3,
        'title_similarity': 0.80,
        'dry_run': True,
    }
    auto_fix = True
    writes_library_files = True

    def scan(self, context: JobContext) -> JobResult:
        from core.library2.paths import resolve_lib2_path

        result = JobResult()
        settings = self._get_settings(context)
        similarity = float(settings.get("title_similarity", 0.80))
        dry_run = bool(settings.get("dry_run", True))
        scope_artist = get_scope_artist(context)
        scope_key = scope_artist.casefold() if scope_artist else None
        subjects = [
            row for row in active_file_subjects(context.db, context.config_manager)
            if not scope_key or str(row.get("artist_name") or "").casefold() == scope_key
        ]
        by_album: Dict[int, list[Dict[str, Any]]] = {}
        for subject in subjects:
            by_album.setdefault(int(subject["album_id"]), []).append(subject)
        total = len(subjects)

        # Files are the mutation subjects, never the canonical tracklist.
        # Materialize/cache the complete edition list first, then read every
        # track row for the album so missing files still contribute to totals
        # and multi-disc numbering heuristics.
        canonical_by_album: Dict[int, list[Dict[str, Any]]] = {}
        # dd28-18: lib2_albums is the release GROUP; the concrete numbering
        # lives per edition in lib2_release_tracks. Keep both views.
        edition_tracks_by_album: Dict[int, Dict[int, list[Dict[str, Any]]]] = {}
        edition_of_track: Dict[int, list[int]] = {}
        conn = context.db._get_connection()
        try:
            from core.library2.completeness import resolve_tracklist

            for album_id in by_album:
                try:
                    resolve_tracklist(context.config_manager, conn, album_id)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "Canonical tracklist resolution failed for album %s: %s",
                        album_id, exc,
                    )
                canonical_by_album[album_id] = [dict(row) for row in conn.execute(
                    """SELECT id AS lib2_track_id, title AS name,
                              track_number, COALESCE(disc_number, 1) AS disc_number
                         FROM lib2_tracks WHERE album_id=?
                     ORDER BY COALESCE(disc_number, 1), track_number, id""",
                    (album_id,),
                ).fetchall()]
                edition_tracks, track_edition = _edition_tracklists(conn, album_id)
                if edition_tracks:
                    edition_tracks_by_album[album_id] = edition_tracks
                    edition_of_track.update(track_edition)
        finally:
            conn.close()

        for album_id, album_subjects in by_album.items():
            group_tracks = [
                row for row in canonical_by_album.get(album_id, [])
                if row.get("track_number") is not None
            ]
            editions = edition_tracks_by_album.get(album_id) or {}
            if not group_tracks:
                result.skipped += len(album_subjects)
                continue
            for subject in album_subjects:
                if context.check_stop() or context.wait_if_paused():
                    return result
                result.scanned += 1
                # dd28-18: with a standard + deluxe pressing in one release
                # group, the union of both tracklists made disc_total count e.g.
                # 28 instead of 12, so ``N/28`` was written into every file —
                # and duplicate titles across editions let the title matcher
                # pick an arbitrary edition's track number. With dry_run False
                # that also renamed files: deterministic, unreviewed corruption
                # over a whole class of albums. Judge each file against ITS
                # edition, and refuse to guess when the edition is ambiguous.
                api_tracks = _api_tracks_for_subject(
                    subject, group_tracks, editions, edition_of_track,
                )
                if not api_tracks:
                    result.skipped += 1
                    continue
                raw_path = str(subject.get("path") or "")
                resolved = raw_path if os.path.isfile(raw_path) else resolve_lib2_path(
                    raw_path, config_manager=context.config_manager,
                )
                if not resolved or not os.path.isfile(resolved):
                    result.skipped += 1
                    continue
                try:
                    finding = _check_single_track(
                        resolved, os.path.basename(resolved), api_tracks, similarity,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Track-number inspection failed for %s: %s", raw_path, exc)
                    result.errors += 1
                    continue
                if not finding:
                    result.skipped += 1
                    continue
                details = dict(finding["details"])
                details.update(subject_details(subject))
                details.update({
                    "track_id": f"lib2:{subject['track_id']}",
                    "file_id": int(subject["file_id"]),
                    "title": subject.get("title"),
                    "artist": subject.get("artist_name"),
                    "artist_id": subject.get("artist_id"),
                    "album": subject.get("album_title"),
                    "album_thumb_url": subject.get("album_image"),
                    "artist_thumb_url": subject.get("artist_image"),
                })
                if dry_run:
                    if context.create_finding:
                        inserted = context.create_finding(
                            job_id=self.job_id,
                            finding_type="track_number_mismatch",
                            severity="warning",
                            entity_type="track",
                            entity_id=f"lib2:{subject['track_id']}",
                            file_path=raw_path,
                            title=f"Track number mismatch: {subject.get('title') or 'Unknown'}",
                            description=finding["description"],
                            details=details,
                        )
                        if inserted:
                            result.findings_created += 1
                        else:
                            result.findings_skipped_dedup += 1
                    continue

                try:
                    if not details.get("tag_ok", False):
                        # iss29-E07: the rename is only correct if the tag write
                        # actually landed. `save_audio_file` returns False when
                        # its integrity check aborts the swap — the original is
                        # left untouched and the tags are NOT written. Renaming
                        # anyway produced a file called `07 - Song.flac` still
                        # carrying TRCK 3, and `fix_finding` then resolved the
                        # finding, so the scanner would never look at it again.
                        if not _fix_track_number_tag(
                            resolved,
                            int(details["correct_track_num"]),
                            int(details.get("total_tracks") or 0),
                        ):
                            logger.warning(
                                "Track-number tag write aborted for %s — leaving the "
                                "filename alone so the finding stays actionable",
                                resolved,
                            )
                            result.errors += 1
                            continue
                    new_path = None
                    new_filename = details.get("new_filename")
                    if new_filename:
                        # iss29-E08: distinguish "already named correctly" from
                        # "refused to rename". Both used to arrive as None and
                        # both were then counted as auto_fixed, so a collision
                        # with an existing destination resolved the finding for
                        # a file that still had the wrong name.
                        from core.repair_jobs.track_number_repair import (
                            rename_to_basename_result,
                        )

                        new_path, rename_error = rename_to_basename_result(
                            resolved,
                            os.path.basename(resolved),
                            os.path.splitext(str(new_filename))[0],
                        )
                        if rename_error:
                            logger.warning(
                                "Track-number rename refused for %s: %s", resolved,
                                rename_error,
                            )
                            result.errors += 1
                            continue
                    if new_path:
                        # dd28-29: the rename already happened on disk. If the
                        # separate DB write fails there is no rollback, so the
                        # catalog keeps pointing at a path that no longer exists
                        # and ``report_change`` is skipped too — nothing left to
                        # reconcile it. Put the file back instead, so disk and
                        # catalog stay in the one consistent state we still have.
                        conn = context.db._get_connection()
                        try:
                            conn.execute(
                                "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                                "WHERE id=?",
                                (new_path, int(subject["file_id"])),
                            )
                            conn.commit()
                        except Exception:
                            try:
                                os.replace(new_path, resolved)
                                # iss29-E09: the rename carried the .lrc along,
                                # so the rollback has to bring it back too —
                                # otherwise the audio returns to its old name
                                # and the lyrics stay orphaned at the new one,
                                # where nothing looks for them.
                                _restore_sidecar(new_path, resolved)
                                logger.warning(
                                    "Rolled back rename of %s: catalog update failed",
                                    new_path,
                                )
                            except OSError as undo_exc:
                                logger.error(
                                    "Rename of %s could not be rolled back after a "
                                    "failed catalog update: %s", new_path, undo_exc,
                                )
                            raise
                        finally:
                            conn.close()
                    result.auto_fixed += 1
                    if context.report_change:
                        context.report_change(
                            finding_type="track_number_mismatch",
                            action="fixed_track_number",
                            entity_type="track",
                            entity_id=f"lib2:{subject['track_id']}",
                            file_path=new_path or raw_path,
                            details=details,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Native track-number repair failed for %s: %s", raw_path, exc)
                    result.errors += 1
                if context.update_progress:
                    context.update_progress(result.scanned, total)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        return count_active_files(context.db, context.config_manager)

    def _get_settings(self, context: JobContext) -> dict:
        """Read job settings from config, falling back to defaults."""
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    # ------------------------------------------------------------------
    # Album-level repair
    # ------------------------------------------------------------------
    def _repair_album(self, folder_path: str, filenames: List[str],
                      anomaly_threshold: int, context: JobContext,
                      scan_state: dict = None) -> JobResult:
        from mutagen import File as MutagenFile

        if scan_state is None:
            scan_state = {'album_tracks_cache': {}, 'title_similarity': 0.80}

        result = JobResult()

        # Step 0: Anomaly detection. Keyed on (disc, track): a multi-disc album
        # stored flat in one folder legitimately repeats every track number once
        # per disc (disc 1 track 1, disc 2 track 1, …) — counting bare track
        # numbers declared a perfectly-tagged 5-disc box set anomalous (#1009).
        # Untagged discs fall back to 1, so the real all-tracks-say-01 bug this
        # job exists for still trips the threshold exactly as before.
        track_num_counts: Dict[Tuple[int, int], int] = {}
        file_track_data: List[Tuple[str, str, Optional[int], Optional[int]]] = []

        for fname in filenames:
            fpath = os.path.join(folder_path, fname)
            try:
                audio = MutagenFile(fpath)
                if audio is None:
                    file_track_data.append((fpath, fname, None, None))
                    continue
                track_num, _ = _read_track_number_tag(audio)
                disc_num, _ = _read_disc_number_tag(audio)
                file_track_data.append((fpath, fname, track_num, disc_num))
                if track_num is not None:
                    key = (disc_num or 1, track_num)
                    track_num_counts[key] = track_num_counts.get(key, 0) + 1
            except Exception:
                file_track_data.append((fpath, fname, None, None))

        has_anomaly = any(count >= anomaly_threshold for count in track_num_counts.values())
        if not has_anomaly:
            result.scanned += len(filenames)
            return result

        duped = {num: cnt for num, cnt in track_num_counts.items() if cnt >= anomaly_threshold}
        logger.info("Anomaly detected in %s — %d files share track number(s): %s",
                     os.path.basename(folder_path), sum(duped.values()), duped)

        # Resolve album tracklist via source-aware cascading fallbacks
        api_tracks = self._resolve_album_tracklist(file_track_data, folder_path, context, scan_state)
        if not api_tracks:
            result.skipped += len(filenames)
            result.scanned += len(filenames)
            return result

        # Process each file
        title_sim = scan_state.get('title_similarity', 0.80)
        dry_run = scan_state.get('dry_run', True)

        # Look up album/artist art once per album folder for enriched findings
        art_info = _lookup_album_artist_art(file_track_data, context) if dry_run else {}

        for fpath, fname, _, _ in file_track_data:
            if context.check_stop():
                return result

            result.scanned += 1
            try:
                if dry_run:
                    finding = _check_single_track(fpath, fname, api_tracks, title_sim)
                    if finding:
                        if context.create_finding:
                            details = finding['details']
                            # Enrich with album/artist art and names
                            if art_info.get('album_thumb_url'):
                                details['album_thumb_url'] = art_info['album_thumb_url']
                            if art_info.get('artist_thumb_url'):
                                details['artist_thumb_url'] = art_info['artist_thumb_url']
                            if art_info.get('album_title'):
                                details['album_title'] = art_info['album_title']
                            if art_info.get('artist_name'):
                                details['artist_name'] = art_info['artist_name']
                            if art_info.get('artist_id') is not None:
                                details['artist_id'] = art_info['artist_id']
                            inserted = context.create_finding(
                                job_id=self.job_id,
                                finding_type='track_number_mismatch',
                                severity='warning',
                                entity_type='file',
                                entity_id=None,
                                file_path=fpath,
                                title=f'Track number fix: {os.path.basename(fpath)}',
                                description=finding['description'],
                                details=details
                            )
                            if inserted:
                                result.findings_created += 1
                            else:
                                result.findings_skipped_dedup += 1
                else:
                    if _repair_single_track(fpath, fname, api_tracks, title_sim, context):
                        result.auto_fixed += 1
            except Exception as e:
                logger.error("Error repairing %s: %s", fpath, e, exc_info=True)
                result.errors += 1

        return result

    # ------------------------------------------------------------------
    # Tracklist resolution (7-level fallback cascade)
    # ------------------------------------------------------------------
    def _resolve_album_tracklist(self, file_track_data: List[Tuple[str, str, Optional[int]]],
                                 folder_path: str, context: JobContext,
                                 scan_state: dict = None) -> Optional[List[Dict]]:
        if scan_state is None:
            scan_state = {'album_tracks_cache': {}, 'title_similarity': 0.80}

        cache = scan_state['album_tracks_cache']
        folder_name = os.path.basename(folder_path)
        primary_source = get_primary_source()
        source_priority = get_source_priority(primary_source)

        # Fallback -1 (#765): a pinned canonical release wins over the whole
        # cascade below — so Track Number Repair resolves the SAME release the
        # Reorganizer does (Stage 3) and the two stop contradicting each other.
        # Gated on the album carrying a canonical; everything below is untouched
        # for albums without one (preserving the all-01-album rescue this job
        # exists for — the regression we refused to take in a reactive fix).
        canonical = _lookup_canonical_from_db(file_track_data, context)
        if canonical:
            c_source, c_id = canonical
            if _is_valid_album_id(c_id):
                tracks = _get_album_tracklist(c_source, c_id, cache)
                if tracks:
                    logger.info("[Repair] %s — resolved via canonical %s album ID: %s",
                                folder_name, c_source, c_id)
                    return tracks

        # Fallback 0: Check DB first. If any tracked file already has source IDs,
        # prefer the configured source order and use the first available album ID.
        source_album_ids = _lookup_album_ids_from_db(file_track_data, context)

        # Collect available IDs from file tags (fallback when DB has no IDs)
        spotify_track_id = None
        mb_album_id = None
        album_name = None
        artist_name = None

        for fpath, *_rest in file_track_data:
            if 'spotify' not in source_album_ids or 'itunes' not in source_album_ids:
                aid, source = _read_album_id_from_file(fpath)
                if aid and source in ('spotify', 'itunes') and source not in source_album_ids:
                    source_album_ids[source] = aid

            if not spotify_track_id:
                spotify_track_id = _read_spotify_track_id_from_file(fpath)

            if not mb_album_id:
                mb_album_id = _read_musicbrainz_album_id_from_file(fpath)

            if not album_name:
                album_name, artist_name = _read_album_artist_from_file(fpath)

            if source_album_ids and spotify_track_id and mb_album_id and album_name:
                break

        # Fallback 1: Album IDs from DB / file tags, using source priority
        for source in source_priority:
            album_id = source_album_ids.get(source)
            if album_id and _is_valid_album_id(album_id):
                tracks = _get_album_tracklist(source, album_id, cache)
                if tracks:
                    logger.info("[Repair] %s — resolved via %s album ID: %s",
                                folder_name, source, album_id)
                    return tracks

        # Fallback 2: Spotify track ID → discover album ID
        client = get_client_for_source('spotify')
        if spotify_track_id and client:
            try:
                track_details = client.get_track_details(spotify_track_id)
                if track_details and track_details.get('album', {}).get('id'):
                    real_album_id = track_details['album']['id']
                    tracks = _get_album_tracklist('spotify', real_album_id, cache)
                    if tracks:
                        logger.info("[Repair] %s — resolved via Spotify track ID %s → album %s",
                                    folder_name, spotify_track_id, real_album_id)
                        return tracks
            except Exception as e:
                logger.debug("Spotify track lookup failed for %s: %s", spotify_track_id, e)

        # Fallback 3: Search metadata sources by album name + artist
        if album_name:
            query = f"{artist_name} {album_name}" if artist_name else album_name
            for source in source_priority:
                client = get_client_for_source(source)
                if not client or not hasattr(client, 'search_albums'):
                    continue
                try:
                    results = client.search_albums(query, limit=5)
                    if results:
                        best = results[0]
                        best_album_id = getattr(best, 'id', None) if not isinstance(best, dict) else best.get('id')
                        if best_album_id:
                            tracks = _get_album_tracklist(source, str(best_album_id), cache)
                            if tracks:
                                logger.info("[Repair] %s — resolved via %s album search: '%s' → %s",
                                            folder_name, source, query, best_album_id)
                                return tracks
                except Exception as e:
                    logger.debug("%s album search failed for '%s': %s", source.capitalize(), album_name, e)

        # Fallback 4: MusicBrainz album ID from tags
        if mb_album_id:
            tracks = _get_tracklist_from_musicbrainz(mb_album_id, context, cache)
            if tracks:
                logger.info("[Repair] %s — resolved via MusicBrainz album ID: %s", folder_name, mb_album_id)
                return tracks

        # Fallback 5: AudioDB → MusicBrainz
        if album_name and artist_name:
            adb_mb_id = _get_musicbrainz_id_via_audiodb(artist_name, album_name, context)
            if adb_mb_id and adb_mb_id != mb_album_id:
                tracks = _get_tracklist_from_musicbrainz(adb_mb_id, context, cache)
                if tracks:
                    logger.info("[Repair] %s — resolved via AudioDB → MusicBrainz: %s",
                                folder_name, adb_mb_id)
                    return tracks

        logger.warning("[Repair] %s — all tracklist resolution strategies exhausted", folder_name)
        return None

    # ------------------------------------------------------------------
    # Batch scan support (called by RepairWorker.process_batch)
    # ------------------------------------------------------------------
    def scan_folders(self, folders: List[str], context: JobContext) -> JobResult:
        """Scan specific folders only (for batch post-download repair)."""
        result = JobResult()
        settings = self._get_settings(context)
        anomaly_threshold = settings.get('anomaly_threshold', 3)

        # Thread-local state (not on self — avoids race with concurrent scan())
        scan_state = {
            'album_tracks_cache': {},
            'title_similarity': settings.get('title_similarity', 0.80),
            'dry_run': settings.get('dry_run', True),
        }

        for folder_path in folders:
            if context.check_stop():
                break
            if not os.path.isdir(folder_path):
                continue
            filenames = [
                f for f in os.listdir(folder_path)
                if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
            ]
            if not filenames:
                continue

            try:
                folder_result = self._repair_album(folder_path, filenames, anomaly_threshold, context, scan_state)
                result.scanned += folder_result.scanned
                result.auto_fixed += folder_result.auto_fixed
                result.skipped += folder_result.skipped
                result.errors += folder_result.errors
            except Exception as e:
                logger.error("[Repair] Error scanning %s: %s", folder_path, e, exc_info=True)
                result.errors += 1

        return result


# ======================================================================
# Module-level helper functions (extracted from old RepairWorker methods)
# ======================================================================

def _read_track_number_tag(audio) -> Tuple[Optional[int], Optional[int]]:
    """Read track number and total from tags. Returns (track_num, total)."""
    from mutagen.id3 import ID3
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4

    try:
        if hasattr(audio, 'tags') and audio.tags is not None:
            if isinstance(audio.tags, ID3):
                frames = audio.tags.getall('TRCK')
                if frames and frames[0].text:
                    return _parse_track_str(str(frames[0].text[0]))
            elif isinstance(audio, (FLAC, OggVorbis)):
                val = audio.get('tracknumber')
                if val:
                    return _parse_track_str(str(val[0]))
            elif isinstance(audio, MP4):
                val = audio.tags.get('trkn')
                if val and val[0]:
                    t = val[0]
                    return (int(t[0]), int(t[1]) if t[1] else None)
    except Exception as e:
        logger.debug("Error reading track number tag: %s", e)
    return None, None


def _parse_track_str(s: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse '5/12' or '5' into (track_num, total)."""
    try:
        if '/' in s:
            parts = s.split('/')
            return int(parts[0]), int(parts[1])
        return int(s), None
    except (ValueError, IndexError):
        return None, None


def _read_disc_number_tag(audio) -> Tuple[Optional[int], Optional[int]]:
    """Read disc number and total discs from tags. Returns (disc_num, total)."""
    from mutagen.id3 import ID3
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4

    try:
        if hasattr(audio, 'tags') and audio.tags is not None:
            if isinstance(audio.tags, ID3):
                frames = audio.tags.getall('TPOS')
                if frames and frames[0].text:
                    return _parse_track_str(str(frames[0].text[0]))
            elif isinstance(audio, (FLAC, OggVorbis)):
                val = audio.get('discnumber')
                if val:
                    return _parse_track_str(str(val[0]))
            elif isinstance(audio, MP4):
                val = audio.tags.get('disk')
                if val and val[0]:
                    d = val[0]
                    return (int(d[0]), int(d[1]) if d[1] else None)
    except Exception as e:
        logger.debug("Error reading disc number tag: %s", e)
    return None, None


def _api_disc_count(api_tracks: List[Dict]) -> int:
    """The number of discs the API tracklist spans (1 for single-disc albums)."""
    count = 1
    for t in api_tracks:
        try:
            d = int(t.get('disc_number') or 1)
        except (TypeError, ValueError):
            d = 1
        if d > count:
            count = d
    return count


def _api_disc_of(track: Dict) -> int:
    try:
        return int(track.get('disc_number') or 1)
    except (TypeError, ValueError):
        return 1


def _read_title_tag(audio) -> Optional[str]:
    """Read the title tag from an already-opened Mutagen file."""
    from mutagen.id3 import ID3
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4

    try:
        if hasattr(audio, 'tags') and audio.tags is not None:
            if isinstance(audio.tags, ID3):
                frames = audio.tags.getall('TIT2')
                if frames and frames[0].text:
                    return str(frames[0].text[0])
            elif isinstance(audio, (FLAC, OggVorbis)):
                val = audio.get('title')
                if val:
                    return str(val[0])
            elif isinstance(audio, MP4):
                val = audio.tags.get('\xa9nam')
                if val:
                    return str(val[0])
    except Exception as e:
        logger.debug("Error reading title tag: %s", e)
    return None


def _read_album_id_from_file(file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Read SPOTIFY_ALBUM_ID or ITUNES_ALBUM_ID from embedded tags.
    Returns (album_id, source) where source is 'spotify' or 'itunes'."""
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        audio = MutagenFile(file_path)
        if audio is None:
            return None, None

        if hasattr(audio, 'tags') and audio.tags is not None:
            if isinstance(audio.tags, ID3):
                for key in ['TXXX:SPOTIFY_ALBUM_ID', 'TXXX:spotify_album_id']:
                    frame = audio.tags.getall(key)
                    if frame and frame[0].text:
                        return str(frame[0].text[0]), 'spotify'
                for key in ['TXXX:ITUNES_ALBUM_ID', 'TXXX:itunes_album_id']:
                    frame = audio.tags.getall(key)
                    if frame and frame[0].text:
                        return str(frame[0].text[0]), 'itunes'

            elif isinstance(audio, (FLAC, OggVorbis)):
                for key in ['spotify_album_id', 'SPOTIFY_ALBUM_ID']:
                    val = audio.get(key)
                    if val:
                        return str(val[0]), 'spotify'
                for key in ['itunes_album_id', 'ITUNES_ALBUM_ID']:
                    val = audio.get(key)
                    if val:
                        return str(val[0]), 'itunes'

            elif isinstance(audio, MP4):
                for key in ['----:com.apple.iTunes:SPOTIFY_ALBUM_ID',
                            '----:com.apple.iTunes:spotify_album_id']:
                    val = audio.tags.get(key)
                    if val:
                        raw = val[0]
                        return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw), 'spotify'
                for key in ['----:com.apple.iTunes:ITUNES_ALBUM_ID',
                            '----:com.apple.iTunes:itunes_album_id']:
                    val = audio.tags.get(key)
                    if val:
                        raw = val[0]
                        return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw), 'itunes'

    except Exception as e:
        logger.debug("Error reading album ID from %s: %s", file_path, e)
    return None, None


def _is_valid_album_id(album_id: Optional[str]) -> bool:
    """Check if an album ID is a real API identifier, not a placeholder."""
    if not album_id:
        return False
    if album_id.strip().lower() in _PLACEHOLDER_IDS:
        return False
    if len(album_id.strip()) < 5:
        return False
    return True


def _read_spotify_track_id_from_file(file_path: str) -> Optional[str]:
    """Read SPOTIFY_TRACK_ID from embedded tags."""
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        audio = MutagenFile(file_path)
        if audio is None:
            return None

        if hasattr(audio, 'tags') and audio.tags is not None:
            if isinstance(audio.tags, ID3):
                for key in ['TXXX:SPOTIFY_TRACK_ID', 'TXXX:spotify_track_id']:
                    frame = audio.tags.getall(key)
                    if frame and frame[0].text:
                        return str(frame[0].text[0])
            elif isinstance(audio, (FLAC, OggVorbis)):
                for key in ['spotify_track_id', 'SPOTIFY_TRACK_ID']:
                    val = audio.get(key)
                    if val:
                        return str(val[0])
            elif isinstance(audio, MP4):
                for key in ['----:com.apple.iTunes:SPOTIFY_TRACK_ID',
                            '----:com.apple.iTunes:spotify_track_id']:
                    val = audio.tags.get(key)
                    if val:
                        raw = val[0]
                        return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw)

    except Exception as e:
        logger.debug("Error reading Spotify track ID from %s: %s", file_path, e)
    return None


def _read_musicbrainz_album_id_from_file(file_path: str) -> Optional[str]:
    """Read MusicBrainz Album Id (release MBID) from embedded tags."""
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        audio = MutagenFile(file_path)
        if audio is None:
            return None

        if hasattr(audio, 'tags') and audio.tags is not None:
            if isinstance(audio.tags, ID3):
                for key in ['TXXX:MusicBrainz Album Id', 'TXXX:MUSICBRAINZ_ALBUMID',
                            'TXXX:musicbrainz_albumid']:
                    frame = audio.tags.getall(key)
                    if frame and frame[0].text:
                        return str(frame[0].text[0])
            elif isinstance(audio, (FLAC, OggVorbis)):
                for key in ['musicbrainz_albumid', 'MUSICBRAINZ_ALBUMID',
                            'MusicBrainz Album Id']:
                    val = audio.get(key)
                    if val:
                        return str(val[0])
            elif isinstance(audio, MP4):
                for key in ['----:com.apple.iTunes:MusicBrainz Album Id',
                            '----:com.apple.iTunes:MUSICBRAINZ_ALBUMID',
                            '----:com.apple.music.albums:MUSICBRAINZ_ALBUMID']:
                    val = audio.tags.get(key)
                    if val:
                        raw = val[0]
                        return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw)

    except Exception as e:
        logger.debug("Error reading MusicBrainz album ID from %s: %s", file_path, e)
    return None


def _read_album_artist_from_file(file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Read album name and artist name from embedded tags.
    Returns (album_name, artist_name)."""
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        audio = MutagenFile(file_path)
        if audio is None:
            return None, None

        album_name = None
        artist_name = None

        if hasattr(audio, 'tags') and audio.tags is not None:
            if isinstance(audio.tags, ID3):
                frames = audio.tags.getall('TALB')
                if frames and frames[0].text:
                    album_name = str(frames[0].text[0])
                for tag in ['TPE2', 'TPE1']:
                    frames = audio.tags.getall(tag)
                    if frames and frames[0].text:
                        artist_name = str(frames[0].text[0])
                        break
            elif isinstance(audio, (FLAC, OggVorbis)):
                val = audio.get('album')
                if val:
                    album_name = str(val[0])
                for key in ['albumartist', 'artist']:
                    val = audio.get(key)
                    if val:
                        artist_name = str(val[0])
                        break
            elif isinstance(audio, MP4):
                val = audio.tags.get('\xa9alb')
                if val:
                    album_name = str(val[0])
                for key in ['aART', '\xa9ART']:
                    val = audio.tags.get(key)
                    if val:
                        artist_name = str(val[0])
                        break

        return album_name, artist_name
    except Exception as e:
        logger.debug("Error reading album/artist from %s: %s", file_path, e)
    return None, None


def _match_disc_aware(query: str, api_tracks: List[Dict], threshold: float,
                      file_disc: Optional[int], multi_disc: bool) -> Tuple[Optional[Dict], float]:
    """Fuzzy title match that prefers the file's own disc on multi-disc albums.

    #1009: two discs of a box set can carry same/similar titles and per-disc
    track numbers repeat; matching across the whole album picks an arbitrary
    disc. When the file says which disc it's on (tag or a DDTT filename
    prefix), same-disc candidates are tried first; a miss there still falls
    back to the full tracklist (a WRONG disc tag mustn't block the repair)."""
    if multi_disc and file_disc:
        same_disc = [t for t in api_tracks if _api_disc_of(t) == file_disc]
        if same_disc:
            matched, score = _match_title_to_api_track(query, same_disc, threshold)
            if matched:
                return matched, score
    return _match_title_to_api_track(query, api_tracks, threshold)


def _planned_prefix(prefix: str, correct_num: int, correct_disc: int,
                    multi_disc: bool) -> Optional[str]:
    """The corrected filename prefix, PRESERVING the file's own convention.

    #1009: the old logic replaced the first 1-3 digits of the prefix with the
    2-digit track — so a 4-digit disc+track name like '0213 - X' (disc 2,
    track 13; the $disc$track template) became '133 - X': it swallowed '021',
    wrote '13', and left the stray '3' behind. The reporter read that stray
    digit as "a digit from the album's total track count"; it's really the
    tail of their own prefix.

    - 4-digit prefix on a multi-disc album → the $disc$track convention:
      rebuild as DDTT from the MATCHED track's disc + number.
    - 1-3 digit prefix → plain track number (both conventions pad to 2).
    - 4 digits on a single-disc album (a year: '1999 - ...') or 5+ digits →
      not a track prefix we understand; leave the filename alone.
    Returns the new prefix, or None when the filename must not be touched.
    """
    if not prefix:
        return None
    if len(prefix) == 4:
        if not multi_disc:
            return None
        return f"{correct_disc:02d}{correct_num:02d}"
    if len(prefix) <= 3:
        return f"{correct_num:02d}"
    return None


def _plan_track_repair(file_path: str, filename: str, api_tracks: List[Dict],
                       title_similarity: float) -> Optional[Dict]:
    """Work out what (if anything) needs fixing for one file. Shared by the
    dry-run check and the live repair so the finding can never promise a
    different change than the fix applies.

    Returns None when the file couldn't be matched or is already correct,
    else a dict with the matched track, corrected numbers, per-disc total,
    and the planned new basename (None = filename untouched)."""
    from mutagen import File as MutagenFile

    audio = MutagenFile(file_path)
    if audio is None:
        return None

    multi_disc = _api_disc_count(api_tracks) > 1
    basename = os.path.splitext(filename)[0]
    prefix_match = re.match(r'^(\d+)', basename.strip())
    prefix = prefix_match.group(1) if prefix_match else ''

    tag_disc, _tag_disc_total = _read_disc_number_tag(audio)
    file_disc = tag_disc
    # a DDTT filename prefix reveals the disc when the tag doesn't (matching
    # only — an inferred disc is not a tag, so it never satisfies disc_ok)
    if not file_disc and multi_disc and len(prefix) == 4:
        file_disc = int(prefix[:2]) or None

    file_title = _read_title_tag(audio)
    matched_track, match_score = (None, 0.0)
    if file_title:
        matched_track, match_score = _match_disc_aware(
            file_title, api_tracks, title_similarity, file_disc, multi_disc)
    if not matched_track:
        # strip the WHOLE leading digit run ('0213 - X' → 'X', not '3 - X')
        clean_name = re.sub(r'^\d+[\s.\-_]*', '', basename).strip()
        if clean_name:
            matched_track, match_score = _match_disc_aware(
                clean_name, api_tracks, title_similarity, file_disc, multi_disc)
    if not matched_track:
        return None

    correct_num = matched_track.get('track_number')
    if correct_num is None:
        return None
    correct_disc = _api_disc_of(matched_track)
    # tag totals are PER DISC ('13/20'), matching standard tagging — the old
    # whole-album total is also accepted below so files it wrote stay quiet
    disc_total = sum(1 for t in api_tracks if _api_disc_of(t) == correct_disc)

    current_num, current_total = _read_track_number_tag(audio)
    tag_ok = (current_num == correct_num
              and current_total in (None, disc_total, len(api_tracks)))
    # #1075: per-disc track numbers are meaningless without disc tags — a
    # repair that writes "track 10 of 11" onto a disc-tagless file in a
    # 3-disc folder just manufactures a duplicate track number. On multi-disc
    # albums the DISC TAG is part of correctness; single-disc albums never
    # get disc tags touched.
    total_discs = _api_disc_count(api_tracks)
    disc_ok = (not multi_disc) or (tag_disc == correct_disc)

    planned = _planned_prefix(prefix, correct_num, correct_disc, multi_disc)
    new_basename = None
    if planned is not None and prefix:
        candidate = re.sub(r'^\d+', planned, basename, count=1)
        if candidate != basename:
            new_basename = candidate

    if tag_ok and disc_ok and new_basename is None:
        return None
    return {
        'matched_track': matched_track,
        'match_score': match_score,
        'correct_num': correct_num,
        'correct_disc': correct_disc,
        'disc_total': disc_total,
        'total_discs': total_discs,
        'multi_disc': multi_disc,
        'current_num': current_num,
        'current_total': current_total,
        'current_disc': tag_disc,
        'tag_ok': tag_ok,
        'disc_ok': disc_ok,
        'new_basename': new_basename,
        'file_title': file_title,
    }


def _match_title_to_api_track(file_title: str, api_tracks: List[Dict],
                               threshold: float) -> Tuple[Optional[Dict], float]:
    """Fuzzy-match a file title to an API track. Returns (track, score).

    The primary score compares QUALIFIER-STRIPPED titles (remaster noise must
    not break matching), but that makes every version of a song identical —
    "Like Spinning Plates ('Why Us?' Version)" and "Like Spinning Plates"
    both normalize to the same string, and first-in-tracklist used to win
    (#1075: the file got renumbered to the WRONG disc's original version).
    Ties on the stripped score are broken by the RAW title, so the version
    whose full name actually matches the file wins."""
    norm_file = _normalize_title(file_title)
    raw_file = file_title.lower().strip()
    best_match = None
    best_key = (-1.0, -1.0)

    for track in api_tracks:
        api_name = track.get('name', '')
        norm_score = SequenceMatcher(None, norm_file, _normalize_title(api_name)).ratio()
        raw_score = SequenceMatcher(None, raw_file, api_name.lower().strip()).ratio()
        key = (norm_score, raw_score)
        if key > best_key:
            best_key = key
            best_match = track

    if best_key[0] >= threshold:
        return best_match, best_key[0]
    return None, max(best_key[0], 0.0)


def _normalize_title(title: str) -> str:
    """Normalize a title for comparison."""
    t = title.lower()
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'[^a-z0-9 ]', '', t)
    return t.strip()


def _fix_track_number_tag(file_path: str, correct_num: int, total: int) -> bool:
    """Update ONLY the track number tag in the file.

    Returns True only when the tag actually reached the file on disk
    (iss29-E07) — callers gate the follow-up rename on this.
    """
    from mutagen import File as MutagenFile
    from mutagen.id3 import TRCK, ID3
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4

    try:
        audio = MutagenFile(file_path)
        if audio is None:
            logger.error("Cannot re-open file for tag fix: %s", file_path)
            return False

        track_str = f"{correct_num}/{total}"

        if isinstance(audio.tags, ID3):
            audio.tags.delall('TRCK')
            audio.tags.add(TRCK(encoding=3, text=[track_str]))
        elif isinstance(audio, (FLAC, OggVorbis)):
            audio['tracknumber'] = [track_str]
        elif isinstance(audio, MP4):
            audio['trkn'] = [(correct_num, total)]
        else:
            return False

        # Atomic + audio-integrity-verified save (#819/#1000): never rewrite the
        # user's library file in place; abort if the write would damage the audio.
        #
        # iss29-E07: the return value MUST be propagated. `save_audio_file`
        # returns False for "integrity check failed, original untouched, tags
        # NOT written" — discarding that reported an unwritten tag as fixed,
        # and in the native P3 path it let the rename proceed, leaving a file
        # named `07 - Song.flac` carrying TRCK 3 with the finding resolved so
        # nothing would ever look at it again.
        from core.metadata.common import save_audio_file, get_mutagen_symbols
        if not save_audio_file(audio, get_mutagen_symbols()):
            logger.error("Track tag NOT written (atomic save aborted): %s", file_path)
            return False

        logger.info("Fixed track tag: %s → %s", os.path.basename(file_path), track_str)
        return True
    except Exception as e:
        logger.error("Error fixing track tag in %s: %s", file_path, e, exc_info=True)
        return False


def _fix_disc_number_tag(file_path: str, disc_num: int, total_discs: int) -> bool:
    """Update ONLY the disc number tag (multi-disc albums — #1075: per-disc
    track numbering is only enforceable when the disc tag rides along).

    Returns True only when the tag actually reached the file (iss29-E07).
    """
    from mutagen import File as MutagenFile
    from mutagen.id3 import TPOS, ID3
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4

    try:
        audio = MutagenFile(file_path)
        if audio is None:
            logger.error("Cannot re-open file for disc tag fix: %s", file_path)
            return False

        disc_str = f"{disc_num}/{total_discs}" if total_discs else str(disc_num)

        if isinstance(audio.tags, ID3):
            audio.tags.delall('TPOS')
            audio.tags.add(TPOS(encoding=3, text=[disc_str]))
        elif isinstance(audio, (FLAC, OggVorbis)):
            audio['discnumber'] = [disc_str]
            if total_discs:
                audio['disctotal'] = [str(total_discs)]
        elif isinstance(audio, MP4):
            audio['disk'] = [(disc_num, total_discs or 0)]
        else:
            return False

        # Atomic + audio-integrity-verified save (#819/#1000). The result is
        # propagated for the same reason as in `_fix_track_number_tag`
        # (iss29-E07).
        from core.metadata.common import save_audio_file, get_mutagen_symbols
        if not save_audio_file(audio, get_mutagen_symbols()):
            logger.error("Disc tag NOT written (atomic save aborted): %s", file_path)
            return False

        logger.info("Fixed disc tag: %s → %s", os.path.basename(file_path), disc_str)
        return True
    except Exception as e:
        logger.error("Error fixing disc tag in %s: %s", file_path, e, exc_info=True)
        return False


def rename_to_basename_result(
    file_path: str, filename: str, new_basename: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Rename a file to the planned basename (extension kept).

    Returns ``(new_path, error)``:

    * ``(path, None)`` — renamed.
    * ``(None, None)`` — nothing to do; the name is already correct.
    * ``(None, "…")`` — the rename was NOT performed and the reason why.

    iss29-E08: :func:`_rename_to_basename` collapses all three onto ``None``,
    so a collision with an existing destination — or a source that vanished —
    was indistinguishable from "already named correctly" and got counted as a
    successful fix, resolving the finding for a file that still carries the
    wrong name. The prefix itself is decided by ``_planned_prefix``; this only
    moves.
    """
    try:
        basename = os.path.splitext(filename)[0]
        ext = os.path.splitext(filename)[1]
        if new_basename == basename:
            return None, None

        new_filename = new_basename + ext
        parent_dir = os.path.dirname(file_path)
        new_path = os.path.join(parent_dir, new_filename)

        if not os.path.isfile(file_path):
            logger.error("Source file disappeared before rename: %s", file_path)
            return None, f'source file no longer on disk: {file_path}'

        if os.path.exists(new_path):
            logger.warning("Target path already exists, skipping rename: %s", new_path)
            return None, f'a different file already occupies {new_filename}'

        os.rename(file_path, new_path)
        logger.info("Renamed: %s → %s", filename, new_filename)

        # Rename associated .lrc file if it exists
        lrc_path = os.path.join(parent_dir, basename + '.lrc')
        if os.path.isfile(lrc_path):
            new_lrc_path = os.path.join(parent_dir, new_basename + '.lrc')
            if not os.path.exists(new_lrc_path):
                os.rename(lrc_path, new_lrc_path)
                logger.info("Renamed LRC: %s.lrc → %s.lrc", basename, new_basename)

        return new_path, None
    except Exception as e:
        logger.error("Error renaming %s: %s", file_path, e, exc_info=True)
        return None, str(e)


def _rename_to_basename(file_path: str, filename: str, new_basename: str) -> Optional[str]:
    """Backwards-compatible wrapper: the new path, or None for anything else.

    Callers that must tell "already correct" from "refused" use
    :func:`rename_to_basename_result` instead (iss29-E08).
    """
    new_path, _error = rename_to_basename_result(file_path, filename, new_basename)
    return new_path


def _update_db_file_path(db, old_path: str, new_path: str):
    """Follow a renamed file in ``lib2_track_files``.

    The path is the file row's identity, so a rename on disk that is not recorded
    here leaves the catalogue pointing at a file that no longer exists.
    """
    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE lib2_track_files SET path = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE path = ?",
            (new_path, old_path)
        )
        if cursor.rowcount > 0:
            conn.commit()
            logger.debug("Updated DB file_path: %s → %s", old_path, new_path)
        else:
            conn.commit()
    except Exception as e:
        logger.debug("Error updating DB file_path: %s", e)
    finally:
        if conn:
            conn.close()


def _album_row_for_files(conn, file_track_data) -> Any:
    """The lib2 album a folder's files belong to, or None.

    Tries every file path exactly, then falls back to a suffix match on the first
    one. The fallback is not cosmetic: a library indexed on one machine and scanned
    from another has the same files under different absolute prefixes
    (``/mnt/musicBackup/...`` vs ``H:\\Music\\...``), and without it the folder
    scan silently loses every enrichment the catalogue could have contributed.
    """
    sql = """
        SELECT al.id, al.title, al.image_url, al.external_ids, al.spotify_id,
               al.musicbrainz_id, ar.id AS artist_id, ar.name AS artist_name,
               ar.image_url AS artist_image
          FROM lib2_track_files f
          JOIN lib2_tracks t ON t.id = f.track_id
          JOIN lib2_albums al ON al.id = t.album_id
          LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
         WHERE f.path %s
         LIMIT 1
    """
    for fpath, *_rest in file_track_data:
        row = conn.execute(sql % "= ?", (fpath,)).fetchone()
        if row:
            return row
    if file_track_data:
        parts = str(file_track_data[0][0]).replace('\\', '/').split('/')
        if len(parts) >= 2:
            suffix = '/'.join(parts[-2:])
            row = conn.execute(sql % "LIKE ?", (f'%{suffix}',)).fetchone()
            if row:
                return row
    return None


def _lookup_canonical_from_db(file_track_data: List[Tuple[str, str, Any, Any]],
                              context: JobContext) -> Optional[Tuple[str, str]]:
    """Return the album's pinned canonical ``(source, album_id)`` or None.

    #765: when the album this folder's files belong to has a canonical release
    pinned (best-fit to the files), Track Number Repair uses it first so it agrees
    with the Reorganizer.

    lib2 records that pin as the album's DEFAULT release edition rather than a
    ``canonical_source``/``canonical_album_id`` column pair — same idea, one level
    down: the release group is the album, the edition is the concrete release the
    files were matched to. Its provider id is what the tracklist fetch needs.
    """
    if not context.db:
        return None
    conn = None
    try:
        conn = context.db._get_connection()
        album = _album_row_for_files(conn, file_track_data)
        if album is None:
            return None
        edition = conn.execute(
            "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_release_editions "
            "WHERE release_group_id = ? AND is_default = 1 LIMIT 1",
            (album["id"],),
        ).fetchone()
        if edition is None:
            return None
        ids = parse_external_ids(edition["external_ids"])
        if edition["spotify_id"]:
            ids["spotify"] = str(edition["spotify_id"])
        if edition["musicbrainz_id"]:
            ids["musicbrainz"] = str(edition["musicbrainz_id"])
        for source, _column in _SOURCE_ALBUM_ID_COLUMNS:
            if ids.get(source):
                return (source, str(ids[source]))
        if ids.get("musicbrainz"):
            return ("musicbrainz", str(ids["musicbrainz"]))
    except Exception as e:
        logger.debug("Error looking up canonical from DB: %s", e)
    finally:
        if conn:
            conn.close()
    return None


def _lookup_album_ids_from_db(file_track_data: List[Tuple[str, str, Any, Any]],
                              context: JobContext) -> Dict[str, Optional[str]]:
    """Provider album ids the catalogue already knows for this folder.

    Saves the expensive tag reads and API calls when lib2 can answer. Reads both
    places lib2 keeps ids: the promoted Spotify/MusicBrainz columns and
    ``external_ids``.
    """
    if not context.db:
        return {}
    conn = None
    try:
        conn = context.db._get_connection()
        album = _album_row_for_files(conn, file_track_data)
        if album is None:
            return {}
        ids = parse_external_ids(album["external_ids"])
        if album["spotify_id"]:
            ids["spotify"] = str(album["spotify_id"])
        if album["musicbrainz_id"]:
            ids["musicbrainz"] = str(album["musicbrainz_id"])
        return {
            source: str(ids[source])
            for source, _column in _SOURCE_ALBUM_ID_COLUMNS
            if ids.get(source)
        }
    except Exception as e:
        logger.debug("Error looking up album IDs from DB: %s", e)
        return {}
    finally:
        if conn:
            conn.close()


def _lookup_album_artist_art(file_track_data: List[Tuple[str, str, Any, Any]],
                             context: JobContext) -> Dict[str, Optional[str]]:
    """Album/artist artwork and names for an enriched finding card."""
    result = {'album_thumb_url': None, 'artist_thumb_url': None,
              'album_title': None, 'artist_name': None, 'artist_id': None}
    if not context.db:
        return result
    conn = None
    try:
        conn = context.db._get_connection()
        album = _album_row_for_files(conn, file_track_data)
        if album is not None:
            result['album_thumb_url'] = album["image_url"] or None
            result['artist_thumb_url'] = album["artist_image"] or None
            result['album_title'] = album["title"] or None
            result['artist_name'] = album["artist_name"] or None
            result['artist_id'] = album["artist_id"]
    except Exception as e:
        logger.debug("Error looking up album/artist art from DB: %s", e)
    finally:
        if conn:
            conn.close()
    return result


def _check_single_track(file_path: str, filename: str, api_tracks: List[Dict],
                        title_similarity: float) -> Optional[Dict]:
    """Check if a track needs repair and return finding info (dry run mode).

    Returns a dict with 'description' and 'details' if repair is needed, else None.
    """
    plan = _plan_track_repair(file_path, filename, api_tracks, title_similarity)
    if not plan:
        return None

    disc_suffix = (f" (disc {plan['correct_disc']} of {plan['total_discs']})"
                   if plan['multi_disc'] else "")
    changes = []
    if plan['current_num'] != plan['correct_num']:
        changes.append(f"Track number: {plan['current_num']} -> {plan['correct_num']}{disc_suffix}")
    if not plan['tag_ok'] and plan['current_total'] != plan['disc_total']:
        changes.append(f"Total tracks: {plan['current_total']} -> {plan['disc_total']}"
                       f"{' (per disc)' if plan['multi_disc'] else ''}")
    if not plan['disc_ok']:
        changes.append(f"Disc: {plan['current_disc'] if plan['current_disc'] else 'none'}"
                       f" -> {plan['correct_disc']}/{plan['total_discs']}")
    if plan['new_basename']:
        changes.append(f"Filename: {filename} -> {plan['new_basename']}{os.path.splitext(filename)[1]}")

    matched_track = plan['matched_track']
    details = {
        'current_track_num': plan['current_num'],
        'correct_track_num': plan['correct_num'],
        'total_tracks': plan['disc_total'],
        'matched_title': matched_track.get('name', ''),
        'file_title': plan['file_title'] or filename,
        'changes': changes,
        'match_score': round(plan['match_score'], 3),
        # the approval-time fixer applies EXACTLY this plan — the tag skip and
        # the rename target ride in the finding so approve can never invent a
        # different (convention-mangling) rename than the one shown (#1009)
        'tag_ok': plan['tag_ok'],
        'disc_ok': plan['disc_ok'],
    }
    if plan['new_basename']:
        details['new_filename'] = plan['new_basename'] + os.path.splitext(filename)[1]
    if plan['multi_disc']:
        details['disc_number'] = plan['correct_disc']
        details['total_discs'] = plan['total_discs']
    return {
        'description': f'Matched to: "{matched_track.get("name", "?")}"\n' + '\n'.join(changes),
        'details': details,
    }


def _repair_single_track(file_path: str, filename: str, api_tracks: List[Dict],
                         title_similarity: float, context: JobContext) -> bool:
    """Match a single file to the API tracklist and fix its track number tag + filename.

    Returns True if the track was actually repaired.
    """
    plan = _plan_track_repair(file_path, filename, api_tracks, title_similarity)
    if not plan:
        return False

    if not plan['tag_ok']:
        _fix_track_number_tag(file_path, plan['correct_num'], plan['disc_total'])
    if not plan['disc_ok']:
        _fix_disc_number_tag(file_path, plan['correct_disc'], plan['total_discs'])

    final_path = file_path
    if plan['new_basename']:
        new_path = _rename_to_basename(file_path, filename, plan['new_basename'])
        if new_path and context.db:
            _update_db_file_path(context.db, file_path, new_path)
            final_path = new_path

    if context.report_change:
        context.report_change(
            finding_type='track_number_mismatch',
            action='fixed_track_number',
            entity_type='file',
            entity_id=None,
            file_path=final_path,
            details={'original_path': file_path},
        )

    return True


def _normalize_album_track_items(data) -> List[Dict[str, Any]]:
    """Normalize album track payloads to a list of dicts."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    items = data.get('items')
    if isinstance(items, list):
        return items
    tracks = data.get('tracks')
    if isinstance(tracks, list):
        return tracks
    if isinstance(tracks, dict):
        nested_items = tracks.get('items')
        if isinstance(nested_items, list):
            return nested_items
    return []


def _get_album_tracklist(source: str, album_id: str, cache: dict) -> Optional[List[Dict]]:
    """Fetch an album tracklist from a specific source, with per-scan caching.

    Returns a list of dicts with at least 'name' and 'track_number' keys,
    or None if lookup fails.
    """
    cache_key = f"{source}:{album_id}"
    if cache_key in cache:
        return cache[cache_key]

    result = None

    try:
        data = get_album_tracks_for_source(source, album_id)
        items = _normalize_album_track_items(data)
        if items:
            result = [
                {
                    'name': item.get('name', '') if isinstance(item, dict) else getattr(item, 'name', ''),
                    'track_number': item.get('track_number') if isinstance(item, dict) else getattr(item, 'track_number', None),
                    'disc_number': item.get('disc_number', 1) if isinstance(item, dict) else getattr(item, 'disc_number', 1),
                }
                for item in items
            ]
    except Exception as e:
        logger.debug("%s get_album_tracks failed for %s: %s", source.capitalize(), album_id, e)

    cache[cache_key] = result
    return result


def _get_tracklist_from_musicbrainz(mbid: str, context: JobContext,
                                     cache: dict) -> Optional[List[Dict]]:
    """Fetch an album tracklist from MusicBrainz release data.

    Returns a list of dicts with 'name' and 'track_number' keys,
    or None if lookup fails.
    """
    cache_key = f"mb_{mbid}"
    if cache_key in cache:
        return cache[cache_key]

    result = None
    mb = context.mb_client

    if mb:
        try:
            release = mb.get_release(mbid, includes=['recordings'])
            if release and 'media' in release:
                tracks = []
                for medium in release['media']:
                    medium_tracks = medium.get('tracks') or medium.get('track-list', [])
                    for track in medium_tracks:
                        name = track.get('title', '')
                        # MusicBrainz uses 'position' for track number within the medium
                        position = track.get('position') or track.get('number')
                        try:
                            position = int(position)
                        except (TypeError, ValueError):
                            position = None
                        tracks.append({
                            'name': name,
                            'track_number': position,
                            'disc_number': medium.get('position', 1),
                        })
                if tracks:
                    result = tracks
        except Exception as e:
            logger.debug("MusicBrainz get_release failed for %s: %s", mbid, e)

    cache[cache_key] = result
    return result


def _get_musicbrainz_id_via_audiodb(artist_name: str, album_name: str,
                                     context: JobContext) -> Optional[str]:
    """Search AudioDB for an album and extract its MusicBrainz release ID."""
    try:
        from core.audiodb_client import AudioDBClient
        client = AudioDBClient()
    except Exception:
        return None

    try:
        result = client.search_album(artist_name, album_name)
        if result:
            mb_id = result.get('strMusicBrainzAlbumID')
            if mb_id and mb_id.strip():
                logger.debug("AudioDB returned MusicBrainz ID %s for '%s - %s'",
                             mb_id, artist_name, album_name)
                return mb_id.strip()
    except Exception as e:
        logger.debug("AudioDB lookup failed for '%s - %s': %s", artist_name, album_name, e)
    return None


_CARRIED_SIDECAR_EXTS = ('.lrc',)


def _restore_sidecar(moved_audio: str, original_audio: str) -> None:
    """Undo the sidecar half of a renamed track.

    ``_rename_to_basename`` renames the ``.lrc`` alongside the audio, but the
    dd28-29 rollback only put the audio back — leaving the lyrics stranded under
    the new stem, where nothing looks for them, and the track showing no lyrics
    despite the file being right there. Best-effort: a rollback must never raise
    a second failure on top of the first.
    """
    moved_stem = os.path.splitext(moved_audio)[0]
    original_stem = os.path.splitext(original_audio)[0]
    for ext in _CARRIED_SIDECAR_EXTS:
        moved_sidecar = moved_stem + ext
        original_sidecar = original_stem + ext
        if not os.path.isfile(moved_sidecar) or os.path.exists(original_sidecar):
            continue
        try:
            os.replace(moved_sidecar, original_sidecar)
        except OSError as exc:  # noqa: BLE001
            logger.warning(
                "Could not roll back sidecar %s → %s: %s",
                moved_sidecar, original_sidecar, exc,
            )


def _edition_tracklists(
    conn: Any, album_id: int,
) -> tuple[Dict[int, list[Dict[str, Any]]], Dict[int, list[int]]]:
    """Per-edition tracklists for one release group, plus a track→editions map.

    dd28-18: ``lib2_albums`` is the release *group*; the concrete numbering of a
    given pressing lives in ``lib2_release_tracks``. Returns ``({}, {})`` when
    the album has no edition rows yet, so callers fall back to the group view
    (which is correct for a single-edition album).
    """
    rows = conn.execute(
        """SELECT rt.release_edition_id AS edition_id,
                  rt.track_id AS lib2_track_id,
                  COALESCE(rt.title_override, t.title) AS name,
                  rt.track_number,
                  COALESCE(rt.disc_number, 1) AS disc_number
             FROM lib2_release_tracks rt
             JOIN lib2_release_editions e ON e.id = rt.release_edition_id
             LEFT JOIN lib2_tracks t ON t.id = rt.track_id
            WHERE e.release_group_id = ? AND rt.track_id IS NOT NULL
            ORDER BY rt.release_edition_id, COALESCE(rt.disc_number, 1),
                     rt.track_number, rt.id""",
        (int(album_id),),
    ).fetchall()
    by_edition: Dict[int, list[Dict[str, Any]]] = {}
    of_track: Dict[int, list[int]] = {}
    for row in rows:
        entry = dict(row)
        edition_id = int(entry.pop("edition_id"))
        if entry.get("track_number") is None:
            continue
        by_edition.setdefault(edition_id, []).append(entry)
        of_track.setdefault(int(entry["lib2_track_id"]), []).append(edition_id)
    return by_edition, of_track


def _api_tracks_for_subject(
    subject: Dict[str, Any],
    group_tracks: list[Dict[str, Any]],
    editions: Dict[int, list[Dict[str, Any]]],
    edition_of_track: Dict[int, list[int]],
) -> list[Dict[str, Any]]:
    """The tracklist a single file must be judged against (dd28-18).

    With one edition (or none recorded) the release group's own list is the
    right answer and behaviour is unchanged. With several, the file is judged
    against the edition it actually belongs to — and if that cannot be
    determined unambiguously, against nothing at all: writing a plausible-
    looking wrong track number is worse than reporting no finding.
    """
    if len(editions) <= 1:
        return group_tracks
    try:
        track_id = int(subject["track_id"])
    except (KeyError, TypeError, ValueError):
        return []
    owning = edition_of_track.get(track_id) or []
    if len(owning) != 1:
        logger.debug(
            "Skipping track %s: it maps to %d editions of a multi-edition release",
            track_id, len(owning),
        )
        return []
    return editions.get(owning[0]) or []
