"""Track Number Repair Job — fixes embedded track number tags and filename prefixes.

Detects albums where 3+ files share the same track number (the "all tracks = 01"
bug pattern), then uses cascading API lookups in metadata-source priority order
before falling back to MusicBrainz and AudioDB to resolve the correct tracklist
and repair each file.
"""

import os
import re
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

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        anomaly_threshold = settings.get('anomaly_threshold', 3)
        title_similarity = settings.get('title_similarity', 0.80)
        dry_run = settings.get('dry_run', True)

        # Thread-local state to avoid race conditions with concurrent scan_folders()
        scan_state = {
            'album_tracks_cache': {},
            'title_similarity': title_similarity,
            'dry_run': dry_run,
        }

        transfer = context.transfer_folder
        if not os.path.isdir(transfer):
            logger.warning("Transfer folder does not exist: %s", transfer)
            return result

        # Collect album folders (directories containing audio files)
        album_folders: Dict[str, List[str]] = {}
        for root, _dirs, files in os.walk(transfer):
            if context.check_stop():
                return result
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in AUDIO_EXTENSIONS:
                    album_folders.setdefault(root, []).append(fname)

        total = sum(len(fnames) for fnames in album_folders.values())
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(
                phase=f'Scanning {len(album_folders)} album folders ({total} files)...',
                total=total
            )

        for folder_path, filenames in album_folders.items():
            if context.check_stop():
                return result
            if context.wait_if_paused():
                return result

            folder_name = os.path.basename(folder_path)
            if context.report_progress:
                context.report_progress(
                    scanned=result.scanned, total=total,
                    phase=f'Checking {result.scanned} / {total}',
                    log_line=f'Album: {folder_name} ({len(filenames)} tracks)',
                    log_type='info'
                )

            try:
                folder_result = self._repair_album(
                    folder_path, filenames, anomaly_threshold, context, scan_state
                )
                result.scanned += folder_result.scanned
                result.auto_fixed += folder_result.auto_fixed
                result.skipped += folder_result.skipped
                result.errors += folder_result.errors
                result.findings_created += folder_result.findings_created
                if folder_result.findings_created > 0 and context.report_progress:
                    context.report_progress(
                        log_line=f'Found {folder_result.findings_created} issues in {folder_name}',
                        log_type='skip'
                    )
            except Exception as e:
                logger.error("Error processing album folder %s: %s", folder_path, e, exc_info=True)
                result.errors += 1

            if context.update_progress:
                context.update_progress(result.scanned, total)

        return result

    def estimate_scope(self, context: JobContext) -> int:
        transfer = context.transfer_folder
        if not os.path.isdir(transfer):
            return 0
        count = 0
        for _root, _dirs, files in os.walk(transfer):
            for fname in files:
                if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                    count += 1
        return count

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

        # Step 0: Anomaly detection
        track_num_counts: Dict[int, int] = {}
        file_track_data: List[Tuple[str, str, Optional[int]]] = []

        for fname in filenames:
            fpath = os.path.join(folder_path, fname)
            try:
                audio = MutagenFile(fpath)
                if audio is None:
                    file_track_data.append((fpath, fname, None))
                    continue
                track_num, _ = _read_track_number_tag(audio)
                file_track_data.append((fpath, fname, track_num))
                if track_num is not None:
                    track_num_counts[track_num] = track_num_counts.get(track_num, 0) + 1
            except Exception:
                file_track_data.append((fpath, fname, None))

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

        for fpath, fname, _ in file_track_data:
            if context.check_stop():
                return result

            result.scanned += 1
            try:
                if dry_run:
                    finding = _check_single_track(fpath, fname, api_tracks, len(api_tracks), title_sim)
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
                            context.create_finding(
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
                            result.findings_created += 1
                else:
                    if _repair_single_track(fpath, fname, api_tracks, len(api_tracks), title_sim, context):
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

        # Fallback 0: Check DB first. If any tracked file already has source IDs,
        # prefer the configured source order and use the first available album ID.
        source_album_ids = _lookup_album_ids_from_db(file_track_data, context)

        # Collect available IDs from file tags (fallback when DB has no IDs)
        spotify_track_id = None
        mb_album_id = None
        album_name = None
        artist_name = None

        for fpath, fname, _ in file_track_data:
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


def _extract_track_number_from_filename(filename: str) -> Optional[int]:
    """Extract leading track number from filename like '01 - Song.flac'."""
    basename = os.path.splitext(filename)[0]
    match = re.match(r'^(\d{1,3})', basename.strip())
    if match:
        return int(match.group(1))
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


def _match_title_to_api_track(file_title: str, api_tracks: List[Dict],
                               threshold: float) -> Tuple[Optional[Dict], float]:
    """Fuzzy-match a file title to an API track. Returns (track, score)."""
    norm_file = _normalize_title(file_title)
    best_match = None
    best_score = 0.0

    for track in api_tracks:
        api_name = track.get('name', '')
        norm_api = _normalize_title(api_name)
        score = SequenceMatcher(None, norm_file, norm_api).ratio()
        if score > best_score:
            best_score = score
            best_match = track

    if best_score >= threshold:
        return best_match, best_score
    return None, best_score


def _normalize_title(title: str) -> str:
    """Normalize a title for comparison."""
    t = title.lower()
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'[^a-z0-9 ]', '', t)
    return t.strip()


def _fix_track_number_tag(file_path: str, correct_num: int, total: int):
    """Update ONLY the track number tag in the file."""
    from mutagen import File as MutagenFile
    from mutagen.id3 import TRCK, ID3
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4

    try:
        audio = MutagenFile(file_path)
        if audio is None:
            logger.error("Cannot re-open file for tag fix: %s", file_path)
            return

        track_str = f"{correct_num}/{total}"

        if isinstance(audio.tags, ID3):
            audio.tags.delall('TRCK')
            audio.tags.add(TRCK(encoding=3, text=[track_str]))
            audio.save(v1=0, v2_version=4)
        elif isinstance(audio, (FLAC, OggVorbis)):
            audio['tracknumber'] = [track_str]
            if isinstance(audio, FLAC):
                audio.save(deleteid3=True)
            else:
                audio.save()
        elif isinstance(audio, MP4):
            audio['trkn'] = [(correct_num, total)]
            audio.save()

        logger.info("Fixed track tag: %s → %s", os.path.basename(file_path), track_str)
    except Exception as e:
        logger.error("Error fixing track tag in %s: %s", file_path, e, exc_info=True)


def _fix_filename_track_number(file_path: str, filename: str, correct_num: int) -> Optional[str]:
    """Fix the track number prefix in a filename. Returns new path or None."""
    try:
        basename = os.path.splitext(filename)[0]
        ext = os.path.splitext(filename)[1]

        new_basename = re.sub(r'^\d{1,3}', f'{correct_num:02d}', basename)
        if new_basename == basename:
            return None

        new_filename = new_basename + ext
        parent_dir = os.path.dirname(file_path)
        new_path = os.path.join(parent_dir, new_filename)

        if not os.path.isfile(file_path):
            logger.error("Source file disappeared before rename: %s", file_path)
            return None

        if os.path.exists(new_path):
            logger.warning("Target path already exists, skipping rename: %s", new_path)
            return None

        os.rename(file_path, new_path)
        logger.info("Renamed: %s → %s", filename, new_filename)

        # Rename associated .lrc file if it exists
        lrc_path = os.path.join(parent_dir, basename + '.lrc')
        if os.path.isfile(lrc_path):
            new_lrc_path = os.path.join(parent_dir, new_basename + '.lrc')
            if not os.path.exists(new_lrc_path):
                os.rename(lrc_path, new_lrc_path)
                logger.info("Renamed LRC: %s.lrc → %s.lrc", basename, new_basename)

        return new_path
    except Exception as e:
        logger.error("Error renaming %s: %s", file_path, e, exc_info=True)
        return None


def _update_db_file_path(db, old_path: str, new_path: str):
    """Update file_path in tracks table if this track is tracked."""
    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tracks SET file_path = ?, updated_at = CURRENT_TIMESTAMP WHERE file_path = ?",
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


def _lookup_album_ids_from_db(file_track_data: List[Tuple[str, str, Any]],
                              context: JobContext) -> Dict[str, Optional[str]]:
    """Look up album IDs from the database using file paths.

    Checks if any of the files in this folder are tracked in the DB, and if so,
    returns a mapping of metadata source -> album ID.
    This avoids expensive file tag reads and API calls when the DB already knows.
    """
    if not context.db:
        return {}

    conn = None
    try:
        conn = context.db._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(albums)")
        album_columns = {row[1] for row in cursor.fetchall()}

        selected_sources = [
            (source, column)
            for source, column in _SOURCE_ALBUM_ID_COLUMNS
            if column in album_columns
        ]
        if not selected_sources:
            return {}

        # Try each file path until we find one tracked in the DB
        for fpath, _, _ in file_track_data:
            select_cols = ", ".join(f"al.{column}" for _source, column in selected_sources)
            cursor.execute(f"""
                SELECT {select_cols}
                FROM tracks t
                JOIN albums al ON al.id = t.album_id
                WHERE t.file_path = ?
                LIMIT 1
            """, (fpath,))
            row = cursor.fetchone()
            if row:
                return {
                    source: str(row[idx])
                    for idx, (source, _column) in enumerate(selected_sources)
                    if row[idx]
                }

    except Exception as e:
        logger.debug("Error looking up album IDs from DB: %s", e)
    finally:
        if conn:
            conn.close()

    return {}


def _lookup_album_artist_art(file_track_data: List[Tuple[str, str, Any]],
                             context: JobContext) -> Dict[str, Optional[str]]:
    """Look up album/artist thumb URLs and names from DB for enriched finding details.

    Uses suffix-based matching since DB paths may differ from local paths
    (e.g., /mnt/musicBackup/... vs H:\\Music\\...).
    """
    result = {'album_thumb_url': None, 'artist_thumb_url': None,
              'album_title': None, 'artist_name': None}
    if not context.db:
        return result

    conn = None
    try:
        conn = context.db._get_connection()
        cursor = conn.cursor()

        # First try exact path match (fast)
        for fpath, _, _ in file_track_data:
            cursor.execute("""
                SELECT al.thumb_url, ar.thumb_url, al.title, ar.name
                FROM tracks t
                LEFT JOIN albums al ON al.id = t.album_id
                LEFT JOIN artists ar ON ar.id = t.artist_id
                WHERE t.file_path = ?
                LIMIT 1
            """, (fpath,))
            row = cursor.fetchone()
            if row:
                result['album_thumb_url'] = row[0] or None
                result['artist_thumb_url'] = row[1] or None
                result['album_title'] = row[2] or None
                result['artist_name'] = row[3] or None
                return result

        # Fallback: suffix-based matching (handles cross-environment path mismatches)
        # Build suffix from the first file path (artist/album/filename)
        if file_track_data:
            fpath = file_track_data[0][0]
            parts = fpath.replace('\\', '/').split('/')
            # Try matching on last 2 components (album/filename) — most specific without artist
            if len(parts) >= 2:
                suffix = '/'.join(parts[-2:])
                # Use LIKE with the suffix for cross-platform matching
                cursor.execute("""
                    SELECT al.thumb_url, ar.thumb_url, al.title, ar.name
                    FROM tracks t
                    LEFT JOIN albums al ON al.id = t.album_id
                    LEFT JOIN artists ar ON ar.id = t.artist_id
                    WHERE t.file_path LIKE ?
                    LIMIT 1
                """, (f'%{suffix}',))
                row = cursor.fetchone()
                if row:
                    result['album_thumb_url'] = row[0] or None
                    result['artist_thumb_url'] = row[1] or None
                    result['album_title'] = row[2] or None
                    result['artist_name'] = row[3] or None

    except Exception as e:
        logger.debug("Error looking up album/artist art from DB: %s", e)
    finally:
        if conn:
            conn.close()

    return result


def _check_single_track(file_path: str, filename: str, api_tracks: List[Dict],
                        total_tracks: int, title_similarity: float) -> Optional[Dict]:
    """Check if a track needs repair and return finding info (dry run mode).

    Returns a dict with 'description' and 'details' if repair is needed, else None.
    """
    from mutagen import File as MutagenFile

    audio = MutagenFile(file_path)
    if audio is None:
        return None

    file_title = _read_title_tag(audio)
    matched_track = None
    match_score = 0.0

    if file_title:
        matched_track, match_score = _match_title_to_api_track(file_title, api_tracks, title_similarity)

    if not matched_track:
        basename = os.path.splitext(filename)[0]
        clean_name = re.sub(r'^\d{1,3}[\s.\-_]*', '', basename).strip()
        if clean_name:
            matched_track, match_score = _match_title_to_api_track(clean_name, api_tracks, title_similarity)

    if not matched_track:
        return None

    correct_num = matched_track.get('track_number')
    if correct_num is None:
        return None

    current_num, current_total = _read_track_number_tag(audio)
    if current_num == correct_num and current_total == total_tracks:
        return None  # Already correct

    changes = []
    if current_num != correct_num:
        changes.append(f'Track number: {current_num} -> {correct_num}')
    if current_total != total_tracks:
        changes.append(f'Total tracks: {current_total} -> {total_tracks}')

    # Check if filename would change
    basename_noext = os.path.splitext(filename)[0]
    new_basename = re.sub(r'^\d{1,3}', f'{correct_num:02d}', basename_noext)
    if new_basename != basename_noext:
        changes.append(f'Filename: {filename} -> {new_basename}{os.path.splitext(filename)[1]}')

    return {
        'description': f'Matched to: "{matched_track.get("name", "?")}"\n' + '\n'.join(changes),
        'details': {
            'current_track_num': current_num,
            'correct_track_num': correct_num,
            'total_tracks': total_tracks,
            'matched_title': matched_track.get('name', ''),
            'file_title': file_title or filename,
            'changes': changes,
            'match_score': round(match_score, 3),
        }
    }


def _repair_single_track(file_path: str, filename: str, api_tracks: List[Dict],
                         total_tracks: int, title_similarity: float,
                         context: JobContext) -> bool:
    """Match a single file to the API tracklist and fix its track number tag + filename.

    Returns True if the track was actually repaired.
    """
    from mutagen import File as MutagenFile

    audio = MutagenFile(file_path)
    if audio is None:
        return False

    # Try to match via embedded title tag first
    file_title = _read_title_tag(audio)
    matched_track = None

    if file_title:
        matched_track, _ = _match_title_to_api_track(file_title, api_tracks, title_similarity)

    # Fallback: match via filename (without track number prefix and extension)
    if not matched_track:
        basename = os.path.splitext(filename)[0]
        # Strip leading track number prefix like "01 - " or "01. "
        clean_name = re.sub(r'^\d{1,3}[\s.\-_]*', '', basename).strip()
        if clean_name:
            matched_track, _ = _match_title_to_api_track(clean_name, api_tracks, title_similarity)

    if not matched_track:
        return False

    correct_num = matched_track.get('track_number')
    if correct_num is None:
        return False

    # Check if track number already correct
    current_num, current_total = _read_track_number_tag(audio)
    if current_num == correct_num and current_total == total_tracks:
        return False  # Already correct

    # Fix the track number tag
    _fix_track_number_tag(file_path, correct_num, total_tracks)

    # Fix filename prefix if it starts with a track number
    new_path = _fix_filename_track_number(file_path, filename, correct_num)
    if new_path and context.db:
        _update_db_file_path(context.db, file_path, new_path)

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
